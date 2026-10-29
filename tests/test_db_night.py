"""The demo night's done-whens (Modules 1-8), against the real database. Resets and runs the night once.

The tests read the 9 a.m. state first. The last tests change it, in order: the officer decides, the night goes on to
10:30 for Bank Z's desk, and the investigating officer's notice of 21 Oct arrives. Run the file as a whole:

    python -m pytest -m db tests/test_db_night.py
"""
from decimal import Decimal

import pytest

from pipeline.cases import DEMO_CASE, load_case
from tests.dbtools import connect_or_skip, d, one, q, ts

pytestmark = pytest.mark.db

demo = load_case(DEMO_CASE)


@pytest.fixture(scope="module")
def night():
    from pipeline.night import run_night

    connect_or_skip().close()
    return run_night(until="2026-10-18 09:00:00", quiet=True, amlworld=False)


@pytest.fixture(scope="module")
def db(night):
    c = connect_or_skip()
    yield c
    c.close()


def held(db, schema: str, account: str) -> Decimal:
    return d(one(db, f"SELECT SUM(amount_inr) FROM {schema}.HOLD_LEDGER WHERE account_id = {{a}} "
                     "AND hold_kind = 'LIEN' AND hold_status = 'ACTIVE'", a=account))


# ------------------------------------------------------------------ 9 a.m. at Bank X

def test_three_decisions_wait_for_the_officer_at_nine(db):
    got = q(db, "SELECT case_id, account_id, action_code, amount_inr FROM PH_X.PROPOSAL "
                "WHERE proposal_status = 'WAITING' ORDER BY proposal_id")
    want = {(m["case_id"], m["account_id"], m["action_code"]) for m in demo.expected_morning}
    assert {(c, a, k) for c, a, k, _ in got} == want and len(got) == 3
    amounts = {(c, a): v for c, a, _, v in got}
    assert d(amounts[("CASE_DEMO", "ACC_D")]) == Decimal("500")
    assert d(amounts[("CASE_BADUTR", "ACC_K")]) == Decimal("25000")


def test_the_mandatory_holds_landed_three_minutes_after_the_ticket(db):
    rows = q(db, "SELECT account_id, amount_inr, placed_at, stmt_id FROM PH_X.HOLD_LEDGER "
                 "WHERE case_id = 'CASE_DEMO' AND action_code = 'MANDATORY_HOLD' ORDER BY account_id")
    assert {(a, d(v)) for a, v, _, _ in rows} == {("ACC_A", Decimal("50000")), ("ACC_C", Decimal("29500")),
                                                  ("ACC_D", Decimal("1000"))}
    assert {ts(p) for _, _, p, _ in rows} == {"2026-10-18 02:16:00"}
    assert all(s is not None for *_, s in rows)                     # every hold traces to an Exasol statement


def test_kyc_packets_and_filings_go_in_the_same_run(db):
    kinds = q(db, "SELECT entry_kind, account_id, filed_at FROM PH_X.PORTAL_ENTRY WHERE case_id = 'CASE_DEMO'")
    kyc = {a for k, a, _ in kinds if k == "KYC"}
    assert kyc == {"ACC_A", "ACC_C", "ACC_D"}
    assert {k for k, _, _ in kinds} >= {"KYC", "EXIT", "ONWARD"}
    first = {ts(f) for k, _, f in kinds if k in ("KYC", "EXIT", "ONWARD")}
    assert min(first) == "2026-10-18 02:16:00"
    assert {ts(f) for k, _, f in kinds if k == "KYC"} == {"2026-10-18 02:16:00"}   # not a week later (SOP 6.1(k))


def test_bank_y_works_the_onward_ticket_within_a_minute_and_holds_what_b_kept(db):
    filed = one(db, "SELECT MIN(filed_at) FROM PH_X.PORTAL_ENTRY WHERE case_id = 'CASE_DEMO' "
                    "AND entry_kind = 'ONWARD' AND account_id = 'ACC_B'")
    received, picked = q(db, "SELECT received_at, picked_up_at FROM PH_Y.TICKET WHERE case_id = 'CASE_DEMO-Y1'")[0]
    from datetime import datetime

    gap = datetime.fromisoformat(ts(received)) - datetime.fromisoformat(ts(filed))
    assert gap.total_seconds() <= 60 and ts(picked) == ts(received)
    assert held(db, "PH_Y", "ACC_B") == Decimal("40000")
    assert one(db, "SELECT COUNT(*) FROM PH_Y.PORTAL_ENTRY WHERE case_id = 'CASE_DEMO-Y1' AND entry_kind = 'ONWARD' "
                   "AND account_id = 'ACC_G'") == 1


