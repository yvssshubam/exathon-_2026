"""Run the per-case steps in Exasol (DECISIONS D-18). Python only passes parameters; the work is SQL in sql/steps/.

    python -m pipeline.steps trace CASE_DEMO                 # viewed as OWN_BANK_ID from .env (default BANK_X)
    python -m pipeline.steps trace CASE_DEMO --bank BANK_Z   # the same case seen from Murugan's bank
    python -m pipeline.steps trace CASE_LOOP --as-of "2026-10-18 23:59:59"

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
    ap.add_argument("step", choices=["trace"])
    ap.add_argument("case_id")
    ap.add_argument("--bank", help="bank using the tool (default: OWN_BANK_ID in .env)")
    ap.add_argument("--as-of", help='replay cut-off, "YYYY-MM-DD HH:MM:SS" (default: reported_at + hold_minutes)')
    ap.add_argument("--max-hops", type=int)
    ap.add_argument("--window-hours", type=int)
    args = ap.parse_args(argv)

    from pipeline.db import connect, settings

    bank = args.bank or settings()["own_bank_id"]
    conn = connect()
    if args.step == "trace":
        info = trace(conn, args.case_id, bank, args.as_of, args.max_hops, args.window_hours)
        print(f"trace {args.case_id}  viewed as {bank}  as_of {info['as_of']}  "
              f"max {info['max_hops']} hops / {info['window_hours']} h  took {info['seconds']:.2f}s\n")
        print_trace(conn, args.case_id)
    conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())