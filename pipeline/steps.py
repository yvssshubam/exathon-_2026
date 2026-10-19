"""Run the per-case steps in Exasol (DECISIONS D-18). Python only passes parameters; the work is SQL in sql/steps/.

    python -m pipeline.steps trace CASE_DEMO                 # viewed as OWN_BANK_ID from .env (default BANK_X)
    python -m pipeline.steps trace CASE_DEMO --bank BANK_Z   # the same case seen from Murugan's bank
    python -m pipeline.steps trace CASE_LOOP --as-of "2026-10-18 23:59:59"
    python -m pipeline.steps attribute CASE_DEMO             # needs the trace first
    python -m pipeline.steps score CASE_DEMO                 # needs trace + attribute first
    python -m pipeline.steps run CASE_DEMO                   # trace + attribute + score

Each run prints what it wrote, so a wrong number can be traced to the step and the statement that made it.
"""
from __future__ import annotations

import argparse
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

import yaml

from pipeline.run_sql import split_statements

ROOT = Path(__file__).resolve().parents[1]
STEPS = ROOT / "sql" / "steps"
DEFAULTS = yaml.safe_load((ROOT / "config" / "case_defaults.yaml").read_text(encoding="utf-8"))
SCORING = yaml.safe_load((ROOT / "config" / "scoring.yaml").read_text(encoding="utf-8"))
FMT = "%Y-%m-%d %H:%M:%S"


def complaint(conn, case_id: str) -> dict:
    row = conn.execute(
        "SELECT case_id, utr, reported_at, lane, victim_tag FROM PH.COMPLAINT WHERE case_id = {case_id}",
        {"case_id": case_id},
    ).fetchone()
    if row is None:
        raise SystemExit(f"No complaint {case_id!r} in PH.COMPLAINT. Load cases: python -m pipeline.load_cases")
    keys = ("case_id", "utr", "reported_at", "lane", "victim_tag")
    return dict(zip(keys, row, strict=True))


def default_as_of(reported_at) -> str:
    """Holds are placed hold_minutes after the ticket reaches the bank (DECISIONS D-19)."""
    if isinstance(reported_at, str):
        reported_at = datetime.strptime(reported_at[:19], FMT)
    return (reported_at + timedelta(minutes=int(DEFAULTS["hold_minutes"]))).strftime(FMT)


def run_step(conn, file_name: str, params: dict) -> list:
    """Run every statement of sql/steps/<file_name> with params; return the statement objects."""
    statements = split_statements((STEPS / file_name).read_text(encoding="utf-8"))
    results = []
    for i, stmt in enumerate(statements, 1):
        try:
            results.append(conn.execute(stmt, params))
        except Exception as exc:
            first = stmt.splitlines()[0][:90]
            raise SystemExit(f"FAILED {file_name} statement {i}: {first}\n{exc}") from exc
    return results


def trace(conn, case_id: str, bank: str, as_of: str | None = None,
          max_hops: int | None = None, window_hours: int | None = None) -> dict:
    c = complaint(conn, case_id)
    params = {
        "case_id": case_id,
        "utr": c["utr"],
        "as_of": as_of or default_as_of(c["reported_at"]),
        "max_hops": int(max_hops or DEFAULTS["max_hops"]),
        "window_hours": int(window_hours or DEFAULTS["window_hours"]),
        "own_bank_id": bank,
    }
    started = time.perf_counter()
    run_step(conn, "040_trace.sql", params)
    elapsed = time.perf_counter() - started
    from pipeline.db import audit

    audit(conn, "trace", case_id, f"bank={bank} as_of={params['as_of']} hops={params['max_hops']}")
    return {**params, "seconds": elapsed}


def _ts(value) -> str:
    return str(value)[:19]


