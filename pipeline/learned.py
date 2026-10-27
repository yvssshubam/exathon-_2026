"""The learned signal (DECISIONS D-51): one more signal next to the eight hand-set ones, never a replacement.

A logistic regression trained on AMLworld's labels: accounts that sent a payment AMLworld marks as laundering against
accounts that did not, described by the case-independent signals of sql/steps/060_score.sql and five connection
features (computed the same way in Exasol: sql/learn/010_features.sql for training, 060 at the bank). Plain numpy
(Newton's method with a small L2 penalty, classes balanced), so it adds no dependency and every number in the model
can be read and checked.

Kept out of the model on purpose: account age and declared profile (AMLworld has neither, so the import makes them
up: D-37), the complaint links and the balance retained (they belong to a ticket, not to the account's history), and
any signal that does not vary in the data (AMLworld records no withdrawals).

Writes config/learned_signal.yaml (the model; load_cases puts it in every bank's LEARNED_COEF, and the scoring SQL
scores it in Exasol) and docs/LEARNED_SIGNAL.md (the held-out test, next to the hand-set weights on the same
accounts). Only aids, never replaces: a new model is saved only if it ranks the held-out accounts better than the
hand-set weights and than the saved model; otherwise the saved one stays. Boosted trees are measured next to it as
an experiment and never deployed. With no model file the scoring runs on the eight signals alone, as before.

    python -m pipeline.learned              # train on PH_AML (needs `make data`), then `make seed`
    python -m pipeline.learned --replace    # save the new model even if it is not better than the saved one
    python -m pipeline.learned --show       # print the saved model's test results
"""
from __future__ import annotations

import argparse
import hashlib
import math
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

import yaml

from pipeline.db import ROOT

MODEL_FILE = ROOT / "config" / "learned_signal.yaml"
REPORT = ROOT / "docs" / "LEARNED_SIGNAL.md"
FEATURE_SQL = ROOT / "sql" / "learn" / "010_features.sql"
SCORING = yaml.safe_load((ROOT / "config" / "scoring.yaml").read_text(encoding="utf-8"))
SIGNAL = SCORING["learned_signal"]
INTERCEPT = "(intercept)"
CAVEAT = "Synthetic data (IBM AMLworld HI-Small); laundering labels, not cyber-fraud mule labels."
# (signal, log-transform). The order is the order of the coefficients. The first five are the hand-set signals the
# data really holds; the last five describe how the account is connected (fan-in, fan-out, money out against in,
# activity, paying back its own payers) and exist only as inputs to this model: the officer never sees them as
# signals of their own, and they never enter the score except through the one learned signal.
SIGNAL_FEATURES = [("median_dwell_minutes", True), ("pass_through_ratio", False), ("large_payer_share", False),
                   ("cash_or_crypto_exits", True), ("credit_vs_profile", True)]
CONNECTION_FEATURES = [("distinct_payers", True), ("distinct_payees", True), ("out_in_ratio", True),
                       ("payment_count", True), ("reciprocal_share", False)]
FEATURES = SIGNAL_FEATURES + CONNECTION_FEATURES
MIN_AUC_GAIN = 0.01        # a new model replaces the saved one only if it ranks the held-out accounts this much better
Z_LIMIT = 5.0              # standardised values are clipped to +-5, here and in the SQL
LOGIT_LIMIT = 30.0
L2 = 1e-3
TEST_SHARE = 5             # one account in five (by a hash of its ID) is held out
FMT = "%Y-%m-%d %H:%M:%S"


# ---------------------------------------------------------------- scoring one account (the SQL does the same)

def load(path: Path = MODEL_FILE) -> dict | None:
    return yaml.safe_load(path.read_text(encoding="utf-8")) if path.exists() else None


def _clip(x: float, limit: float) -> float:
    return max(-limit, min(limit, x))


