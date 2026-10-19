"""AT-02, AT-09, AT-10, AT-11 against Exasol: ticket check, officer rules, notices, legal clocks."""
from datetime import datetime, timedelta

import pytest

from pipeline.officer import DecisionError, approve, deadlines, grievance, reject
from pipeline.steps import attribute, recommend, recommendations, score, trace

pytestmark = pytest.mark.db
FMT = "%Y-%m-%d %H:%M:%S"


@pytest.fixture(scope="module")
def conn():
    from pipeline.db import connect

    c = connect()
    yield c
    c.close()


def run_as(conn, case_id, bank):
    trace(conn, case_id, bank)
    attribute(conn, case_id, bank)
    score(conn, case_id)
    recommend(conn, case_id, bank)


def due(conn, decision_id):
    rows = conn.execute("SELECT rule_name, anchor_at, due_at FROM PH.DEADLINE WHERE decision_id = {d}",
                        {"d": decision_id}).fetchall()
    return {r[0]: (datetime.strptime(str(r[1])[:19], FMT), datetime.strptime(str(r[2])[:19], FMT)) for r in rows}


def test_ticket_check(conn):                                                           # AT-02
    assert conn.execute("SELECT notice_status FROM PH.COMPLAINT_CHECK WHERE case_id = 'CASE_DEMO'").fetchval() \
        == "VALID"
    conn.execute("DELETE FROM PH.COMPLAINT WHERE case_id = 'CASE_NOAMT'")
    conn.execute(
        "INSERT INTO PH.COMPLAINT (case_id, ack_no, complaint_source, victim_account, victim_tag, utr, amount_inr, "
        "reported_at, lane, notice_legal_basis, notice_has_amount) VALUES ('CASE_NOAMT', '31025101899999', '1930', "
        "'ACC_LAKSHMI', 'V_TEST', 'IMPS2026101810020001', NULL, TIMESTAMP '2026-10-18 11:10:00', 'A', NULL, FALSE)")
    status, problems = conn.execute(
        "SELECT notice_status, problems FROM PH.COMPLAINT_CHECK WHERE case_id = 'CASE_NOAMT'").fetchone()
    conn.execute("DELETE FROM PH.COMPLAINT WHERE case_id = 'CASE_NOAMT'")
    assert status == "INCOMPLETE" and "no amount" in problems and "no legal basis" in problems


def test_officer_rules(conn):                                                         # AT-09
    run_as(conn, "CASE_DEMO", "BANK_X")
    with pytest.raises(DecisionError):
        approve(conn, "CASE_DEMO", "BANK_X", "ACC_C", officer="  ")
    with pytest.raises(DecisionError):
        reject(conn, "CASE_DEMO", "BANK_X", "ACC_A", officer="priya", reason="")
    with pytest.raises(DecisionError):
        approve(conn, "CASE_DEMO", "BANK_X", "EXIT:ATM:BY-ATM-0042", officer="priya")
    with pytest.raises(DecisionError):
        approve(conn, "CASE_DEMO", "BANK_X", "ACC_C", officer="priya", amount=50000)          # edit without reason
    with pytest.raises(DecisionError):
        approve(conn, "CASE_DEMO", "BANK_X", "ACC_C", officer="priya", amount=10**9, comment="too much")
    assert conn.execute("SELECT COUNT(*) FROM PH.DECISION WHERE decided_by IS NULL OR TRIM(decided_by) = ''"
                        ).fetchval() == 0


def test_approve_c_starts_lane_a_clock(conn):                                         # AT-11 (part)
    run_as(conn, "CASE_DEMO", "BANK_X")
    d = approve(conn, "CASE_DEMO", "BANK_X", "ACC_C", officer="priya")
    row = conn.execute("SELECT action_code, amount_inr, decided_by FROM PH.DECISION WHERE decision_id = {d}",
                       {"d": d}).fetchone()
    assert (row[0], float(row[1]), row[2]) == ("AMOUNT_HOLD", 80000.0, "priya")
    clocks = due(conn, d)
    anchor, due_at = clocks["trail_update_to_cfcfrms"]
    assert due_at - anchor == timedelta(days=7)


def test_murugans_notice_and_grievance_clocks(conn):                                  # AT-10, AT-11
    run_as(conn, "CASE_DEMO", "BANK_Z")
    d = approve(conn, "CASE_DEMO", "BANK_Z", "ACC_D", officer="kavitha")
    notices = dict(conn.execute("SELECT lang, notice_body FROM PH.NOTICE WHERE decision_id = {d}",
                                {"d": d}).fetchall())
    assert set(notices) == {"en", "hi", "ta"}
    for body in notices.values():
        assert "₹1,000" in body and "UPI629181021000001" in body and "VLR/CCPS/2026/1018-01" in body
        for secret in ("Lakshmi", "ACC_LAKSHMI", "V_LAKSHMI", "IMPS2026101810020001"):
            assert secret not in body
    received = "2026-10-20 11:00:00"
    grievance(conn, d, received)
    clocks = due(conn, d)
    start = datetime.strptime(received, FMT)
    assert clocks["forward_grievance"][1] - start == timedelta(days=7)
    assert clocks["officer_decision"][1] - start == timedelta(days=15)
    assert clocks["hold_removal"][1] - start == timedelta(days=90)
    assert clocks["intimate_police_before_removal"][1] - start == timedelta(days=75)
    run_as(conn, "CASE_DEMO", "BANK_X")


def test_lane_b_clock(conn):                                                          # AT-11, Lane B
    run_as(conn, "CASE_X07", "BANK_X")
    holdable = [r for r in recommendations(conn, "CASE_X07", "BANK_X")
                if r["action_code"] in ("AMOUNT_HOLD", "ACCOUNT_HOLD")]
    assert holdable, "CASE_X07 (lane B) should have something to hold at Bank X"
    rec = holdable[0]
    d = approve(conn, "CASE_X07", "BANK_X", rec["account_id"], officer="priya",
                comment="Last resort: mule signals documented" if rec["action_code"] == "ACCOUNT_HOLD" else None)
    clocks = due(conn, d)
    for name, days in (("customer_explanation", 20), ("bank_decision_no_explanation", 30),
                       ("max_hold_without_police", 60)):
        anchor, due_at = clocks[name]
        assert due_at - anchor == timedelta(days=days), name
    assert any(x["decision_id"] == d for x in deadlines(conn))