def attribute(conn, case_id: str, bank: str, as_of: str | None = None, rule: str | None = None) -> dict:
    """Step 2. Uses the accounts the trace reached, so run trace first with the same as_of."""
    c = complaint(conn, case_id)
    as_of = as_of or default_as_of(c["reported_at"])
    traced = conn.execute("SELECT COUNT(*) FROM PH.TRACE_EDGE WHERE case_id = {c}", {"c": case_id}).fetchval()
    if not traced:
        raise SystemExit(f"No trace for {case_id}. Run: python -m pipeline.steps trace {case_id}")
    root_ts = _ts(conn.execute("SELECT MIN(ts) FROM PH.TXN WHERE utr = {u}", {"u": c["utr"]}).fetchval())
    snapshot = conn.execute(
        "SELECT MAX(ts) FROM PH.BALANCE_SNAPSHOT WHERE ts <= TO_TIMESTAMP({r}, 'YYYY-MM-DD HH24:MI:SS') "
        "AND account_id IN (SELECT to_account FROM PH.TRACE_EDGE WHERE case_id = {c})",
        {"r": root_ts, "c": case_id},
    ).fetchval()
    replay_from = _ts(snapshot) if snapshot else (
        datetime.strptime(root_ts, FMT) - timedelta(seconds=1)).strftime(FMT)
    params = {
        "case_id": case_id,
        "victim_tag": c["victim_tag"],
        "as_of": as_of,
        "replay_from": replay_from,
        "own_bank_id": bank,
        "rule": rule or DEFAULTS["default_rule"],
    }
    started = time.perf_counter()
    run_step(conn, "050_attribute.sql", params)
    elapsed = time.perf_counter() - started
    from pipeline.db import audit

    audit(conn, "attribute", case_id, f"bank={bank} as_of={as_of} replay_from={replay_from}")
    return {**params, "seconds": elapsed}


def print_attribution(conn, case_id: str) -> None:
    rows = conn.execute(
        "SELECT account_id, trace_rule, victim_amount_inr, other_victims_inr, own_funds_inr, stmt_id "
        "FROM PH.ATTRIBUTION WHERE case_id = {c}", {"c": case_id}).fetchall()
    table: dict[str, dict] = {}
    for acc, rule, victim, others, own, _stmt in rows:
        table.setdefault(acc, {})[rule] = (float(victim), float(others), float(own))
    print(f"{'account / exit':26} {'LIBR':>13} {'FIFO':>13} {'PRO_RATA':>13}   other victims   own money (LIBR)")
    totals = {"LIBR": 0.0, "FIFO": 0.0, "PRO_RATA": 0.0}
    for acc in sorted(table, key=lambda a: (a.startswith("EXIT:"), a)):
        r = table[acc]
        cells = "".join(f"{r.get(rule, (0, 0, 0))[0]:>13,.2f} " for rule in ("LIBR", "FIFO", "PRO_RATA"))
        libr = r.get("LIBR", (0, 0, 0))
        print(f"{acc:26} {cells}  {libr[1]:>13,.2f}   {libr[2]:>14,.2f}")
        for rule in totals:
            totals[rule] += r.get(rule, (0, 0, 0))[0]
    print(f"{'total (must equal the reported amount)':26} " +
          "".join(f"{totals[rule]:>13,.2f} " for rule in ("LIBR", "FIFO", "PRO_RATA")))
    if rows:
        print(f"(statement {rows[0][5]})")
    refs = conn.execute(
        "SELECT from_bank_id, from_account, bank_id, account_id, amount_inr, victim_amount_inr, trace_rule "
        "FROM PH.REFERRAL_OUT WHERE case_id = {c} ORDER BY ts", {"c": case_id}).fetchall()
    if refs:
        print("\nreferrals to other banks (simulated CFCFRMS update):")
        for fb, fa, bank, acc, amount, victim, rule in refs:
            print(f"  {fb} {fa} -> {bank} {acc}: Rs {float(amount):,.2f} sent, "
                  f"Rs {float(victim):,.2f} of it the victim's ({rule})")