def predict(values: dict, model: dict | None) -> float | None:
    """The model's 0-1 rating of one account from its signal values (a missing value counts as average)."""
    if not model:
        return None
    logit = model["intercept"]
    for f in model["features"]:
        x = values.get(f["name"])
        if x is None:
            continue
        x = math.log1p(max(float(x), 0.0)) if f["log"] else float(x)
        logit += f["coef"] * _clip((x - f["mean"]) / f["std"], Z_LIMIT)
    return 1.0 / (1.0 + math.exp(-_clip(logit, LOGIT_LIMIT)))


def coef_rows(model: dict | None) -> list[tuple]:
    """LEARNED_COEF rows: (feature_name, use_log, mean_value, std_value, coef); the intercept is a row of its own."""
    if not model:
        return []
    rows = [(INTERCEPT, "FALSE", 0.0, 1.0, model["intercept"])]
    return rows + [(f["name"], "TRUE" if f["log"] else "FALSE", f["mean"], f["std"], f["coef"])
                   for f in model["features"]]


def config_row(model: dict | None) -> list[tuple]:
    """The learned signal's SCORING_CONFIG row, only when there is a model to score it."""
    if not model:
        return []
    s = SIGNAL
    return [(s["name"], s["weight"], s["honest_at"], s["mule_at"], s["mule_sentence"], s["honest_sentence"])]


# ---------------------------------------------------------------- fitting and measuring (numpy)

def transform(rows: list[dict], names: list[tuple[str, bool]]):
    """Raw values -> a float matrix with NaN for missing, log1p where the feature says so."""
    import numpy as np

    out = np.full((len(rows), len(names)), np.nan)
    for i, r in enumerate(rows):
        for j, (name, use_log) in enumerate(names):
            v = r.get(name)
            if v is not None:
                out[i, j] = math.log1p(max(float(v), 0.0)) if use_log else float(v)
    return out


def standardise(x, mean, std):
    import numpy as np

    z = (x - mean) / std
    return np.clip(np.nan_to_num(z, nan=0.0), -Z_LIMIT, Z_LIMIT)


def fit(z, y, l2: float = L2, iterations: int = 50):
    """Class-balanced logistic regression by Newton's method; the intercept is not penalised. Returns (b0, w)."""
    import numpy as np

    n, k = z.shape
    x = np.hstack([np.ones((n, 1)), z])
    pos = y.sum()
    sw = np.where(y == 1, n / (2 * pos), n / (2 * (n - pos)))
    beta = np.zeros(k + 1)
    pen = np.full(k + 1, l2 * n)
    pen[0] = 0.0
    for _ in range(iterations):
        p = 1 / (1 + np.exp(-np.clip(x @ beta, -LOGIT_LIMIT, LOGIT_LIMIT)))
        grad = x.T @ (sw * (p - y)) + pen * beta
        hess = (x * (sw * p * (1 - p))[:, None]).T @ x + np.diag(pen)
        step = np.linalg.solve(hess, grad)
        beta -= step
        if np.max(np.abs(step)) < 1e-10:
            break
    return float(beta[0]), beta[1:]


def auc(y, s) -> float:
    """Area under the ROC curve (ties count half): the chance a random flagged account rates above a random other."""
    import numpy as np

    y, s = np.asarray(y), np.asarray(s, dtype=float)
    pos, neg = int((y == 1).sum()), int((y == 0).sum())
    if not pos or not neg:
        return float("nan")
    _, inverse, counts = np.unique(s, return_inverse=True, return_counts=True)
    ranks = (np.cumsum(counts) - (counts - 1) / 2)[inverse]
    return float((ranks[y == 1].sum() - pos * (pos + 1) / 2) / (pos * neg))


def hand_score(rows: list[dict], names: list[str]):
    """The hand-set formula of config/scoring.yaml on the same signals: sum(weight * m) / sum(weight)."""
    import numpy as np

    sig = SCORING["signals"]
    total = sum(sig[n]["weight"] for n in names)
    out = np.zeros(len(rows))
    for i, r in enumerate(rows):
        for n in names:
            v, s = r.get(n), sig[n]
            if v is not None:
                out[i] += s["weight"] * max(0.0, min(1.0, (v - s["honest_at"]) / (s["mule_at"] - s["honest_at"])))
    return out / total


