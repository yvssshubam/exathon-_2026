# Body of the Exasol Python 3 SET script TRAIN_SCORING_WEIGHTS (schema PH_LEARN; DECISIONS D-58).
# sql/learn/030_train_udf.sql wraps this file via --@include; pipeline/weights.py calls it once over the training rows
# (sql/learn/020_weight_rows.sql) and tests/test_weights.py runs it without Exasol.
#
# The model decides only how much each signal counts. People still set what each signal measures, its low and high
# points, the cut-offs, the ladder and every safety rule. For each score (pass-through, fraud exposure):
#   1. a logistic regression on the signals' m values (0 to 1), coefficients kept at zero or above, so a signal can
#      only push the way its meaning says (projected gradient descent with a small L2 penalty, classes balanced:
#      plain numpy, so the same rows always give the same weights)
#   2. weight = coefficient / sum of coefficients, then each weight kept between 0.05 and 0.50 and the rest rescaled
#      to sum to 1, repeated until stable: no signal silently drops out, and none takes over
#   3. measured on the 30% of accounts held back (split with a fixed seed, the same share of label-1 accounts in each
#      part): AUC and precision at the 0.6 cut-off, for the starting weights and the learned ones
#
# Input rows: account_id, label (0/1), score_name, signal_name, m_value, start_weight, seed, test_share, l2,
#             weight_min, weight_max  (one row per account, score and signal)
# Output rows: score_name, signal_name, coefficient, weight, start_weight, test_auc, test_precision, start_auc,
#              start_precision, train_accounts, test_accounts

import random

CUTOFF = 0.6


def split(accounts, labels, seed, test_share):
    """Stratified split by a fixed seed: the same share of label-1 accounts in the training and test parts."""
    rng = random.Random(seed)
    test = set()
    for wanted in (0, 1):
        group = sorted(a for a in accounts if labels[a] == wanted)
        rng.shuffle(group)
        test.update(group[: int(round(len(group) * test_share))])
    return [a for a in sorted(accounts) if a not in test], sorted(test)


def fit(x, y, l2=0.001, steps=4000, rate=0.5):
    """Logistic regression with coefficients >= 0 (intercept free), classes balanced. x: list of rows of m values."""
    import numpy as np

    x = np.asarray(x, dtype=float)
    y = np.asarray(y, dtype=float)
    n, k = x.shape
    pos = max(float(y.sum()), 1.0)
    neg = max(float(n - y.sum()), 1.0)
    weight = np.where(y == 1, n / (2 * pos), n / (2 * neg))
    coef = np.zeros(k)
    intercept = 0.0
    for _ in range(steps):
        z = np.clip(x @ coef + intercept, -30, 30)
        p = 1 / (1 + np.exp(-z))
        g = weight * (p - y) / n
        coef = np.maximum(coef - rate * (x.T @ g + 2 * l2 * coef), 0.0)
        intercept -= rate * float(g.sum())
    return [float(c) for c in coef], float(intercept)


def bounded(coef, low=0.05, high=0.50):
    """Weights from coefficients: share of the sum, each kept in [low, high], the rest rescaled to sum to 1."""
    k = len(coef)
    total = sum(coef)
    w = [c / total for c in coef] if total > 0 else [1.0 / k] * k
    for _ in range(100):
        fixed = [min(max(v, low), high) for v in w]
        free = [i for i, v in enumerate(w) if low < v < high]
        rest = 1.0 - sum(fixed[i] for i in range(k) if i not in free)
        free_total = sum(w[i] for i in free)
        new = list(fixed)
        if free:
            for i in free:
                new[i] = rest * w[i] / free_total if free_total > 0 else rest / len(free)
        else:
            s = sum(new)
            new = [v / s for v in new]
        if max(abs(a - b) for a, b in zip(new, w, strict=False)) < 1e-12:
            w = new
            break
        w = new
    s = sum(w)
    return [v / s for v in w]


def auc(labels, scores):
    """Probability a random label-1 account scores above a random label-0 one (ties count half)."""
    pairs = sorted(zip(scores, labels, strict=False))
    pos = sum(1 for _, y in pairs if y == 1)
    neg = len(pairs) - pos
    if not pos or not neg:
        return None
    rank_sum, i = 0.0, 0
    while i < len(pairs):
        j = i
        while j < len(pairs) and pairs[j][0] == pairs[i][0]:
            j += 1
        avg = (i + j + 1) / 2.0
        rank_sum += avg * sum(1 for t in range(i, j) if pairs[t][1] == 1)
        i = j
    return (rank_sum - pos * (pos + 1) / 2.0) / (pos * neg)


def precision_at(labels, scores, cutoff=CUTOFF):
    flagged = [y for s, y in zip(scores, labels, strict=False) if s >= cutoff]
    return sum(flagged) / len(flagged) if flagged else None


def score_of(rows, signals, weights):
    total = sum(weights)
    return [sum(w * r[s] for s, w in zip(signals, weights, strict=False)) / total for r in rows]


def train(table, seed=42, test_share=0.30, l2=0.001, low=0.05, high=0.50):
    """table: {score_name: {"signals": [...], "start": [...], "rows": {account: {signal: m}}}}, labels: account -> y.
    Returns one result dict per (score, signal)."""
    out = []
    for score_name in sorted(table):
        t = table[score_name]
        signals, start, rows, labels = t["signals"], t["start"], t["rows"], t["labels"]
        accounts = sorted(rows)
        train_ids, test_ids = split(accounts, labels, seed, test_share)
        x = [[rows[a][s] for s in signals] for a in train_ids]
        y = [labels[a] for a in train_ids]
        coef, _ = fit(x, y, l2)
        weights = bounded(coef, low, high)
        test_rows = [rows[a] for a in test_ids]
        test_y = [labels[a] for a in test_ids]
        learned = score_of(test_rows, signals, weights)
        base = score_of(test_rows, signals, start)
        metrics = {"test_auc": auc(test_y, learned), "test_precision": precision_at(test_y, learned),
                   "start_auc": auc(test_y, base), "start_precision": precision_at(test_y, base),
                   "train_accounts": len(train_ids), "test_accounts": len(test_ids)}
        for s, c, w, w0 in zip(signals, coef, weights, start, strict=False):
            out.append({"score_name": score_name, "signal_name": s, "coefficient": c, "weight": w,
                        "start_weight": w0, **metrics})
    return out


def run(ctx):
    table, seed, test_share, l2, low, high = {}, 42, 0.30, 0.001, 0.05, 0.50
    while True:
        t = table.setdefault(ctx.score_name, {"signals": [], "start": [], "rows": {}, "labels": {}})
        if ctx.signal_name not in t["signals"]:
            t["signals"].append(ctx.signal_name)
            t["start"].append(float(ctx.start_weight))
        t["rows"].setdefault(ctx.account_id, {})[ctx.signal_name] = float(ctx.m_value or 0)
        t["labels"][ctx.account_id] = int(ctx.label)
        seed, test_share, l2 = int(ctx.seed), float(ctx.test_share), float(ctx.l2)
        low, high = float(ctx.weight_min), float(ctx.weight_max)
        if not ctx.next():
            break
    for t in table.values():                     # an account missing a signal row counts as m = 0
        for row in t["rows"].values():
            for s in t["signals"]:
                row.setdefault(s, 0.0)
    for r in train(table, seed, test_share, l2, low, high):
        ctx.emit(r["score_name"], r["signal_name"], r["coefficient"], r["weight"], r["start_weight"], r["test_auc"],
                 r["test_precision"], r["start_auc"], r["start_precision"], r["train_accounts"], r["test_accounts"])