def test_bank_z_has_not_opened_yet(db):
    assert q(db, "SELECT ticket_status FROM PH_Z.TICKET WHERE case_id = 'CASE_DEMO-Y1-Z1'") == [("QUEUED",)]


def test_the_near_match_holds_nothing_and_sends_back_once(db):
    k = q(db, "SELECT match_status, near_txn_id FROM PH_X.INTAKE_CHECK WHERE case_id = 'CASE_BADUTR'")
    assert k == [("NEAR_MATCH", "T_DEMO_12")]
    assert one(db, "SELECT COUNT(*) FROM PH_X.HOLD_LEDGER WHERE case_id = 'CASE_BADUTR'") == 0
    assert one(db, "SELECT COUNT(*) FROM PH_X.PORTAL_ENTRY WHERE case_id = 'CASE_BADUTR' "
                   "AND entry_kind = 'SEND_BACK'") == 1


def test_c_s_atm_try_at_4_30_is_refused_and_a_freeze_is_proposed(db):
    tries = q(db, "SELECT attempted_at, channel, amount_inr FROM PH_X.DECLINED_ATTEMPT WHERE account_id = 'ACC_C' "
                  "ORDER BY attempted_at")
    assert (ts(tries[0][0]), tries[0][1], d(tries[0][2])) == ("2026-10-18 04:30:00", "ATM", Decimal("29500"))
    p = q(db, "SELECT proposed_at, proposed_by FROM PH_X.PROPOSAL WHERE account_id = 'ACC_C' "
              "AND action_code = 'PROPOSE_FULL_FREEZE' AND proposal_status = 'WAITING'")
    assert [(ts(a), b) for a, b in p] == [("2026-10-18 04:30:00", "watch")]
    assert held(db, "PH_X", "ACC_C") == Decimal("29500")           # still a lien: freezing is the officer's call


def test_e_goes_on_the_watch_with_no_hold(db):
    assert one(db, "SELECT COUNT(*) FROM PH_X.WATCH_LIST WHERE account_id = 'ACC_E' AND left_at IS NULL") == 1
    assert one(db, "SELECT COUNT(*) FROM PH_X.HOLD_LEDGER WHERE account_id = 'ACC_E'") == 0
    assert one(db, "SELECT COUNT(*) FROM PH_X.DECLINED_ATTEMPT WHERE account_id = 'ACC_C' AND channel = 'UPI' "
                   "AND intended_account = 'ACC_E'") == 1


def test_a_s_transfer_to_bank_z_is_refused_and_recorded(db):
    rows = q(db, "SELECT attempted_at, amount_inr, intended_bank_id FROM PH_X.DECLINED_ATTEMPT "
                 "WHERE account_id = 'ACC_A'")
    assert [(ts(a), d(v), b) for a, v, b in rows] == [("2026-10-18 05:10:00", Decimal("50000"), "BANK_Z")]
    assert one(db, "SELECT COUNT(*) FROM PH_X.WATCH_EVENT WHERE account_id = 'ACC_A' "
                   "AND event_kind = 'RECIPIENT_OTHER_BANK'") == 1


def test_the_refused_transfer_never_arrives_at_bank_z(db):
    rows = q(db, "SELECT txn_ref, from_account, to_account, amount_inr, refused_by_bank FROM PH_Z.UNDELIVERED_CREDIT")
    assert [(r, f, t, d(v), b) for r, f, t, v, b in rows] == [("T_DEMO_07", "ACC_A", "ACC_F", Decimal("50000"),
                                                                "BANK_X")]
    assert one(db, "SELECT COUNT(*) FROM PH_Z.TXN_LIVE WHERE txn_id = 'T_DEMO_07'") == 0
    assert one(db, "SELECT COUNT(*) FROM PH_Z.TXN WHERE txn_id = 'T_DEMO_07'") == 1     # the bank's raw feed keeps it


def test_b_s_atm_try_at_bank_y_is_refused_there(db):
    rows = q(db, "SELECT attempted_at, channel FROM PH_Y.DECLINED_ATTEMPT WHERE account_id = 'ACC_B'")
    assert [(ts(a), c) for a, c in rows] == [("2026-10-18 07:00:00", "ATM")]


def test_murugan_s_7_30_sale_is_not_held_and_says_why(db):
    assert one(db, "SELECT COUNT(*) FROM PH_X.HOLD_LEDGER WHERE account_id = 'ACC_D' "
                   "AND source_txn_id = 'T_DEMO_11'") == 0
    s = q(db, "SELECT event_at, sentence FROM PH_X.WATCH_EVENT WHERE account_id = 'ACC_D' "
              "AND event_kind = 'NEW_MONEY_HONEST'")
    assert len(s) == 1 and ts(s[0][0]) == "2026-10-18 07:30:00" and "not held" in s[0][1]