def mule_likeness(p):
    import numpy as np

    return np.clip((p - SIGNAL["honest_at"]) / (SIGNAL["mule_at"] - SIGNAL["honest_at"]), 0, 1)


def held_out(account_id: str) -> bool:
    return int(hashlib.md5(account_id.encode()).hexdigest(), 16) % TEST_SHARE == 0


def logistic(train: list[dict], feats: list[tuple[str, bool]]) -> dict:
    """One logistic regression on these features (any that does not vary in the training accounts is left out)."""
    import numpy as np

    y = np.array([int(r["label"]) for r in train])
    if y.sum() < 10 or (len(y) - y.sum()) < 10:
        raise ValueError("too few flagged or other accounts to train on")
    raw = transform(train, feats)
    kept, dropped = [], {}
    for j, (name, use_log) in enumerate(feats):
        col = raw[:, j][~np.isnan(raw[:, j])]
        if len(col) < 10 or float(col.std()) < 1e-9:
            dropped[name] = "no variation in the data" if len(col) else "no values in the data"
        else:
            kept.append((j, name, use_log, float(col.mean()), float(col.std())))
    if not kept:
        raise ValueError("no signal varies in the data")
    idx = [k[0] for k in kept]
    mean = np.array([k[3] for k in kept])
    std = np.array([k[4] for k in kept])
    b0, w = fit(standardise(raw[:, idx], mean, std), y)
    return {
        "intercept": round(b0, 8),
        "features": [{"name": name, "log": use_log, "mean": round(m, 8), "std": round(s, 8), "coef": round(float(c), 8)}
                     for (_, name, use_log, m, s), c in zip(kept, w, strict=True)],
        "dropped": dropped,
    }


def zmatrix(rows: list[dict], model: dict):
    """The model's standardised, clipped inputs (missing = average), one row per account."""
    import numpy as np

    feats = [(f["name"], f["log"]) for f in model["features"]]
    mean = np.array([f["mean"] for f in model["features"]])
    std = np.array([f["std"] for f in model["features"]])
    return standardise(transform(rows, feats), mean, std)


# ---------------------------------------------------------------- the experiment: boosted trees (never deployed)

def _best_split(b, idx, g, h, lam: float, sizes: list[int]):
    import numpy as np

    G, H = g[idx].sum(), h[idx].sum()
    best = (0.0, None, None)
    for j, size in enumerate(sizes):
        if not size:
            continue
        gs = np.bincount(b[idx, j], weights=g[idx], minlength=size + 1)
        hs = np.bincount(b[idx, j], weights=h[idx], minlength=size + 1)
        gl, hl = np.cumsum(gs)[:size], np.cumsum(hs)[:size]
        gain = gl ** 2 / (hl + lam) + (G - gl) ** 2 / (H - hl + lam) - G ** 2 / (H + lam)
        gain[(hl < 1e-3) | (H - hl < 1e-3)] = -np.inf
        t = int(np.argmax(gain))
        if gain[t] > best[0]:
            best = (float(gain[t]), j, t)
    return best


def _grow(b, edges, idx, g, h, lam: float, depth: int):
    sizes = [len(e) for e in edges]
    gain, j, t = _best_split(b, idx, g, h, lam, sizes) if depth else (0.0, None, None)
    if j is None:
        return float(-g[idx].sum() / (h[idx].sum() + lam))
    left = idx[b[idx, j] <= t]
    right = idx[b[idx, j] > t]
    return {"f": j, "t": float(edges[j][t]), "l": _grow(b, edges, left, g, h, lam, depth - 1),
            "r": _grow(b, edges, right, g, h, lam, depth - 1)}


def _apply(node, z):
    import numpy as np

    if not isinstance(node, dict):
        return np.full(len(z), node)
    go = z[:, node["f"]] <= node["t"]
    out = np.empty(len(z))
    out[go] = _apply(node["l"], z[go])
    out[~go] = _apply(node["r"], z[~go])
    return out


