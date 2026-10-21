"""AT-04, AT-05, AT-06 and FR-04 against Exasol: the UDF's answers equal the section 9 table and the oracle.
Run with `pytest -m db` after `python -m pipeline.run_sql sql` and `python -m pipeline.load_cases`."""
from decimal import Decimal

import pytest

from pipeline.cases import DEMO_CASE, EXTRA_CASES, load_case, paise
from pipeline.steps import attribute, trace
from tests.helpers import run as oracle_run

pytestmark = pytest.mark.db
demo = load_case(DEMO_CASE)


@pytest.fixture(scope="module")
def conn():
    from pipeline.db import connect

    c = connect()
    yield c
    c.close()


def attribution(conn, case_id):
    rows = conn.execute(
        "SELECT trace_rule, account_id, victim_amount_inr, other_victims_inr FROM PH.ATTRIBUTION "
        "WHERE case_id = {c}", {"c": case_id}).fetchall()
    return {(r[0], r[1]): (paise(Decimal(str(r[2]))), paise(Decimal(str(r[3])))) for r in rows}


@pytest.fixture(scope="module")
def demo_rows(conn):
    trace(conn, "CASE_DEMO", "BANK_X")
    attribute(conn, "CASE_DEMO", "BANK_X")
    return attribution(conn, "CASE_DEMO")


@pytest.mark.parametrize("account", ["ACC_A", "ACC_B", "ACC_C", "ACC_D", "EXIT:ATM:BX-ATM-0042"])
def test_libr_equals_the_hand_checked_table(demo_rows, account):                    # AT-04
    assert demo_rows[("LIBR", account)][0] == paise(demo.expected_libr[account])


@pytest.mark.parametrize("rule", ["LIBR", "FIFO", "PRO_RATA"])
def test_every_rule_equals_the_oracle(demo_rows, rule):                              # AT-04
    o = oracle_run(demo, rule, demo.tool_as_of)
    for acc in ("ACC_A", "ACC_B", "ACC_C", "ACC_D", "EXIT:ATM:BX-ATM-0042"):
        assert demo_rows[(rule, acc)][0] == o.tainted(acc, "V_LAKSHMI"), (rule, acc)


@pytest.mark.parametrize("rule", ["LIBR", "FIFO", "PRO_RATA"])
def test_demo_conserves_to_the_paisa(demo_rows, rule):                               # AT-05 (demo)
    assert sum(v[0] for (r, _), v in demo_rows.items() if r == rule) == paise(200000)


def test_onward_share_from_bank_x_to_bank_y(conn, demo_rows):                       # FR-04
    refs = conn.execute(
        "SELECT account_id, amount_inr, victim_amount_inr FROM PH.REFERRAL_OUT "
        "WHERE case_id = 'CASE_DEMO' AND from_bank_id = 'BANK_X'").fetchall()
    assert [(r[0], float(r[1]), float(r[2])) for r in refs] == [("ACC_B", 70000.0, 70000.0)]


def test_all_20_extra_victims_conserved(conn):                                       # AT-05, AT-06
    extra = load_case(EXTRA_CASES)
    for c in extra.complaints:
        trace(conn, c["case_id"], "BANK_X")
        attribute(conn, c["case_id"], "BANK_X")
        rows = attribution(conn, c["case_id"])
        reported = conn.execute(
            "SELECT amount_inr FROM PH.COMPLAINT WHERE case_id = {c}", {"c": c["case_id"]}).fetchval()
        for rule in ("LIBR", "FIFO", "PRO_RATA"):
            held_and_exited = sum(v[0] for (r, _), v in rows.items() if r == rule)
            assert held_and_exited == paise(Decimal(str(reported))), (c["case_id"], rule)
    # victims 1-3 all paid mule ACC_M01: their money is mixed there (AT-06)
    m01 = attribution(conn, "CASE_X01").get(("LIBR", "ACC_M01"))
    assert m01 is not None and m01[1] > 0, "ACC_M01 should also hold other victims' money"