def test_victim_two_s_money_is_caught_then_linked_and_the_total_held_never_changes(db):
    rows = q(db, "SELECT hold_tag, amount_inr, placed_at, hold_status FROM PH_X.HOLD_LEDGER "
                 "WHERE account_id = 'ACC_A' AND hold_kind = 'LIEN' ORDER BY hold_id")
    caught = [(ts(p), s) for t, v, p, s in rows if t == "NOT_LINKED" and d(v) == Decimal("40000")]
    assert caught == [("2026-10-18 05:45:00", "REPLACED")]
    linked = [ts(p) for t, v, p, s in rows if t == "V_RAVI" and s == "ACTIVE"]
    assert linked == ["2026-10-18 06:03:00"]                 # the 06:00 ticket, worked within 3 minutes
    assert held(db, "PH_X", "ACC_A") == Decimal("90000")
    assert one(db, "SELECT COUNT(*) FROM PH_X.WATCH_EVENT WHERE account_id = 'ACC_A' "
                   "AND event_kind = 'LINKED_TO_VICTIM'") == 1


def test_victim_two_was_flagged_before_he_reported_and_the_pre_alert_closed_unsent(db):
    """D-53: Ravi's 05:45 payment came from a new payer into A, already held: a pre-alert waited for the officer, with
    where A is on hold. His own complaint at 06:00 closed it unsent, 15 minutes after the flag."""
    rows = q(db, "SELECT proposed_at, proposal_status, amount_inr, draft_text FROM PH_X.PROPOSAL "
                 "WHERE account_id = 'ACC_A' AND action_code = 'PROPOSE_PRE_ALERT'")
    assert [(ts(t), st, d(v)) for t, st, v, _ in rows] == [("2026-10-18 05:45:00", "CANCELLED", Decimal("40000"))]
    text = rows[0][3]
    assert "Where account ACC_A is on hold at Bank X:" in text and "IMPS2026101805450021" in text
    assert "this payment, held under the same notice, not linked to a reported victim yet" in text
    kinds = q(db, "SELECT event_kind, event_at, sentence FROM PH_X.WATCH_EVENT WHERE account_id = 'ACC_A' "
                  "AND event_kind LIKE 'PRE_ALERT%' ORDER BY event_at, event_id")
    assert [(k, ts(t)) for k, t, _ in kinds] == [("PRE_ALERT_READY", "2026-10-18 05:45:00"),
                                                 ("PRE_ALERT_NOT_NEEDED", "2026-10-18 06:03:00")]
    assert "15 minutes earlier" in kinds[1][2]
    assert one(db, "SELECT COUNT(*) FROM PH_X.PORTAL_ENTRY WHERE entry_kind = 'PRE_ALERT'") == 0     # never sent
    log = q(db, "SELECT log_text FROM PH_X.MORNING_LOG ORDER BY log_id DESC LIMIT 1")[0][0]
    assert "flagged the payer as a likely unreported victim 15 minutes before the complaint" in log


def test_the_split_between_victims_is_versioned(db):
    rows = q(db, "SELECT version_no, victim_tag, victim_inr, superseded_at FROM PH_X.VICTIM_SPLIT "
                 "WHERE account_id = 'ACC_A' ORDER BY version_no, victim_tag")
    versions = sorted({int(v) for v, *_ in rows})
    assert len(versions) >= 2
    latest = {t: d(v) for n, t, v, s in rows if int(n) == versions[-1]}
    assert latest == {"V_LAKSHMI": Decimal("50000"), "V_RAVI": Decimal("40000")}
    assert all(s is not None for n, _, _, s in rows if int(n) < versions[-1])     # old versions kept, superseded


def test_the_morning_log_is_written_for_each_product_bank(db):
    for schema in ("PH_X", "PH_Y"):
        log = q(db, f"SELECT period_to, log_text, written_by FROM {schema}.MORNING_LOG ORDER BY log_id DESC")
        assert log and ts(log[0][0]) == "2026-10-18 09:00:00" and log[0][1]
        assert log[0][2] == "template"                             # the model is off in the tests
    assert q(db, "SELECT COUNT(*) FROM PH_Z.MORNING_LOG") == [(0,)]


def test_no_bank_ever_froze_anything_by_itself(db):
    for schema in ("PH_X", "PH_Y", "PH_Z"):
        assert one(db, f"SELECT COUNT(*) FROM {schema}.HOLD_LEDGER WHERE hold_kind NOT IN ('LIEN', 'MARKER')") == 0