def boost_fit(z, y, rounds: int = 200, rate: float = 0.1, depth: int = 2, bins: int = 32, lam: float = 1.0):
    """Gradient-boosted trees (logistic loss, classes balanced), fixed settings, no tuning. Returns the trees."""
    import numpy as np

    n, k = z.shape
    edges = [np.unique(np.quantile(z[:, j], np.linspace(0, 1, bins + 1)[1:-1])) for j in range(k)]
    b = np.column_stack([np.searchsorted(edges[j], z[:, j], side="left") for j in range(k)])
    pos = y.sum()
    sw = np.where(y == 1, n / (2 * pos), n / (2 * (n - pos)))
    f, trees, idx = np.zeros(n), [], np.arange(n)
    for _ in range(rounds):
        p = 1 / (1 + np.exp(-f))
        tree = _grow(b, edges, idx, sw * (p - y), sw * p * (1 - p), lam, depth)
        f += rate * _apply(tree, z)
        trees.append(tree)
    return trees


def boost_predict(trees, z, rate: float = 0.1):
    import numpy as np

    f = np.zeros(len(z))
    for tree in trees:
        f += rate * _apply(tree, z)
    return 1 / (1 + np.exp(-f))


# ---------------------------------------------------------------- training and the held-out test

def train_rows(rows: list[dict]) -> tuple[dict, dict]:
    """Fit on the training accounts, measure on the held-out ones. Returns (model, metrics).
    The model is the logistic regression on every feature; the rest of the table is there to compare it with."""
    import numpy as np

    train = [r for r in rows if not held_out(r["account_id"])]
    test = [r for r in rows if held_out(r["account_id"])]
    y_train = np.array([int(r["label"]) for r in train])
    y_test = np.array([int(r["label"]) for r in test])
    model = logistic(train, FEATURES)
    signals_only = logistic(train, SIGNAL_FEATURES)
    p_test = np.array([predict(r, model) for r in test])
    p_signals = np.array([predict(r, signals_only) for r in test])
    trees = boost_fit(zmatrix(train, model), y_train)
    p_trees = boost_predict(trees, zmatrix(test, model))
    names = [n for n, _ in SIGNAL_FEATURES if n not in signals_only["dropped"]]
    hand = hand_score(test, names)
    wl = SIGNAL["weight"]
    hand_w = sum(SCORING["signals"][n]["weight"] for n in names)
    combined = (hand * hand_w + wl * mule_likeness(p_test)) / (hand_w + wl)
    flagged = p_test >= 0.5
    tp = int((flagged & (y_test == 1)).sum())
    metrics = {
        "accounts": {"train": len(train), "test": len(test), "flagged_train": int(y_train.sum()),
                     "flagged_test": int(y_test.sum())},
        "auc": {"model": round(auc(y_test, p_test), 4), "hand_set_same_signals": round(auc(y_test, hand), 4),
                "hand_set_plus_model": round(auc(y_test, combined), 4),
                "model_signals_only": round(auc(y_test, p_signals), 4),
                "boosted_trees_experiment": round(auc(y_test, p_trees), 4)},
        "at_50": {"precision": round(tp / max(int(flagged.sum()), 1), 4),
                  "recall": round(tp / max(int(y_test.sum()), 1), 4),
                  "rated_50_or_more": int(flagged.sum())},
    }
    return model, metrics


def should_save(metrics: dict, saved: dict | None) -> tuple[bool, str]:
    """Only aids: a new model must rank the held-out accounts better than the hand-set weights and the saved model."""
    a = metrics["auc"]["model"]
    hand = metrics["auc"]["hand_set_same_signals"]
    if a < hand + MIN_AUC_GAIN:
        return False, f"it ranks no better than the hand-set weights ({a:.3f} against {hand:.3f})"
    before = ((saved or {}).get("metrics") or {}).get("auc", {}).get("model")
    if before is not None and a < before + MIN_AUC_GAIN:
        return False, f"it is not better than the saved model ({a:.3f} against {before:.3f})"
    return True, "better than the hand-set weights" + ("" if before is None else f" and the saved model ({before:.3f})")


