"""AT-03 against Exasol: the demo trail and the loop case. Run with `pytest -m db` after loading the cases."""
import pytest

from pipeline.steps import trace

pytestmark = pytest.mark.db


@pytest.fixture(scope="module")
def conn():
    from pipeline.db import connect

    c = connect()
    yield c
    c.close()


def edges(conn, case_id):
    rows = conn.execute(
        "SELECT to_account, hop, is_own_bank, is_exit, is_cycle, stmt_id FROM PH.TRACE_EDGE "
        "WHERE case_id = {c}", {"c": case_id}).fetchall()
    return {r[0]: r[1:] for r in rows}


def test_demo_trail_viewed_from_bank_x(conn):
    trace(conn, "CASE_DEMO", "BANK_X")
    e = edges(conn, "CASE_DEMO")
    # A, B, C, D and B's ATM exit; C's 12:30 cash-out happens after the 11:13 holds, so it is not there yet
    assert set(e) == {"ACC_A", "ACC_B", "ACC_C", "ACC_D", "EXIT:ATM:BY-ATM-0042"}
    assert {k: int(v[0]) for k, v in e.items()} == {
        "ACC_A": 1, "ACC_B": 2, "ACC_C": 2, "ACC_D": 3, "EXIT:ATM:BY-ATM-0042": 3}
    assert {k for k, v in e.items() if v[1]} == {"ACC_A", "ACC_C"}      # own bank = Bank X
    assert {k for k, v in e.items() if v[2]} == {"EXIT:ATM:BY-ATM-0042"}
    assert not any(v[3] for v in e.values())
    assert all(v[4] for v in e.values())                   # every row carries its statement id


def test_same_trail_viewed_from_murugans_bank(conn):
    trace(conn, "CASE_DEMO", "BANK_Z")
    e = edges(conn, "CASE_DEMO")
    assert {k for k, v in e.items() if v[1]} == {"ACC_D"}
    trace(conn, "CASE_DEMO", "BANK_X")                     # leave the demo in its usual state


def test_after_baseline_time_c_has_cashed_out(conn):
    trace(conn, "CASE_DEMO", "BANK_X", as_of="2026-10-18 13:05:00")
    assert "EXIT:ATM:BX-ATM-0117" in edges(conn, "CASE_DEMO")
    trace(conn, "CASE_DEMO", "BANK_X")


def test_loop_is_found_and_does_not_hang(conn):
    trace(conn, "CASE_LOOP", "BANK_X", as_of="2026-10-18 23:59:59")
    e = edges(conn, "CASE_LOOP")
    assert set(e) == {"ACC_LOOP_A", "ACC_LOOP_E", "EXIT:ATM:BX-ATM-0200"}
    assert any(v[3] for v in e.values()), "the E -> A transfer must be flagged as a loop"