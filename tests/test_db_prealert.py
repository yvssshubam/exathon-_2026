"""Sending a pre-alert (DECISIONS D-53), against the real database. The night stops at 05:50, after Ravi's payment
into A and before his complaint: the pre-alert waits for the officer, who sends it; his complaint at 06:00 then
answers it. Resets the database and runs the demo night to 06:10.

    python -m pytest -m db tests/test_db_prealert.py
"""
from decimal import Decimal

import pytest

from tests.dbtools import connect_or_skip, d, one, q, ts

pytestmark = pytest.mark.db


@pytest.fixture(scope="module")
def db():
    from pipeline.night import run_night

    connect_or_skip().close()
    run_night(until="2026-10-18 05:50:00", quiet=True, amlworld=False)
    c = connect_or_skip()
    yield c
    c.close()


def waiting(db) -> list[tuple]:
    return q(db, "SELECT proposal_id, amount_inr, reasons_json FROM PH_X.PROPOSAL WHERE action_code = "
                 "'PROPOSE_PRE_ALERT' AND proposal_status = 'WAITING'")


def test_the_pre_alert_waits_with_where_a_is_on_hold(db):
    import json

    rows = waiting(db)
    assert len(rows) == 1 and d(rows[0][1]) == Decimal("40000")
    r = json.loads(rows[0][2])
    assert r["source_txn_id"] == "T_DEMO_10" and r["utr"] == "IMPS2026101805450021"
    held = {(h["case_id"], h["not_linked"]): d(h["amount_inr"]) for h in r["holds"]}
    assert held == {("CASE_DEMO", False): Decimal("50000"), ("CASE_DEMO", True): Decimal("40000")}
    assert [a["id"] for a in r["against"]] == ["C1", "C2"]


def test_the_checker_refuses_a_second_pre_alert_for_the_same_payment(db):
    import json

    from pipeline.checker import Checker
    from pipeline.recalc import propose

    m, c = connect_or_skip("maker", "BANK_X"), connect_or_skip("checker", "BANK_X")
    try:
        r = json.loads(waiting(db)[0][2])
        pid = propose(m, "CASE_DEMO", "ACC_A", "PROPOSE_PRE_ALERT", "OFFICER", "40000", "watch",
                      "2026-10-18 05:50:00", r, "again")
        Checker(c, "BANK_X").process(None, "2026-10-18 05:50:00", [pid])
    finally:
        m.close()
        c.close()
    status, note = q(db, "SELECT proposal_status, checker_note FROM PH_X.PROPOSAL WHERE proposal_id = {p!d}",
                     p=str(pid))[0]
    assert status == "REFUSED" and "already exists" in note


def test_the_officer_sends_it_and_nothing_more_is_held(db):
    from pipeline import officer

    before = d(one(db, "SELECT SUM(amount_inr) FROM PH_X.HOLD_LEDGER WHERE account_id = 'ACC_A' "
                       "AND hold_status = 'ACTIVE'"))
    pid = int(waiting(db)[0][0])
    c = connect_or_skip("checker", "BANK_X")
    try:
        officer.approve(c, "BANK_X", pid, "officer.test", now="2026-10-18 05:52:00")
    finally:
        c.close()
    entry = q(db, "SELECT entry_kind, account_id, txn_id, amount_inr, filed_by, detail FROM PH_X.PORTAL_ENTRY "
                  "WHERE proposal_id = {p!d}", p=str(pid))
    assert len(entry) == 1 and entry[0][:3] == ("PRE_ALERT", "ACC_A", "T_DEMO_10")
    assert d(entry[0][3]) == Decimal("40000") and entry[0][4] == "officer.test"
    assert "Where account ACC_A is on hold at Bank X:" in entry[0][5]
    assert d(one(db, "SELECT SUM(amount_inr) FROM PH_X.HOLD_LEDGER WHERE account_id = 'ACC_A' "
                     "AND hold_status = 'ACTIVE'")) == before
    assert one(db, "SELECT COUNT(*) FROM PH_X.WATCH_EVENT WHERE event_kind = 'PRE_ALERT_SENT'") == 1


def test_his_complaint_answers_the_sent_pre_alert(db):
    from pipeline.night import run_night

    run_night(until="2026-10-18 06:10:00", reset=False, quiet=True)
    assert one(db, "SELECT proposal_status FROM PH_X.PROPOSAL WHERE action_code = 'PROPOSE_PRE_ALERT' "
                   "AND proposal_status <> 'REFUSED'") == "APPROVED"
    ev = q(db, "SELECT event_at, sentence FROM PH_X.WATCH_EVENT WHERE event_kind = 'PRE_ALERT_ANSWERED'")
    assert len(ev) == 1 and ts(ev[0][0]) == "2026-10-18 06:03:00" and "15 minutes" in ev[0][1]