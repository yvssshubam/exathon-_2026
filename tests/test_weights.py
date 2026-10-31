"""The learned weights of scoring v2 (DECISIONS D-58), no database: the trainer the UDF runs, the bounds, the gate
and the rule that only an approved version is ever loaded."""
import random

import pytest

from pipeline import scoring, weights
from pipeline.load_cases import weight_rows


def table(seed=3, n=900):
    rng = random.Random(seed)
    rows, labels = {}, {}
    names = list(scoring.CONFIG["pass_through"])
    for i in range(n):
        y = 1 if rng.random() < 0.25 else 0
        acc = f"AML_{i:04d}"
        rows[acc] = {s: min(1.0, max(0.0, rng.gauss(0.65 if y and k < 3 else 0.35, 0.25)))
                     for k, s in enumerate(names)}
        labels[acc] = y
    start = [scoring.CONFIG["pass_through"][s]["start_weight"] for s in names]
    return {"pass_through": {"signals": names, "start": start, "rows": rows, "labels": labels}}


def test_training_twice_gives_the_same_weights():
    tw = weights.trainer()
    assert tw.train(table()) == tw.train(table())


def test_every_weight_is_inside_its_bounds_and_each_score_sums_to_one():
    tw = weights.trainer()
    out = tw.train(table())
    w = [r["weight"] for r in out]
    assert all(0.05 - 1e-9 <= x <= 0.50 + 1e-9 for x in w) and sum(w) == pytest.approx(1.0)
    assert all(r["coefficient"] >= 0 for r in out)                      # a signal only pushes the way it means


def test_the_split_keeps_the_share_of_label_one_accounts():
    tw = weights.trainer()
    t = table()["pass_through"]
    train, test = tw.split(sorted(t["rows"]), t["labels"], 42, 0.30)
    share = lambda ids: sum(t["labels"][a] for a in ids) / len(ids)    # noqa: E731
    assert abs(share(train) - share(test)) < 0.02 and len(test) == pytest.approx(0.3 * len(t["rows"]), abs=2)


def test_auc_is_half_for_guessing_and_one_for_a_perfect_ranking():
    tw = weights.trainer()
    assert tw.auc([0, 1, 0, 1], [0.1, 0.9, 0.2, 0.8]) == 1.0
    assert tw.auc([0, 1], [0.5, 0.5]) == 0.5


def version(number=1, approved=None, **over):
    ws = []
    for score_name in weights.SCORES:
        for name, sig in scoring.CONFIG[score_name].items():
            ws.append({"score": score_name, "signal": name, "coefficient": 1.0,
                       "weight": over.get(name, sig["start_weight"]), "start_weight": sig["start_weight"]})
    return {"version": number, "approved_by": approved, "weights": ws,
            "test": {s: {"auc": 0.80, "start_auc": 0.79} for s in weights.SCORES}}


def test_the_gate_passes_the_starting_weights():
    assert weights.check(version(), {"versions": []}) == []


def test_a_version_that_moves_a_demo_account_is_refused():
    # all of pass-through on the payers' share: Murugan's pump and the mule swap places
    v = version(median_dwell_minutes=0.05, passed_on_within_hour=0.05, cashed_out_within_hour=0.05,
                balance_kept_ratio=0.35, top3_payer_share=0.50)
    v2 = version(money_from_flagged_share=0.05, direct_complaints_7d=0.45, trail_touches_90d=0.45,
                 pays_back_its_payers=0.05)
    problems = weights.check(v, {"versions": []}) + weights.check(v2, {"versions": []})
    assert any("demo accounts would change type" in p for p in problems)


def test_a_weight_out_of_bounds_or_an_auc_drop_is_refused():
    v = version(top3_payer_share=0.70)
    assert any("outside" in p for p in weights.check(v, {"versions": []}))
    worse = version()
    worse["test"]["fraud_exposure"] = {"auc": 0.70, "start_auc": 0.79}
    assert any("AUC" in p for p in weights.check(worse, {"versions": []}))


def test_a_version_without_an_approval_is_never_used(tmp_path, monkeypatch):
    from pipeline import load_cases

    path = tmp_path / "scoring_weights.yaml"
    weights.save_versions({"versions": [version(1)]}, path)
    monkeypatch.setattr(load_cases, "WEIGHTS", path)
    rows = weight_rows(scoring.CONFIG)
    assert {r[0] for r in rows} == {0} and all(r[-1] == "TRUE" for r in rows)
    weights.save_versions({"versions": [version(1, approved="Priya")]}, path)
    rows = weight_rows(scoring.CONFIG)
    live = {r[0] for r in rows if r[-1] == "TRUE"}
    assert live == {1} and {r[0] for r in rows} == {0, 1}