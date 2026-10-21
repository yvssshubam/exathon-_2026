"""The 2:13 a.m. run against Exasol: the agent works the demo ticket, the checker passes, the required holds land,
the paperwork is filed, the judgment calls wait, the officer decides, and Compare shows today vs the agent.
Run with `pytest -m db` after `make db seed` (make check-db does all of it)."""
from decimal import Decimal

import pytest

from pipeline import officer
from pipeline.agent import work_ticket
from pipeline.cases import load_case, paise
from pipeline.load_cases import CASE_FILES, rows_for
from pipeline.today import compare, run_today
from tests.rules_oracle import actions as oracle_actions
from tests.rules_oracle import facts as oracle_facts

pytestmark = pytest.mark.db
demo = load_case()
BANK = "BANK_X"
CLEAN = ["HOLD_LEDGER", "PORTAL_ENTRY", "DECISION", "TODAY_OUTCOME", "TODAY_RAW"]


def reset(conn, case_id):
    for table in CLEAN:
        conn.execute(f"DELETE FROM PH.{table} WHERE case_id = {{c}}", {"c": case_id})
    conn.execute("UPDATE PH.TICKET SET ticket_status = 'QUEUED', run_id = NULL, picked_up_at = NULL "
                 "WHERE case_id = {c}", {"c": case_id})


@pytest.fixture(scope="module")
def conn():
    from pipeline.db import connect

    c = connect()
    reset(c, "CASE_DEMO")
    yield c
    c.close()


@pytest.fixture(scope="module")
def run(conn):
    return work_ticket(conn, "CASE_DEMO", BANK)


def q(conn, sql, **p):
    return conn.execute(sql, {"c": "CASE_DEMO", "b": BANK, **p}).fetchall()


def test_run_is_done_in_seconds(run):
    assert run["status"] == "DONE" and run["seconds"] < 60


def test_every_tool_ran_in_order_with_statement_ids(conn, run):
    steps = q(conn, "SELECT tool_name, chosen_by, stmt_ids FROM PH.AGENT_STEP WHERE run_id = {r} ORDER BY step_no",
              r=run["run_id"])
    assert [s[0] for s in steps] == [
        "read_ticket", "trace_money", "attribute_victim_share", "get_account_evidence", "allowed_actions", "checker",
        "place_mandatory_hold", "record_exit", "draft_onward_entry", "draft_kyc_packet", "escalate_to_officer",
        "write_case_summary"]
    assert all(s[2] for s in steps if s[0] != "write_case_summary"), steps


def test_every_check_passed(conn, run):
    rows = q(conn, "SELECT check_name, check_passed FROM PH.CHECK_RESULT WHERE case_id = {c} AND viewing_bank_id = {b}")
    assert len(rows) == 6 and all(p for _, p in rows), rows


def test_required_holds_landed_at_2_16(conn, run):
    rows = q(conn, "SELECT account_id, hold_kind, amount_inr, placed_at FROM PH.HOLD_LEDGER WHERE case_id = {c} "
                   "AND placed_by = 'agent' ORDER BY account_id")
    assert [(a, k, float(m)) for a, k, m, _ in rows] == [
        ("ACC_A", "LIEN", 50000.0), ("ACC_C", "LIEN", 29500.0), ("ACC_D", "LIEN", 1000.0)]
    assert {str(t)[:16] for *_, t in rows} == {"2026-10-18 02:16"}
    assert not q(conn, "SELECT 1 FROM PH.HOLD_LEDGER WHERE case_id = {c} AND placed_by = 'agent' "
                       "AND hold_kind = 'ACCOUNT_FREEZE'")


def test_paperwork_was_filed(conn, run):
    rows = q(conn, "SELECT entry_kind, account_id, bank_id, amount_inr, victim_amount_inr, entry_status "
                   "FROM PH.PORTAL_ENTRY WHERE case_id = {c} ORDER BY entry_kind, account_id")
    got = [(k, a, b, None if m is None else float(m), None if v is None else float(v), s) for k, a, b, m, v, s in rows]
    assert ("EXIT", "EXIT:ATM:BX-ATM-0042", None, 50000.0, 50000.0, "FILED") in got
    assert ("ONWARD", "ACC_B", "BANK_Y", 70000.0, 70000.0, "FILED") in got
    assert sorted(a for k, a, *_ in got if k == "KYC") == ["ACC_A", "ACC_C", "ACC_D"]
    assert all(s == "DRAFT" for k, *_, s in got if k == "KYC")


def test_actions_equal_the_rules_oracle(conn, run):
    rows = rows_for({k: load_case(v) for k, v in CASE_FILES.items()})
    want = {(a["account_id"], a["action_code"], a["amount"]) for a in oracle_actions(oracle_facts(demo, rows, BANK))
            if a["action_code"] != "NONE"}
    got = {(a, k, None if m is None else paise(Decimal(str(m)))) for a, k, m in q(
        conn, "SELECT account_id, action_code, amount_inr FROM PH.CASE_ACTION WHERE case_id = {c} "
              "AND viewing_bank_id = {b} AND action_code <> 'NONE'")}
    assert got == want


def test_three_decisions_wait(conn, run):
    rows = q(conn, "SELECT account_id, action_code, amount_inr FROM PH.CASE_ACTION WHERE case_id = {c} "
                   "AND viewing_bank_id = {b} AND action_status = 'WAITING' ORDER BY account_id, action_code")
    assert [(a, k) for a, k, _ in rows] == [("ACC_A", "PROPOSE_FULL_FREEZE"), ("ACC_C", "PROPOSE_FULL_FREEZE"),
                                            ("ACC_D", "PROPOSE_NARROW")]
    assert float(rows[2][2]) == 500.0


