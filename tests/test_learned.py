"""The learned signal (DECISIONS D-51), no database: the fit, the scoring formula the SQL repeats, and the promise
that it is one more signal and never decides a band on its own."""
import math
import random

import pytest

from pipeline import learned
from pipeline.cases import DEMO_CASE, load_case
from pipeline.load_cases import CASE_FILES, rows_for
from tests.score_oracle import score, signals

TINY = {"intercept": -0.5, "dropped": {},
        "features": [{"name": "pass_through_ratio", "log": False, "mean": 0.3, "std": 0.2, "coef": 2.0},
                     {"name": "median_dwell_minutes", "log": True, "mean": 4.0, "std": 1.0, "coef": -1.0}]}


def fake_rows(n=2000, seed=7):
    """Flagged accounts forward money fast and in full; the others keep it."""
    rnd = random.Random(seed)
    rows = []
    for i in range(n):
        bad = i % 5 == 0
        rows.append({"account_id": f"AML_{i}", "label": int(bad),
                     "median_dwell_minutes": rnd.lognormvariate(2.5 if bad else 6.0, 1.0),
                     "pass_through_ratio": min(1.0, max(0.0, rnd.gauss(0.75 if bad else 0.25, 0.2))),
                     "large_payer_share": rnd.random(),
                     "cash_or_crypto_exits": 0.0,
                     "credit_vs_profile": rnd.lognormvariate(0, 1) if i % 7 else None,
                     # connection: flagged accounts pay back into their own circle more often
                     "distinct_payers": float(rnd.randint(1, 6)), "distinct_payees": float(rnd.randint(1, 6)),
                     "out_in_ratio": rnd.lognormvariate(0, 0.5), "payment_count": float(rnd.randint(2, 40)),
                     "reciprocal_share": min(1.0, max(0.0, rnd.gauss(0.5 if bad else 0.2, 0.2)))})
    return rows


def test_predict_is_the_formula_the_sql_repeats():
    x = {"pass_through_ratio": 0.7, "median_dwell_minutes": math.e ** 3 - 1}
    logit = -0.5 + 2.0 * (0.7 - 0.3) / 0.2 - 1.0 * (3.0 - 4.0) / 1.0
    assert learned.predict(x, TINY) == pytest.approx(1 / (1 + math.exp(-logit)))
    assert learned.predict({}, TINY) == pytest.approx(1 / (1 + math.exp(0.5)))      # missing counts as average
    huge = learned.predict({"pass_through_ratio": 1e9}, TINY)                         # clipped to +5 sd
    assert huge == pytest.approx(1 / (1 + math.exp(-(-0.5 + 2.0 * 5))))
    assert learned.predict(x, None) is None


def test_auc():
    assert learned.auc([0, 0, 1, 1], [0.1, 0.2, 0.8, 0.9]) == 1.0
    assert learned.auc([0, 0, 1, 1], [0.9, 0.8, 0.2, 0.1]) == 0.0
    assert learned.auc([0, 1, 0, 1], [0.5, 0.5, 0.5, 0.5]) == 0.5


def test_training_finds_the_pattern_and_drops_what_does_not_vary():
    model, metrics = learned.train_rows(fake_rows())
    coef = {f["name"]: f["coef"] for f in model["features"]}
    assert "cash_or_crypto_exits" in model["dropped"] and "cash_or_crypto_exits" not in coef
    assert coef["pass_through_ratio"] > 0 and coef["median_dwell_minutes"] < 0
    assert abs(coef["large_payer_share"]) < abs(coef["pass_through_ratio"]) / 5    # noise gets little weight
    assert metrics["auc"]["model"] > 0.9
    acc = metrics["accounts"]
    assert acc["train"] + acc["test"] == 2000 and 300 < acc["test"] < 500
    assert coef["reciprocal_share"] > 0                                          # a connection input that matters
    assert set(metrics["auc"]) == {"model", "hand_set_same_signals", "hand_set_plus_model", "model_signals_only",
                                   "boosted_trees_experiment"}
    assert metrics["auc"]["model"] >= metrics["auc"]["model_signals_only"]
    assert metrics["auc"]["boosted_trees_experiment"] > 0.9


def test_a_new_model_is_saved_only_if_it_helps():
    def m(model, hand):
        return {"auc": {"model": model, "hand_set_same_signals": hand}}

    assert learned.should_save(m(0.70, 0.54), None)[0]
    assert not learned.should_save(m(0.54, 0.54), None)[0]                     # no better than the hand-set weights
    assert not learned.should_save(m(0.64, 0.54), {"metrics": m(0.70, 0.54)})[0]   # worse than the saved model
    assert learned.should_save(m(0.75, 0.54), {"metrics": m(0.64, 0.54)})[0]
    assert learned.should_save(m(0.75, 0.54), {"features": []})[0]               # a saved file without metrics


def test_boosted_trees_are_fixed_and_learn():
    import numpy as np

    rnd = np.random.default_rng(3)
    z = rnd.normal(size=(600, 3))
    y = ((z[:, 0] > 0.3) & (z[:, 1] < 0)).astype(int)                       # an interaction a line cannot draw
    t1, t2 = learned.boost_fit(z, y, rounds=50), learned.boost_fit(z, y, rounds=50)
    assert t1 == t2
    assert learned.auc(y, learned.boost_predict(t1, z)) > 0.95


def test_held_out_split_is_fixed_by_the_account_id():
    ids = [f"AML_{i}" for i in range(1000)]
    assert [learned.held_out(a) for a in ids] == [learned.held_out(a) for a in ids]
    assert 150 < sum(learned.held_out(a) for a in ids) < 250


def test_save_load_and_the_rows_loaded_into_the_banks(tmp_path):
    model, metrics = learned.train_rows(fake_rows())
    path = tmp_path / "learned_signal.yaml"
    learned.save(model, metrics, "2026-10-17 23:59:00", path)
    back = learned.load(path)
    assert back["features"] == model["features"] and back["metrics"] == metrics
    rows = learned.coef_rows(back)
    assert rows[0][0] == "(intercept)" and len(rows) == len(model["features"]) + 1
    assert learned.config_row(back)[0][:2] == ("learned_pattern", learned.SIGNAL["weight"])
    assert learned.coef_rows(None) == [] and learned.config_row(None) == []
    text = learned.report(back)
    assert "Held-out test" in text and learned.CAVEAT in text
    assert learned.load(tmp_path / "missing.yaml") is None


def test_the_learned_sentences_pass_no_verdict():
    from pipeline.reasons import OVERSTATE, VERDICT

    for key in ("mule_sentence", "honest_sentence"):
        text = learned.SIGNAL[key]
        assert "{pct}" in text and not VERDICT.search(text) and not OVERSTATE.search(text)


def test_one_more_signal_never_decides_the_demo_band():
    """Even a model that rates every account 0% or 100% leaves every demo band as the eight signals set it."""
    demo = load_case(DEMO_CASE)
    rows = rows_for({k: load_case(v) for k, v in CASE_FILES.items()})
    never = {"intercept": -30.0, "features": []}
    always = {"intercept": 30.0, "features": []}
    for account in ("ACC_A", "ACC_B", "ACC_C", "ACC_D", "ACC_E", "ACC_G"):
        v = signals(rows, demo, account, demo.tool_as_of)
        base = score(v, {})[1]
        assert score(v, never)[1] == base == score(v, always)[1], account