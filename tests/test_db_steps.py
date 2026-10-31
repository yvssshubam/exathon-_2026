"""The analysis steps in Exasol give what the Python oracles give, as Bank X sees the world (Modules 1-3).

Runs CASE_DEMO's steps by hand as Bank X's maker at 02:16 (when the agent's holds land) on a freshly seeded
database, then compares Exasol's results with tests/oracle.py, tests/score_oracle.py and tests/rules_oracle.py on
the same rows. Resets the database first.

    python -m pytest -m db tests/test_db_steps.py
"""
from decimal import Decimal

import pytest

from pipeline.cases import DEMO_CASE, load_case
from tests.dbtools import connect_or_skip, q

pytestmark = pytest.mark.db

demo = load_case(DEMO_CASE)
AS_OF = demo.tool_as_of
BANK = "BANK_X"


@pytest.fixture(scope="module")
def maker():
    from pipeline import portal, steps
    from pipeline.load_cases import reset

    admin = connect_or_skip()
    reset(admin, amlworld=False)
    admin.close()
    p = connect_or_skip("portal")
    portal.deliver_one(p, "CASE_DEMO")
    p.close()
    m = connect_or_skip("maker", BANK)
    steps.intake(m, "CASE_DEMO", BANK, "2026-10-18 02:13:00")
    steps.trace(m, "CASE_DEMO", BANK, as_of=AS_OF)
    steps.attribute(m, "CASE_DEMO", BANK, as_of=AS_OF)
    steps.score(m, "CASE_DEMO", BANK, as_of=AS_OF)
    steps.actions(m, "CASE_DEMO", BANK)
    chk = connect_or_skip("checker", BANK)          # the checks are the checker's: the maker may not write them
    steps.check(chk, "CASE_DEMO", BANK)
    chk.close()
    yield m
    m.close()


@pytest.fixture(scope="module")
def world():
    from pipeline.load_cases import CASE_FILES, rows_for

    return rows_for({k: load_case(v) for k, v in CASE_FILES.items()})


def test_intake_matches_the_payment_exactly(maker):
    assert q(maker, "SELECT match_status, matched_txn_id, related FROM INTAKE_CHECK WHERE case_id = 'CASE_DEMO'") == [
        ("EXACT", "T_DEMO_01", "NEW")]


def test_the_trace_stops_at_the_bank_border(maker):
    got = dict(q(maker, "SELECT to_account, MIN(hop) FROM TRACE_EDGE WHERE case_id = 'CASE_DEMO' GROUP BY to_account"))
    assert {k: int(v) for k, v in got.items()} == {"ACC_A": 1, "ACC_B": 2, "ACC_C": 2, "ACC_D": 3,
                                                   "EXIT:ATM:BX-ATM-0042": 3}


def test_attribution_matches_the_oracle_as_bank_x_sees_it(maker):
    got = {a: Decimal(str(v)) for a, v in q(maker, "SELECT account_id, victim_amount_inr FROM ATTRIBUTION "
                                                    "WHERE case_id = 'CASE_DEMO' AND trace_rule = 'LIBR'")}
    want = {k: Decimal(str(v)) for k, v in demo.expected_libr_bank_x.items() if k != "total"}
    assert {k: v for k, v in got.items() if v} == want
    assert sum(got.values()) == Decimal(str(demo.expected_libr_bank_x["total"]))


def test_scores_match_the_oracle(maker, world):
    """Scoring v2 (D-57): the two scores and the type, as the Python reference computes them."""
    from pipeline.load_cases import bank_rows
    from tests.score_oracle import classify, signals

    rows = dict(bank_rows(world, BANK), COMPLAINTS=world["COMPLAINTS"],
                SUSPECT_REGISTRY=world["SUSPECT_REGISTRY"])
    got = {a: (float(pt), float(fe), pat) for a, pt, fe, pat in q(
        maker, "SELECT account_id, pass_through, fraud_exposure, account_pattern FROM ACCOUNT_SCORE "
               "WHERE case_id = 'CASE_DEMO'")}
    assert set(got) == {"ACC_A", "ACC_C", "ACC_D"}                    # Bank X scores only its own accounts
    for acc, (pt, fe, pattern) in got.items():
        want = classify(signals(rows, demo, acc, AS_OF))
        assert pattern == want["account_type"], acc
        assert pt == pytest.approx(want["pass_through"], abs=0.02), acc
        assert fe == pytest.approx(want["fraud_exposure"], abs=0.02), acc
    snaps = q(maker, "SELECT COUNT(*), MIN(weight_version) FROM SCORE_SNAPSHOT WHERE case_id = 'CASE_DEMO'")[0]
    assert int(snaps[0]) == 3 and int(snaps[1]) == 0                  # every scoring leaves a snapshot


def test_the_rules_engine_matches_the_oracle(maker, world):
    from tests.rules_oracle import actions, facts, rupees

    want = sorted((a["account_id"], a["action_code"], a["mode"], rupees(a["amount"]))
                  for a in actions(facts(demo, world, BANK)) if a["action_code"] != "NONE")
    got = sorted((a, c, m, None if v is None else Decimal(str(v))) for a, c, m, v in q(
        maker, "SELECT account_id, action_code, act_mode, amount_inr FROM CASE_ACTION "
               "WHERE case_id = 'CASE_DEMO' AND viewing_bank_id = 'BANK_X' AND action_code <> 'NONE'"))
    assert got == want


def test_every_check_passes_and_names_its_statement(maker):
    rows = q(maker, "SELECT check_name, check_passed, detail, stmt_id FROM CHECK_RESULT WHERE case_id = 'CASE_DEMO'")
    assert {r[0] for r in rows} == {"conservation", "holds_within_balance", "holds_within_trail",
                                    "holds_within_report", "automatic_allowed", "own_accounts_only",
                                    "never_full_respected", "referral_intact"}
    assert all(r[1] for r in rows), [r for r in rows if not r[1]]
    assert all(r[3] is not None for r in rows)


def test_every_analysis_row_carries_its_statement_id(maker):
    for table in ("INTAKE_CHECK", "TRACE_EDGE", "ATTRIBUTION", "ACCOUNT_SCORE", "CASE_ACTION"):
        n = q(maker, f"SELECT COUNT(*) FROM {table} WHERE case_id = 'CASE_DEMO' AND stmt_id IS NULL")[0][0]
        assert int(n) == 0, table