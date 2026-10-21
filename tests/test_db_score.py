"""AT-07 against Exasol: D reads HONEST, A, B and C read MULE, each with three reason sentences, and the SQL's
signal values match the Python reference (tests/score_oracle.py)."""
import pytest

from pipeline.cases import DEMO_CASE, load_case
from pipeline.load_cases import CASE_FILES, rows_for
from pipeline.steps import attribute, score, trace
from tests.score_oracle import signals

pytestmark = pytest.mark.db
demo = load_case(DEMO_CASE)


@pytest.fixture(scope="module")
def conn():
    from pipeline.db import connect

    c = connect()
    trace(c, "CASE_DEMO", "BANK_X")
    attribute(c, "CASE_DEMO", "BANK_X")
    score(c, "CASE_DEMO")
    yield c
    c.close()


@pytest.mark.parametrize("account,band", [("ACC_A", "MULE"), ("ACC_B", "MULE"), ("ACC_C", "MULE"),
                                          ("ACC_D", "HONEST")])
def test_bands(conn, account, band):
    got = conn.execute("SELECT band FROM PH.ACCOUNT_SCORE WHERE case_id = 'CASE_DEMO' AND account_id = {a}",
                       {"a": account}).fetchval()
    assert got == band


@pytest.mark.parametrize("account", ["ACC_A", "ACC_B", "ACC_C", "ACC_D"])
def test_eight_signals_and_three_reasons_each(conn, account):
    rows = conn.execute("SELECT sentence, mule_rank, honest_rank FROM PH.ACCOUNT_SIGNALS "
                        "WHERE case_id = 'CASE_DEMO' AND account_id = {a}", {"a": account}).fetchall()
    assert len(rows) == 8
    assert all(r[0] and "{" not in r[0] for r in rows)
    assert sorted(int(r[1]) for r in rows) == list(range(1, 9))


@pytest.mark.parametrize("account", ["ACC_A", "ACC_B", "ACC_C", "ACC_D"])
def test_signal_values_match_python_reference(conn, account):
    expected = signals(rows_for({k: load_case(v) for k, v in CASE_FILES.items()}), demo, account, demo.tool_as_of)
    got = dict(conn.execute("SELECT signal_name, signal_value FROM PH.ACCOUNT_SIGNALS "
                            "WHERE case_id = 'CASE_DEMO' AND account_id = {a}", {"a": account}).fetchall())
    for name, value in expected.items():
        if value is None:
            assert got[name] is None, name
        else:
            assert got[name] == pytest.approx(value, rel=1e-3, abs=1e-3), name