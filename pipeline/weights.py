"""The learned weights of account scoring v2 (DECISIONS D-58). A logistic regression trained inside Exasol decides how
much each signal counts, within fixed bounds; what each signal measures, its low and high points, the cut-offs and the
rules stay set by policy (config/scoring.yaml). A new version goes live only after the gate and a person's approval.

    python -m pipeline.weights train                     # train on PH_AML (needs `make data`): a new version, not live
    python -m pipeline.weights check --version 1         # the gate: bounds, the five demo accounts, AUC
    python -m pipeline.weights approve --version 1 --by "Priya (nodal officer)"   # the gate again, then live
    python -m pipeline.weights show                      # every version with its test results
    python -m pipeline.weights export-labels             # the outcome labels (Module 7), as CSV

Training: SQL builds one row per account and signal with its m value (sql/learn/020_weight_rows.sql), then the
Python UDF TRAIN_SCORING_WEIGHTS (sql/udf/train_weights.py) fits both scores and measures them on the 30% of accounts
held back. The boosted trees of D-51 are trained on the same rows as a benchmark only: they never change a score.
Versions live in config/scoring_weights.yaml (so `make seed` keeps them) and in PH_LEARN.SCORING_WEIGHTS; an approved
version is written into every bank's SCORING_WEIGHTS and marked live there. Every snapshot cites the version it used.
"""
from __future__ import annotations

import argparse
import importlib.util
import sys
from datetime import datetime
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
CONFIG = yaml.safe_load((ROOT / "config" / "scoring.yaml").read_text(encoding="utf-8"))
LEARN = CONFIG["weight_learning"]
VERSIONS = ROOT / "config" / "scoring_weights.yaml"
REPORT = ROOT / "docs" / "SCORING_WEIGHTS.md"
SCORES = ("pass_through", "fraud_exposure")
FMT = "%Y-%m-%d %H:%M:%S"
CAVEAT = ("Synthetic data (IBM AMLworld HI-Small). The label is 'sends laundering money', close to but not the same "
          "as 'receives fraud money'; complaints and notices are stand-ins (sql/learn/020_weight_rows.sql).")