# ---------------------------------------------------------------- the data (Exasol)

def features(conn, max_txns: int = 1000, max_positives: int = 20000, negatives_per_positive: int = 4) -> tuple:
    """Build PH_LEARN.FEATURES from PH_AML and return (rows, as_of)."""
    from pipeline.run_sql import split_statements

    last = conn.execute("SELECT MAX(ts) FROM PH_AML.TXN").fetchval()
    as_of = (datetime.strptime(str(last)[:19], FMT) + timedelta(minutes=1)).strftime(FMT)
    positives = int(conn.execute(
        "SELECT COUNT(DISTINCT from_account) FROM PH_AML.TXN WHERE is_laundering").fetchval() or 0)
    bands = SCORING["profile_band_expected_monthly_credit_inr"]
    params = {"as_of": as_of, "max_txns": str(max_txns), "max_positives": str(max_positives),
              "max_negatives": str(min(positives, max_positives) * negatives_per_positive),
              "low": str(bands["low"]), "medium": str(bands["medium"]), "high": str(bands["high"])}
    for stmt in split_statements(FEATURE_SQL.read_text(encoding="utf-8")):
        conn.execute(stmt, params)
    cols = ["account_id", "label"] + [f for f, _ in FEATURES]
    rows = [dict(zip(cols, r, strict=True)) for r in conn.execute(
        f"SELECT {', '.join(cols)} FROM PH_LEARN.FEATURES ORDER BY account_id").fetchall()]
    for r in rows:
        for f, _ in FEATURES:
            r[f] = None if r[f] is None else float(r[f])
    return rows, as_of


def save(model: dict, metrics: dict, as_of: str, path: Path = MODEL_FILE) -> None:
    body = {"data": CAVEAT, "trained_at": datetime.now().strftime(FMT), "features_as_of": as_of,
            "method": "logistic regression, classes balanced, L2 1e-3, standardised (log1p where marked), "
                      "clipped to +-5; held out: 1 account in 5 by a hash of its ID",
            **model, "metrics": metrics}
    head = ("# Written by `python -m pipeline.learned` (DECISIONS D-51). Do not edit by hand: retrain instead.\n"
            "# Loaded into every bank's LEARNED_COEF by `make seed`; scored in Exasol by sql/steps/060_score.sql.\n")
    path.write_text(head + yaml.safe_dump(body, sort_keys=False, allow_unicode=True), encoding="utf-8")


