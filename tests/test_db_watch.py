"""The watch against Exasol: after the 02:13 ticket's holds, the two withdrawals of the night are refused and
the officer is alerted (DECISIONS D-38). Needs `pytest -m db`."""
import pytest

pytestmark = pytest.mark.db
BANK = "BANK_X"


@pytest.fixture(scope="module")
def conn():
    from pipeline.agent import work_ticket
    from pipeline.db import connect
    from pipeline.watch import run_watch

    c = connect()
    for table in ("HOLD_LEDGER", "PORTAL_ENTRY", "DECISION"):
        c.execute(f"DELETE FROM PH.{table} WHERE case_id = 'CASE_DEMO'")
    work_ticket(c, "CASE_DEMO", BANK)
    run_watch(c, BANK)
    yield c
    c.close()


def night(conn):
    return conn.execute(
        "SELECT TO_CHAR(event_at, 'HH24:MI'), account_id, event_kind, amount_inr, response, chosen_by "
        "FROM PH.WATCH_EVENT WHERE viewing_bank_id = {b} AND account_id IN ('ACC_A', 'ACC_C', 'ACC_D') "
        "ORDER BY event_at", {"b": BANK}).fetchall()


def test_the_two_withdrawals_of_the_night_are_refused_and_alerted(conn):
    assert [(t, a, k, float(amt), r, by) for t, a, k, amt, r, by in night(conn)] == [
        ("04:30", "ACC_C", "BLOCKED", 29500.0, "ALERT", "pipeline"),
        ("05:10", "ACC_A", "BLOCKED", 50000.0, "ALERT", "pipeline"),
    ]


def test_the_watch_changes_no_money_on_the_demo_ticket(conn):
    assert conn.execute("SELECT COUNT(*) FROM PH.HOLD_LEDGER WHERE case_id = 'CASE_DEMO' "
                        "AND action_code = 'WATCH_HOLD'").fetchval() == 0


def test_running_the_watch_again_gives_the_same_events(conn):
    from pipeline.watch import run_watch

    before = night(conn)
    run_watch(conn, BANK)
    assert night(conn) == before