def test_every_change_names_its_proposal_and_statement(db):
    for schema in ("PH_X", "PH_Y"):
        assert one(db, f"SELECT COUNT(*) FROM {schema}.HOLD_LEDGER WHERE stmt_id IS NULL") == 0
        assert one(db, f"SELECT COUNT(*) FROM {schema}.PORTAL_ENTRY WHERE stmt_id IS NULL OR proposal_id IS NULL") == 0


def test_the_audit_log_tells_the_night_in_simulated_time(db):
    first = one(db, "SELECT MIN(sim_at) FROM PH_X.AUDIT_LOG WHERE case_id = 'CASE_DEMO'")
    assert ts(first) == "2026-10-18 02:13:00"


def test_the_narrator_sees_every_bank(db, night):
    banks = {line["bank"] for line in night["lines"]}
    assert {"BANK_X", "BANK_Y"} <= banks and None in banks          # None: the portal


# ------------------------------------------------------------------ after nine: these change the state, in order

def test_the_officer_narrows_d_to_the_victim_s_500(db):
    from pipeline.checker import Checker

    pid = one(db, "SELECT proposal_id FROM PH_X.PROPOSAL WHERE account_id = 'ACC_D' "
                  "AND action_code = 'PROPOSE_NARROW' AND proposal_status = 'WAITING'")
    chk = connect_or_skip("checker", "BANK_X")
    try:
        Checker(chk, "BANK_X").apply_decision(int(pid), "APPROVED", "officer.test", "2026-10-18 09:10:00",
                                              comment="matches the trail")
    finally:
        chk.close()
    assert held(db, "PH_X", "ACC_D") == Decimal("500")
    dec = q(db, "SELECT decision_outcome, decided_by, statement_ids FROM PH_X.DECISION WHERE proposal_id = {p}",
            p=str(int(pid)))
    assert dec[0][:2] == ("APPROVED", "officer.test") and dec[0][2]


def test_a_decision_on_a_settled_proposal_is_refused(db):
    from pipeline.checker import Checker, DecisionError

    pid = one(db, "SELECT proposal_id FROM PH_X.PROPOSAL WHERE account_id = 'ACC_D' "
                  "AND action_code = 'PROPOSE_NARROW'")
    chk = connect_or_skip("checker", "BANK_X")
    try:
        with pytest.raises(DecisionError):
            Checker(chk, "BANK_X").apply_decision(int(pid), "APPROVED", "officer.test", "2026-10-18 09:11:00")
    finally:
        chk.close()


def test_bank_z_s_desk_finds_nothing_left_at_g(db):
    from pipeline.night import run_night

    run_night(until="2026-10-18 10:30:00", reset=False, quiet=True)
    status = one(db, "SELECT ticket_status FROM PH_Z.TICKET WHERE case_id = 'CASE_DEMO-Y1-Z1'")
    assert status not in ("QUEUED", "WORKING")
    assert held(db, "PH_Z", "ACC_G") == Decimal("0")                 # G cashed out at 03:40, before the desk opened
    picked = one(db, "SELECT picked_up_at FROM PH_Z.TICKET WHERE case_id = 'CASE_DEMO-Y1-Z1'")
    assert ts(picked) >= "2026-10-18 10:00:00"


def test_the_io_notice_is_linked_to_the_first_case_and_holds_nothing_twice(db):
    from pipeline import portal
    from pipeline.agent import work_ticket

    before = held(db, "PH_X", "ACC_A")
    p = connect_or_skip("portal")
    m, c = connect_or_skip("maker", "BANK_X"), connect_or_skip("checker", "BANK_X")
    try:
        portal.deliver_one(p, "CASE_DEMO_IO")
        r = work_ticket(m, c, "CASE_DEMO_IO", "BANK_X", now="2026-10-21 11:00:00")
    finally:
        for conn in (p, m, c):
            conn.close()
    assert r["status"] == "LINKED"
    assert q(db, "SELECT related, related_case_id FROM PH_X.INTAKE_CHECK WHERE case_id = 'CASE_DEMO_IO'") == [
        ("DUPLICATE", "CASE_DEMO")]
    assert one(db, "SELECT COUNT(*) FROM PH_X.CASE_LINK WHERE case_id = 'CASE_DEMO_IO' "
                   "AND linked_case_id = 'CASE_DEMO'") >= 1
    assert one(db, "SELECT COUNT(*) FROM PH_X.HOLD_LEDGER WHERE case_id = 'CASE_DEMO_IO' AND hold_kind = 'LIEN'") == 0
    assert held(db, "PH_X", "ACC_A") == before