def test_summary_is_written(conn, run):
    text = conn.execute("SELECT summary_text FROM PH.AGENT_RUN WHERE run_id = {r}", {"r": run["run_id"]}).fetchval()
    assert text.startswith("At 02:13") and "Rs 1,000 on Murugan" in text


def test_working_it_again_never_doubles_a_hold(conn, run):
    conn.execute("UPDATE PH.TICKET SET ticket_status = 'QUEUED' WHERE case_id = 'CASE_DEMO'")
    work_ticket(conn, "CASE_DEMO", BANK)
    n = conn.execute("SELECT COUNT(*) FROM PH.HOLD_LEDGER WHERE case_id = 'CASE_DEMO' AND placed_by = 'agent'"
                     ).fetchval()
    assert n == 3


def test_compare_today_vs_agent(conn, run):
    run_today(conn, "CASE_DEMO", BANK)
    c = compare(conn, "CASE_DEMO", BANK)
    e = demo.expected_compare
    assert c["today"]["victim_held_inr"] == float(e["victim_money_held"]["today"])
    assert c["today"]["honest_locked_inr"] == float(e["honest_money_locked"]["today"])
    assert c["today"]["minutes_to_first_hold"] == e["minutes_ticket_to_first_hold"]["today"]
    assert c["tool"]["victim_held_inr"] == float(e["victim_money_held"]["tool"])
    assert c["tool"]["honest_locked_overnight_inr"] == float(e["honest_money_locked"]["tool_overnight"])
    assert c["tool"]["minutes_to_first_hold"] == e["minutes_ticket_to_first_hold"]["tool"]
    assert c["tool"]["onward_filed_at"] < c["today"]["onward_filed_at"]


def test_officer_rules(conn, run):
    with pytest.raises(officer.DecisionError, match="officer name"):
        officer.approve(conn, "CASE_DEMO", BANK, "ACC_D", "PROPOSE_NARROW", "  ")
    with pytest.raises(officer.DecisionError, match="reasons written down"):
        officer.approve(conn, "CASE_DEMO", BANK, "ACC_C", "PROPOSE_FULL_FREEZE", "priya")
    with pytest.raises(officer.DecisionError, match="written reason"):
        officer.reject(conn, "CASE_DEMO", BANK, "ACC_A", "PROPOSE_FULL_FREEZE", "priya", "")
    with pytest.raises(officer.DecisionError, match="at least the victims"):
        officer.approve(conn, "CASE_DEMO", BANK, "ACC_D", "PROPOSE_NARROW", "priya", "typo", amount=100)


def test_officer_narrows_murugan_and_honest_money_locked_drops_to_zero(conn, run):
    officer.approve(conn, "CASE_DEMO", BANK, "ACC_D", "PROPOSE_NARROW", "priya")
    active = conn.execute("SELECT amount_inr, placed_by FROM PH.HOLD_LEDGER WHERE case_id = 'CASE_DEMO' "
                          "AND account_id = 'ACC_D' AND hold_status = 'ACTIVE'").fetchall()
    assert [(float(a), b) for a, b in active] == [(500.0, "priya")]
    officer.approve(conn, "CASE_DEMO", BANK, "ACC_C", "PROPOSE_FULL_FREEZE", "priya",
                    "Two weeks old, forwards within minutes, cashes out: the pattern in the reasons")
    officer.reject(conn, "CASE_DEMO", BANK, "ACC_A", "PROPOSE_FULL_FREEZE", "priya", "Wait for the IO's direction")
    c = compare(conn, "CASE_DEMO", BANK)
    assert c["tool"]["honest_locked_now_inr"] == 0.0
    assert c["tool"]["victim_held_now_inr"] == 80000.0
    with pytest.raises(officer.DecisionError, match="already decided"):
        officer.approve(conn, "CASE_DEMO", BANK, "ACC_D", "PROPOSE_NARROW", "priya")


def test_incomplete_notice_is_sent_back(conn):
    conn.execute("INSERT INTO PH.COMPLAINT (case_id, ack_no, complaint_source, victim_account, victim_tag, utr, "
                 "amount_inr, reported_at, lane, notice_legal_basis, notice_has_amount) VALUES ('CASE_BAD', "
                 "'31025101800999', '1930', 'ACC_LAKSHMI', 'V_BAD', 'NO-SUCH-UTR', NULL, "
                 "TIMESTAMP '2026-10-18 03:00:00', 'A', NULL, FALSE)")
    conn.execute("INSERT INTO PH.TICKET (case_id, received_at, ticket_status) VALUES ('CASE_BAD', "
                 "TIMESTAMP '2026-10-18 03:00:00', 'QUEUED')")
    try:
        r = work_ticket(conn, "CASE_BAD", BANK)
        assert r["status"] == "SENT_BACK"
        assert conn.execute("SELECT COUNT(*) FROM PH.HOLD_LEDGER WHERE case_id = 'CASE_BAD'").fetchval() == 0
    finally:
        for t in ("COMPLAINT", "TICKET"):
            conn.execute(f"DELETE FROM PH.{t} WHERE case_id = 'CASE_BAD'")


def test_the_extra_tickets_all_pass_the_checker(conn):
    extra = load_case(CASE_FILES["extra_cases"])
    for c in extra.complaints:
        reset(conn, c["case_id"])
        assert work_ticket(conn, c["case_id"], BANK)["status"] == "DONE", c["case_id"]