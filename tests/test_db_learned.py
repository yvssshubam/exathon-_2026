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
# The same inputs as boosted trees: no coefficients, two trees that split on a signal and on connection inputs.
TREES = {"kind": "trees", "intercept": 0.0, "rate": 0.5, "dropped": {},
         "features": [{**f, "coef": 0.0} for f in TINY["features"]],
         "trees": [{"root_f": "pass_through_ratio", "root_t": 0.25, "left_f": "distinct_payers", "left_t": -0.1,
                    "ll": -1.0, "lr": -0.4, "right_f": "out_in_ratio", "right_t": 0.6, "rl": 0.3, "rr": 1.2},
                   {"root_f": "reciprocal_share", "root_t": 1e9, "left_f": "median_dwell_minutes", "left_t": 0.05,
                    "ll": 0.8, "lr": -0.6, "right_f": "pass_through_ratio", "right_t": 1e9, "rl": 0.0, "rr": 0.0}]}


@pytest.fixture(scope="module", params=[TINY, TREES], ids=["logistic", "trees"])
def model(request):
    return request.param


@pytest.fixture(scope="module")
def maker(model):
    from pipeline import portal, steps
    from pipeline.db import schema_for
    from pipeline.load_cases import reset

    admin = connect_or_skip()
    reset(admin, amlworld=False)
    s = schema_for(BANK)
    admin.execute(f"TRUNCATE TABLE {s}.LEARNED_COEF")
    admin.import_from_iterable(learned.coef_rows(model), (s, "LEARNED_COEF"))
    admin.execute(f"TRUNCATE TABLE {s}.LEARNED_TREE")
    if learned.tree_rows(model):
        admin.import_from_iterable(learned.tree_rows(model), (s, "LEARNED_TREE"))
    admin.execute(f"DELETE FROM {s}.SCORING_CONFIG WHERE signal_name = 'learned_pattern'")
    admin.import_from_iterable(learned.config_row(model), (s, "SCORING_CONFIG"))
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


def test_the_sql_rates_each_account_as_python_does(maker, model):
    from pipeline.load_cases import bank_rows, world_from_files
    from tests.score_oracle import signals

    world = world_from_files()
    rows = dict(bank_rows(world, BANK), COMPLAINTS=world["COMPLAINTS"])
    values = signal_values(maker)
    assert set(values) == {"ACC_A", "ACC_C", "ACC_D"}
    for acc, v in values.items():
        want = signals(rows, demo, acc, AS_OF)             # the connection inputs too, computed in Python
        assert v["learned_pattern"] == pytest.approx(learned.predict(want, model), abs=1e-6), acc


def test_the_score_is_the_oracles_with_one_more_signal(maker, model):
    from pipeline.load_cases import bank_rows, world_from_files
    from tests.score_oracle import score, signals

    world = world_from_files()
    rows = dict(bank_rows(world, BANK), COMPLAINTS=world["COMPLAINTS"])
    for acc, s, band in q(maker, "SELECT account_id, score, band FROM ACCOUNT_SCORE WHERE case_id = 'CASE_DEMO'"):
        want, want_band = score(signals(rows, demo, acc, AS_OF), model)
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
    linear, metrics = learned.train_rows(rows)
    assert linear["features"] and 0.0 <= metrics["auc"]["model"] <= 1.0
    trees, metrics = learned.train_rows(rows, "trees")
    assert trees["trees"] and metrics["auc"]["model"] == metrics["auc"]["boosted_trees_experiment"]


def test_the_model_s_inputs_are_kept_for_every_account(maker, model):
    n = len(model["features"])
    for acc, count in q(maker, "SELECT account_id, COUNT(*) FROM LEARNED_INPUT WHERE case_id = 'CASE_DEMO' "
                               "GROUP BY account_id"):
        assert int(count) == n, acc