def trainer():
    """The UDF's own Python (sql/udf/train_weights.py), importable here and in the tests."""
    spec = importlib.util.spec_from_file_location("train_weights", ROOT / "sql" / "udf" / "train_weights.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def load_versions(path: Path = VERSIONS) -> dict:
    if not path.exists():
        return {"versions": []}
    return yaml.safe_load(path.read_text(encoding="utf-8")) or {"versions": []}


def save_versions(data: dict, path: Path = VERSIONS) -> None:
    head = ("# Learned weights of scoring v2 (pipeline/weights.py; DECISIONS D-58).\n"
            "# Version 0 is config/scoring.yaml's\n"
            "# start_weight. Only an approved version is ever loaded (pipeline/load_cases.py weight_rows).\n")
    path.write_text(head + yaml.safe_dump(data, sort_keys=False, allow_unicode=True, width=120), encoding="utf-8")


def weights_of(version: dict) -> dict[str, dict[str, float]]:
    out: dict[str, dict[str, float]] = {s: {} for s in SCORES}
    for w in version["weights"]:
        out[w["score"]][w["signal"]] = float(w["weight"])
    return out


def live_version(data: dict) -> dict | None:
    approved = [v for v in data["versions"] if v.get("approved_by")]
    return max(approved, key=lambda v: int(v["version"])) if approved else None


# ---------------------------------------------------------------- the gate

def check(version: dict, data: dict) -> list[str]:
    """Why the version may not go live (empty: it passes). 1 every weight in bounds and summing to 1 per score;
    2 the five demo accounts still land in their expected types; 3 test AUC no more than 0.02 below the live one."""
    from pipeline.scoring import demo_types_hold

    problems = []
    w = weights_of(version)
    for score_name in SCORES:
        values = list(w[score_name].values())
        if set(w[score_name]) != set(CONFIG[score_name]):
            problems.append(f"{score_name}: the version does not weigh exactly the policy's signals")
            continue
        for name, v in w[score_name].items():
            if not LEARN["weight_min"] - 1e-9 <= v <= LEARN["weight_max"] + 1e-9:
                problems.append(f"{score_name}.{name} = {v:.3f} is outside {LEARN['weight_min']} to "
                                f"{LEARN['weight_max']}")
        if abs(sum(values) - 1.0) > 1e-6:
            problems.append(f"{score_name}: weights sum to {sum(values):.4f}, not 1")
    moved = demo_types_hold(w)
    if moved:
        problems.append("these demo accounts would change type: " + ", ".join(moved))
    live = live_version(data)
    for score_name in SCORES:
        new_auc = ((version.get("test") or {}).get(score_name) or {}).get("auc")
        if live is not None and int(live["version"]) != int(version["version"]):
            base = ((live.get("test") or {}).get(score_name) or {}).get("auc")
        else:
            base = ((version.get("test") or {}).get(score_name) or {}).get("start_auc")
        if new_auc is not None and base is not None and new_auc < base - LEARN["max_auc_drop_vs_live"]:
            problems.append(f"{score_name}: test AUC {new_auc:.3f} is more than {LEARN['max_auc_drop_vs_live']} below "
                            f"the live version's {base:.3f}")
    return problems


def find(data: dict, number: int) -> dict:
    for v in data["versions"]:
        if int(v["version"]) == int(number):
            return v
    raise SystemExit(f"No version {number} in {VERSIONS.name}: python -m pipeline.weights show")


# ---------------------------------------------------------------- training (Exasol)

def benchmark(rows: list[tuple], seed: int, test_share: float) -> dict:
    """The boosted trees of D-51 on the same rows and the same split, per score: a benchmark, never deployed."""
    import numpy as np

    from pipeline.learned import auc, boost_fit, boost_predict

    tw = trainer()
    out = {}
    for score_name in SCORES:
        signals = list(CONFIG[score_name])
        table: dict[str, dict] = {}
        labels: dict[str, int] = {}
        for acc, label, sname, signal, m in rows:
            if sname != score_name:
                continue
            table.setdefault(acc, dict.fromkeys(signals, 0.0))[signal] = float(m or 0)
            labels[acc] = int(label)
        if not table:
            continue
        train_ids, test_ids = tw.split(sorted(table), labels, seed, test_share)
        x = np.array([[table[a][s] for s in signals] for a in train_ids])
        y = np.array([labels[a] for a in train_ids])
        xt = np.array([[table[a][s] for s in signals] for a in test_ids])
        yt = np.array([labels[a] for a in test_ids])
        trees = boost_fit(x, y)
        p = boost_predict(trees, xt)
        flagged = p >= 0.6
        out[score_name] = {"auc": round(float(auc(yt, p)), 4),
                           "precision": round(float((yt[flagged] == 1).mean()), 4) if flagged.any() else None}
    return out


def train(conn, max_txns: int = 1000, max_positives: int = 20000, negatives_per_positive: int = 4) -> dict:
    """Build the rows in PH_LEARN, run the UDF, measure the benchmark. Returns the new version (not approved)."""
    from pipeline.run_sql import split_statements

    first, last = (str(v)[:19] for v in conn.execute("SELECT MIN(ts), MAX(ts) FROM PH_AML.TXN").fetchone())
    t0, t1 = datetime.strptime(first, FMT), datetime.strptime(last, FMT)
    as_of = (t0 + (t1 - t0) * float(LEARN["signal_window_share"])).strftime(FMT)
    positives = int(conn.execute("SELECT COUNT(DISTINCT from_account) FROM PH_AML.TXN WHERE is_laundering "
                                 "AND ts > TO_TIMESTAMP({a}, 'YYYY-MM-DD HH24:MI:SS')", {"a": as_of}).fetchval() or 0)
    params = {"as_of": as_of, "label_end": last, "max_txns": str(max_txns), "max_positives": str(max_positives),
              "max_negatives": str(max(positives, 1000) * negatives_per_positive)}
    for f in ("020_weight_rows.sql", "030_train_udf.sql"):
        for stmt in split_statements((ROOT / "sql" / "learn" / f).read_text(encoding="utf-8")):
            conn.execute(stmt, params)
    conn.execute("CREATE OR REPLACE TABLE PH_LEARN.W_CONFIG (score_name VARCHAR(20), signal_name VARCHAR(40), "
                 "low_at DOUBLE, high_at DOUBLE, start_weight DOUBLE)")
    conn.import_from_iterable([(s, n, sig["low_at"], sig["high_at"], sig["start_weight"])
                               for s in SCORES for n, sig in CONFIG[s].items()], ("PH_LEARN", "W_CONFIG"))
    conn.execute(f"""
        CREATE OR REPLACE TABLE PH_LEARN.W_ROWS AS
        SELECT s.account_id, s.label, c.score_name, c.signal_name,
               LEAST(1, GREATEST(0, COALESCE((v.signal_value - c.low_at) / (c.high_at - c.low_at), 0))) AS m_value,
               c.start_weight, {int(LEARN['seed'])} AS seed, {float(LEARN['test_accounts_share'])} AS test_share,
               {float(LEARN['l2'])} AS l2, {float(LEARN['weight_min'])} AS weight_min,
               {float(LEARN['weight_max'])} AS weight_max
        FROM PH_LEARN.W_SAMPLE s CROSS JOIN PH_LEARN.W_CONFIG c
        LEFT JOIN PH_LEARN.W_VALUES v ON v.account_id = s.account_id AND v.signal_name = c.signal_name""")
    result = conn.execute("SELECT PH_LEARN.TRAIN_SCORING_WEIGHTS(account_id, label, score_name, signal_name, m_value, "
                          "start_weight, seed, test_share, l2, weight_min, weight_max) FROM PH_LEARN.W_ROWS").fetchall()
    rows = conn.execute("SELECT account_id, label, score_name, signal_name, m_value FROM PH_LEARN.W_ROWS").fetchall()
    bench = benchmark(rows, int(LEARN["seed"]), float(LEARN["test_accounts_share"]))
    data = load_versions()
    number = max([0] + [int(v["version"]) for v in data["versions"]]) + 1
    version = {"version": number, "trained_at": datetime.now().strftime(FMT),
               "data_window": f"AMLworld: signals {as_of[:10]} back 90 days, label to {last[:10]}",
               "approved_by": None, "approved_at": None, "weights": [], "test": {}, "benchmark": bench,
               "caveat": CAVEAT}
    for score_name, signal, coef, weight, start, auc, prec, sauc, sprec, n_train, n_test in result:
        version["weights"].append({"score": score_name, "signal": signal, "coefficient": round(float(coef), 6),
                                   "weight": round(float(weight), 6), "start_weight": float(start)})
        version["test"][score_name] = {"auc": None if auc is None else round(float(auc), 4),
                                       "precision": None if prec is None else round(float(prec), 4),
                                       "start_auc": None if sauc is None else round(float(sauc), 4),
                                       "start_precision": None if sprec is None else round(float(sprec), 4),
                                       "train_accounts": int(n_train), "test_accounts": int(n_test)}
    for score_name in SCORES:            # rounding: keep each score's weights summing to exactly 1
        ws = [w for w in version["weights"] if w["score"] == score_name]
        if ws:
            ws[-1]["weight"] = round(1.0 - sum(w["weight"] for w in ws[:-1]), 6)
    conn.execute("CREATE TABLE IF NOT EXISTS PH_LEARN.SCORING_WEIGHTS (weight_version DECIMAL(9,0), score_name "
                 "VARCHAR(20), signal_name VARCHAR(40), coefficient DOUBLE, weight DOUBLE, trained_at TIMESTAMP, "
                 "data_window VARCHAR(100), test_auc DOUBLE, test_precision DOUBLE, approved_by VARCHAR(60), "
                 "approved_at TIMESTAMP, is_live BOOLEAN)")
    conn.import_from_iterable([(number, w["score"], w["signal"], w["coefficient"], w["weight"], version["trained_at"],
                                version["data_window"][:100], version["test"][w["score"]]["auc"],
                                version["test"][w["score"]]["precision"], None, None, False)
                               for w in version["weights"]], ("PH_LEARN", "SCORING_WEIGHTS"))
    data["versions"].append(version)
    save_versions(data)
    return version


def approve(conn, number: int, by: str) -> list[str]:
    """Run the gate; if it passes, mark the version approved and live in every bank's SCORING_WEIGHTS."""
    from pipeline.db import BANKS, audit, schema_for

    by = (by or "").strip()
    if not by:
        raise SystemExit("--by is required: a person approves every new version.")
    data = load_versions()
    version = find(data, number)
    problems = check(version, data)
    if problems:
        return problems
    version["approved_by"], version["approved_at"] = by, datetime.now().strftime(FMT)
    save_versions(data)
    for bank in BANKS:
        s = schema_for(bank)
        conn.execute(f"DELETE FROM {s}.SCORING_WEIGHTS WHERE weight_version = {{v!d}}", {"v": str(number)})
        conn.execute(f"UPDATE {s}.SCORING_WEIGHTS SET is_live = FALSE")
        conn.import_from_iterable([(number, w["score"], w["signal"], w["coefficient"], w["weight"],
                                    version["trained_at"], version["data_window"][:100],
                                    version["test"][w["score"]]["auc"], version["test"][w["score"]]["precision"], by,
                                    version["approved_at"], True) for w in version["weights"]], (s, "SCORING_WEIGHTS"))
        conn.execute(f"OPEN SCHEMA {s}")
        audit(conn, "weights_approved", detail=f"version {number} approved by {by}; live from now", actor=by)
    return []


def report(data: dict) -> str:
    lines = ["# Learned scoring weights", "", f"_{CAVEAT}_", "",
             "Generated by `python -m pipeline.weights train`. Each version is measured on the 30% of accounts held "
             "back; the boosted trees are a benchmark on the same rows and never change a score (D-58).", ""]
    for v in data["versions"]:
        lines += [f"## Version {v['version']}" + (f" (approved by {v['approved_by']})" if v.get("approved_by") else
                                                   " (not approved: never used)"), "", v.get("data_window", ""), "",
                  "| Score | Model | AUC | Precision at 0.6 |", "|---|---|---|---|"]
        for score_name in SCORES:
            t = (v.get("test") or {}).get(score_name) or {}
            b = (v.get("benchmark") or {}).get(score_name) or {}
            lines += [f"| {score_name} | starting weights (version 0) | {t.get('start_auc')} | "
                      f"{t.get('start_precision')} |",
                      f"| {score_name} | learned weights | {t.get('auc')} | {t.get('precision')} |",
                      f"| {score_name} | boosted trees (benchmark) | {b.get('auc')} | {b.get('precision')} |"]
        lines += ["", "| Score | Signal | Starting weight | Learned weight | Coefficient |", "|---|---|---|---|---|"]
        lines += [f"| {w['score']} | {w['signal']} | {w['start_weight']} | {w['weight']:.3f} | {w['coefficient']:.4f} |"
                  for w in v["weights"]]
        lines.append("")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("command", choices=["train", "check", "approve", "show", "export-labels"])
    ap.add_argument("--version", type=int)
    ap.add_argument("--by", help="who approves (approve)")
    ap.add_argument("--bank", default="BANK_X", help="export-labels: the bank whose outcomes to export")
    args = ap.parse_args(argv)
    data = load_versions()
    if args.command == "show":
        print(report(data) if data["versions"] else "Only version 0 (config/scoring.yaml start_weight) exists.")
        return 0
    if args.command == "check":
        problems = check(find(data, args.version), data)
        print("passes the gate" if not problems else "refused:\n  " + "\n  ".join(problems))
        return 0 if not problems else 1
    from pipeline.db import connect

    conn = connect() if args.command != "export-labels" else connect("maker", args.bank)
    try:
        if args.command == "train":
            if not conn.execute("SELECT COUNT(*) FROM EXA_ALL_TABLES WHERE TABLE_SCHEMA = 'PH_AML' "
                                "AND TABLE_NAME = 'TXN'").fetchval():
                print("PH_AML is not loaded: run `make data` first.", file=sys.stderr)
                return 1
            v = train(conn)
            REPORT.write_text(report(load_versions()), encoding="utf-8")
            problems = check(v, load_versions())
            print(f"version {v['version']} trained (not live). Gate: "
                  + ("passes; approve with: python -m pipeline.weights approve --version "
                     f"{v['version']} --by \"<name>\"" if not problems else "refused:\n  " + "\n  ".join(problems)))
            print(f"report: {REPORT.relative_to(ROOT)}")
            return 0
        if args.command == "approve":
            problems = approve(conn, args.version, args.by)
            if problems:
                print("refused, nothing changed:\n  " + "\n  ".join(problems))
                return 1
            REPORT.write_text(report(load_versions()), encoding="utf-8")
            print(f"version {args.version} is live in every bank (approved by {args.by})")
            return 0
        print("account_id,snapshot_id,label")
        for a, s, lab in conn.execute("SELECT account_id, snapshot_id, label FROM PROACTIVE_HOLD_LABEL "
                                      "ORDER BY account_id, snapshot_id").fetchall():
            print(f"{a},{'' if s is None else int(s)},{int(lab)}")
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    sys.exit(main())