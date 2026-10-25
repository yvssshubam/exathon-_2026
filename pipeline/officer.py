"""The officer's four decisions, from a terminal (the console uses the same code through the API).

Only four things ever wait for the officer: narrow (hold less than the notice asks, sized to the victims' money),
widen (a full freeze), an unmatched notice (a near match: what to do with it) and a release (a hold with no police
direction in 75 days). Each waits as a proposal the checker already validated; approving or rejecting is recorded
with the officer's name and time, and the checker carries it out (pipeline/checker.py: apply_decision). Editing the
reasons is optional; when the officer does, the draft and the signed version are both kept.

    python -m pipeline.officer waiting
    python -m pipeline.officer approve 7 --officer priya
    python -m pipeline.officer approve 7 --officer priya --amount 600 --comment "Diesel sale was Rs 600 of it"
    python -m pipeline.officer reject 9 --officer priya --reason "Wait for the IO"
"""
from __future__ import annotations

import argparse
import sys

from pipeline.checker import Checker, DecisionError

DECISIONS = ("PROPOSE_NARROW", "PROPOSE_FULL_FREEZE", "UNMATCHED_NOTICE", "PROPOSE_RELEASE")

__all__ = ["DECISIONS", "DecisionError", "approve", "reject", "waiting"]


def now_of(conn) -> str:
    """Decisions happen on the simulated clock when there is one (the demo's 9 a.m.), else real time."""
    try:
        v = conn.execute("SELECT MAX(clock_at) FROM PH_PORTAL.SIM_CLOCK").fetchval()
    except Exception:  # the checker user cannot read the portal: fall back to the bank's latest event
        v = None
    if v is None:
        v = conn.execute("SELECT GREATEST(COALESCE(MAX(checked_at), TIMESTAMP '1900-01-01 00:00:00'), "
                         "COALESCE((SELECT MAX(placed_at) FROM HOLD_LEDGER), TIMESTAMP '1900-01-01 00:00:00')) "
                         "FROM PROPOSAL").fetchval()
    return str(v)[:19]


def approve(conn, bank: str, proposal_id: int, officer: str, comment: str | None = None, amount=None,
            signed_text: str | None = None, now: str | None = None) -> int:
    return Checker(conn, bank).apply_decision(proposal_id, "APPROVED", officer, now or now_of(conn), comment, amount,
                                              signed_text)


def reject(conn, bank: str, proposal_id: int, officer: str, reason: str | None, now: str | None = None) -> int:
    return Checker(conn, bank).apply_decision(proposal_id, "REJECTED", officer, now or now_of(conn), reason)


def waiting(conn) -> list[tuple]:
    return conn.execute(
        "SELECT p.proposal_id, p.case_id, p.account_id, p.action_code, p.amount_inr FROM PROPOSAL p "
        "WHERE p.proposal_status = 'WAITING' ORDER BY p.proposal_id").fetchall()


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("waiting")
    for name in ("approve", "reject"):
        p = sub.add_parser(name)
        p.add_argument("proposal_id", type=int)
        p.add_argument("--officer", default="")
        if name == "approve":
            p.add_argument("--comment")
            p.add_argument("--amount", type=float)
        else:
            p.add_argument("--reason")
    ap.add_argument("--bank")
    ap.add_argument("--now", help='simulated time of the decision, "YYYY-MM-DD HH:MM:SS"')
    args = ap.parse_args(argv)

    from pipeline.db import connect, own_bank

    bank = args.bank or own_bank()
    conn = connect("checker", bank)
    try:
        if args.cmd == "waiting":
            for pid, case_id, acc, code, amount in waiting(conn):
                print(f"{int(pid):>4}  {case_id:14} {acc:12} {code:20} {amount if amount is not None else ''}")
        elif args.cmd == "approve":
            d = approve(conn, bank, args.proposal_id, args.officer, args.comment, args.amount, now=args.now)
            print(f"approved: decision {d}")
        else:
            d = reject(conn, bank, args.proposal_id, args.officer, args.reason, now=args.now)
            print(f"rejected: decision {d}")
    except DecisionError as exc:
        print(f"Not allowed: {exc}", file=sys.stderr)
        return 2
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())