def report(m: dict) -> str:
    met = m["metrics"]
    a, acc, at = met["auc"], met["accounts"], met["at_50"]
    better = a["model"] - a["hand_set_same_signals"]
    lines = ["# The learned signal (generated by `python -m pipeline.learned`)", "", f"_{CAVEAT}_", "",
             "One more signal next to the eight hand-set ones (DECISIONS D-51), weight "
             f"{SIGNAL['weight']} of {1 + SIGNAL['weight']:.2f}: it can move a score a little, and the hand-set "
             "signals still decide the band.", "",
             f"Trained on {acc['train']} accounts ({acc['flagged_train']} flagged); tested on {acc['test']} held-out "
             f"accounts ({acc['flagged_test']} flagged). Features as of {m['features_as_of']}.", "",
             "## Held-out test", "", "| Score | AUC |", "| --- | ---: |",
             f"| **The model (deployed)** | **{a['model']:.3f}** |",
             f"| The hand-set weights on the signals AMLworld holds | {a['hand_set_same_signals']:.3f} |",
             f"| The hand-set weights plus the model at weight {SIGNAL['weight']} | {a['hand_set_plus_model']:.3f} |"]
    if "model_signals_only" in a:
        lines += [f"| The same model on those signals only, no connection features | {a['model_signals_only']:.3f} |",
                  f"| Boosted trees on every feature (experiment, not deployed) | "
                  f"{a['boosted_trees_experiment']:.3f} |"]
    lines += ["", "AUC: the chance that a random flagged account rates above a random other one (0.5 is a coin "
             "toss). " + ("The model ranks the held-out accounts better than the hand-set weights." if better > 0.005
                          else "The model does not rank them better than the hand-set weights."), "",
             "The boosted trees are measured to show what a more complex model would add; they are not scored at the "
             "banks, because one formula can be read and checked line by line.", "",
              f"At a rating of 50% or more: precision {at['precision']:.1%}, recall {at['recall']:.1%} "
              f"({at['rated_50_or_more']} accounts rated 50% or more).", "", "## The model", "",
              "| Input | Transform | Weight on the standardised value |", "| --- | --- | ---: |"]
    for f in m["features"]:
        lines.append(f"| {f['name']} | {'log(1 + x)' if f['log'] else 'none'} | {f['coef']:+.3f} |")
    lines.append(f"| (intercept) | | {m['intercept']:+.3f} |")
    if m.get("dropped"):
        lines += ["", "Left out: " + "; ".join(f"{k} ({v})" for k, v in m["dropped"].items()) + "."]
    lines += ["", "Also left out on purpose: account age and declared profile (AMLworld has neither; the import makes "
              "them up), the complaint links and the balance retained (they belong to a ticket), and the payment "
              "format (in AMLworld some formats go with laundering, which says nothing about UPI mule accounts).", "",
              "The connection inputs (distinct payers and payees, money out against in, number of payments, share "
              "of payees that also paid it) are inputs to this model only: they never enter the score on their own.",
              "",
              "A positive weight means a higher value looks more like the flagged accounts. The rating is not a "
              "probability that an account is a mule: the classes are balanced, and the labels are laundering in "
              "synthetic data.", "", f"_{CAVEAT}_", ""]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--show", action="store_true", help="print the saved model's test results")
    ap.add_argument("--max-txns", type=int, default=1000, help="leave out accounts with more payments in or out")
    ap.add_argument("--negatives", type=int, default=4, help="other accounts per flagged account")
    ap.add_argument("--replace", action="store_true", help="save the new model even if it is not better")
    args = ap.parse_args(argv)
    if args.show:
        m = load()
        if not m:
            print("no model yet: run `python -m pipeline.learned`", file=sys.stderr)
            return 1
        print(report(m))
        return 0
    from pipeline.db import connect

    conn = connect()
    try:
        if not conn.execute("SELECT COUNT(*) FROM EXA_ALL_TABLES WHERE TABLE_SCHEMA = 'PH_AML' "
                            "AND TABLE_NAME = 'TXN'").fetchval():
            print("AMLworld is not loaded: run `make data` first.", file=sys.stderr)
            return 1
        started = time.perf_counter()
        rows, as_of = features(conn, args.max_txns, negatives_per_positive=args.negatives)
        print(f"features for {len(rows)} accounts ({sum(int(r['label']) for r in rows)} flagged) in "
              f"{time.perf_counter() - started:.0f}s")
    finally:
        conn.close()
    model, metrics = train_rows(rows)
    a = metrics["auc"]
    print(f"held-out AUC: model {a['model']:.3f}, hand-set {a['hand_set_same_signals']:.3f}, hand-set plus model "
          f"{a['hand_set_plus_model']:.3f}, model without connection features {a['model_signals_only']:.3f}, "
          f"boosted trees (experiment) {a['boosted_trees_experiment']:.3f}")
    ok, why = should_save(metrics, load())
    if not (ok or args.replace):
        print(f"kept the saved model: the new one is not saved because {why}")
        return 0
    save(model, metrics, as_of)
    REPORT.write_text(report(load()), encoding="utf-8")
    print(f"saved: {why}. Wrote {MODEL_FILE.relative_to(ROOT)} and {REPORT.relative_to(ROOT)}; "
          "run `python -m pipeline.load_cases --verify` to load it")
    return 0


if __name__ == "__main__":
    sys.exit(main())