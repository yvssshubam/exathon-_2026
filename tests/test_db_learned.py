"""The learned signal in Exasol (DECISIONS D-51): the scoring SQL rates an account exactly as pipeline/learned.predict
does, the score stays the oracle's, and training on AMLworld gives a model (skipped when PH_AML is not loaded).
Loads a small hand-written model into Bank X for the test and resets the database afterwards.

    python -m pytest -m db tests/test_db_learned.py
"""
import pytest

from pipeline import learned
from pipeline.cases import DEMO_CASE, load_case
from tests.dbtools import connect_or_skip, q

pytestmark = pytest.mark.db

demo = load_case(DEMO_CASE)
AS_OF = demo.tool_as_of
BANK = "BANK_X"
TINY = {"intercept": -0.5, "dropped": {},
        "features": [{"name": "pass_through_ratio", "log": False, "mean": 0.3, "std": 0.2, "coef": 2.0},
                     {"name": "median_dwell_minutes", "log": True, "mean": 4.0, "std": 1.0, "coef": -1.0},
                     {"name": "credit_vs_profile", "log": True, "mean": 0.5, "std": 0.4, "coef": 0.3},
                     {"name": "distinct_payers", "log": True, "mean": 1.5, "std": 0.8, "coef": -0.4},
                     {"name": "distinct_payees", "log": True, "mean": 1.2, "std": 0.7, "coef": 0.2},
                     {"name": "out_in_ratio", "log": True, "mean": 0.6, "std": 0.3, "coef": 0.7},
                     {"name": "payment_count", "log": True, "mean": 3.0, "std": 1.5, "coef": -0.1},
                     {"name": "reciprocal_share", "log": False, "mean": 0.2, "std": 0.2, "coef": 0.5}]}


@pytest.fixture(scope="module")
def maker():
    from pipeline import portal, steps
    from pipeline.db import schema_for
    from pipeline.load_cases import reset

    admin = connect_or_skip()
    reset(admin, amlworld=False)
    s = schema_for(BANK)
    admin.execute(f"TRUNCATE TABLE {s}.LEARNED_COEF")
    admin.import_from_iterable(learned.coef_rows(TINY), (s, "LEARNED_COEF"))
    admin.execute(f"DELETE FROM {s}.SCORING_CONFIG WHERE signal_name = 'learned_pattern'")
    admin.import_from_iterable(learned.config_row(TINY), (s, "SCORING_CONFIG"))
    p = connect_or_skip("portal")
    portal.deliver_one(p, "CASE_DEMO")
    p.close()
    m = connect_or_skip("maker", BANK)
    steps.intake(m, "CASE_DEMO", BANK, "2026-10-18 02:13:00")
    steps.trace(m, "CASE_DEMO", BANK, as_of=AS_OF)
    steps.attribute(m, "CASE_DEMO", BANK, as_of=AS_OF)
    steps.score(m, "CASE_DEMO", BANK, as_of=AS_OF)
    yield m
    m.close()
    reset(admin, amlworld=False)                   # back to the model on disk (or none)
    admin.close()


def signal_values(maker) -> dict[str, dict]:
    out: dict[str, dict] = {}
    for acc, name, value in q(maker, "SELECT account_id, signal_name, signal_value FROM ACCOUNT_SIGNALS "
                                     "WHERE case_id = 'CASE_DEMO'"):
        out.setdefault(acc, {})[name] = None if value is None else float(value)
    return out


def test_the_sql_rates_each_account_as_python_does(maker):
    from pipeline.load_cases import bank_rows, world_from_files
    from tests.score_oracle import signals

    world = world_from_files()
    rows = dict(bank_rows(world, BANK), COMPLAINTS=world["COMPLAINTS"])
    values = signal_values(maker)
    assert set(values) == {"ACC_A", "ACC_C", "ACC_D"}
    for acc, v in values.items():
        want = signals(rows, demo, acc, AS_OF)             # the connection inputs too, computed in Python
        assert v["learned_pattern"] == pytest.approx(learned.predict(want, TINY), abs=1e-6), acc


def test_the_score_is_the_oracles_with_one_more_signal(maker):
    from pipeline.load_cases import bank_rows, world_from_files
    from tests.score_oracle import score, signals

    world = world_from_files()
    rows = dict(bank_rows(world, BANK), COMPLAINTS=world["COMPLAINTS"])
    for acc, s, band in q(maker, "SELECT account_id, score, band FROM ACCOUNT_SCORE WHERE case_id = 'CASE_DEMO'"):
        want, want_band = score(signals(rows, demo, acc, AS_OF), TINY)
        assert band == want_band and float(s) == pytest.approx(want, abs=1e-6), acc
        assert band == score(signals(rows, demo, acc, AS_OF), {})[1], acc     # the eight still set the band


def test_every_signal_is_ranked_both_ways(maker):
    for acc, n, mule, honest in q(maker, "SELECT account_id, COUNT(*), COUNT(DISTINCT mule_rank), "
                                         "COUNT(DISTINCT honest_rank) FROM ACCOUNT_SIGNALS "
                                         "WHERE case_id = 'CASE_DEMO' GROUP BY account_id"):
        assert int(n) == 9 and int(mule) == 9 and int(honest) == 9, acc


def test_training_on_amlworld_gives_a_model():
    conn = connect_or_skip()
    try:
        if not conn.execute("SELECT COUNT(*) FROM EXA_ALL_TABLES WHERE TABLE_SCHEMA = 'PH_AML' "
                            "AND TABLE_NAME = 'TXN'").fetchval():
            pytest.skip("AMLworld is not loaded (run `make data`)")
        rows, as_of = learned.features(conn, max_txns=200, max_positives=300, negatives_per_positive=2)
    finally:
        conn.close()
    assert sum(int(r["label"]) for r in rows) > 10 and as_of.startswith("20")
    model, metrics = learned.train_rows(rows)
    assert model["features"] and 0.0 <= metrics["auc"]["model"] <= 1.0