def score(conn, case_id: str, as_of: str | None = None) -> dict:
    """Step 3. Uses the trace and the LIBR attribution, so run those first with the same as_of."""
    c = complaint(conn, case_id)
    params = {
        "case_id": case_id,
        "as_of": as_of or default_as_of(c["reported_at"]),
        "mule_band": str(SCORING["bands"]["mule"]),
        "review_band": str(SCORING["bands"]["review"]),
    }
    started = time.perf_counter()
    run_step(conn, "060_score.sql", params)
    elapsed = time.perf_counter() - started
    from pipeline.db import audit

    audit(conn, "score", case_id, f"as_of={params['as_of']}")
    return {**params, "seconds": elapsed}


def print_score(conn, case_id: str) -> None:
    scores = conn.execute(
        "SELECT account_id, score, band, stmt_id FROM PH.ACCOUNT_SCORE WHERE case_id = {c} ORDER BY account_id",
        {"c": case_id}).fetchall()
    for acc, value, band, stmt in scores:
        rank_col = "mule_rank" if band in ("MULE", "REVIEW") else "honest_rank"
        reasons = conn.execute(
            f"SELECT sentence FROM PH.ACCOUNT_SIGNALS WHERE case_id = {{c}} AND account_id = {{a}} "
            f"AND {rank_col} <= {int(SCORING['top_reasons'])} ORDER BY {rank_col}",
            {"c": case_id, "a": acc}).fetchall()
        print(f"{acc:14} {band:6} score {float(value):.2f}   (statement {stmt})")
        for (sentence,) in reasons:
            print(f"    - {sentence}")


def print_trace(conn, case_id: str) -> None:
    rows = conn.execute(
        "SELECT hop, from_account, to_account, amount_inr, ts, is_own_bank, is_exit, is_cycle, stmt_id "
        "FROM PH.TRACE_EDGE WHERE case_id = {case_id} ORDER BY hop, ts, txn_id",
        {"case_id": case_id},
    ).fetchall()
    print(f"{'hop':>3}  {'from':14} {'to':24} {'amount (Rs)':>13}  {'time':19}  where      stmt")
    for hop, frm, to, amount, ts, own, is_exit, cycle, stmt in rows:
        where = "exit" if is_exit else ("own bank" if own else "other bank")
        flag = "  LOOP" if cycle else ""
        print(f"{int(hop):>3}  {frm:14} {to:24} {float(amount):>13,.2f}  {str(ts)[:19]:19}  {where:10} {stmt}{flag}")
    print(f"{len(rows)} edges")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("step", choices=["trace", "attribute", "score", "run"])
    ap.add_argument("case_id")
    ap.add_argument("--bank", help="bank using the tool (default: OWN_BANK_ID in .env)")
    ap.add_argument("--as-of", help='replay cut-off, "YYYY-MM-DD HH:MM:SS" (default: reported_at + hold_minutes)')
    ap.add_argument("--max-hops", type=int)
    ap.add_argument("--window-hours", type=int)
    ap.add_argument("--rule", choices=["LIBR", "FIFO", "PRO_RATA"], help="rule for referral shares (default LIBR)")
    args = ap.parse_args(argv)

    from pipeline.db import connect, settings

    bank = args.bank or settings()["own_bank_id"]
    conn = connect()
    if args.step in ("trace", "run"):
        info = trace(conn, args.case_id, bank, args.as_of, args.max_hops, args.window_hours)
        print(f"trace {args.case_id}  viewed as {bank}  as_of {info['as_of']}  "
              f"max {info['max_hops']} hops / {info['window_hours']} h  took {info['seconds']:.2f}s\n")
        print_trace(conn, args.case_id)
    if args.step in ("attribute", "run"):
        info = attribute(conn, args.case_id, bank, args.as_of, args.rule)
        print(f"\nattribute {args.case_id}  victim {info['victim_tag']}  as_of {info['as_of']}  "
              f"replay from {info['replay_from']}  took {info['seconds']:.2f}s\n")
        print_attribution(conn, args.case_id)
    if args.step in ("score", "run"):
        info = score(conn, args.case_id, args.as_of)
        print(f"\nscore {args.case_id}  as_of {info['as_of']}  took {info['seconds']:.2f}s\n")
        print_score(conn, args.case_id)
    conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())