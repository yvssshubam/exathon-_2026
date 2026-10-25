"""Run the per-case steps in Exasol (DECISIONS D-18). Python only passes parameters; the work is SQL in sql/steps/.

Every step runs in the schema the connection has open (one bank's schema: pipeline/db.py), so the same code is the
agent at Bank X and at Bank Y. These are the database halves of the agent's tools (pipeline/agent.py runs them and
logs each one) and of the checker's actions (pipeline/checker.py). Run them by hand to inspect a single step:

    python -m pipeline.steps intake CASE_DEMO                # is the ticket's transaction there?
    python -m pipeline.steps trace CASE_DEMO                 # needs intake (an exact match)
    python -m pipeline.steps attribute CASE_DEMO             # needs the trace first
    python -m pipeline.steps score CASE_DEMO                 # account evidence; needs trace + attribute
    python -m pipeline.steps actions CASE_DEMO               # the rules engine; changes nothing
    python -m pipeline.steps run CASE_DEMO                   # all of the above (still changes nothing)
    ... --bank BANK_Y                                        # default: OWN_BANK_ID in .env

Holds and portal entries are written only by the checker (pipeline/checker.py), under its own database user.
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
RULES = yaml.safe_load((ROOT / "config" / "rules.yaml").read_text(encoding="utf-8"))
ACTION_COLUMNS = ("account_id", "bank_id", "hop", "action_code", "act_mode", "amount_inr", "current_hold_inr",
                  "victim_amount_inr", "other_victims_inr", "balance_now_inr", "trail_amount_inr", "band", "score",
                  "reason_code", "legal_basis", "action_status", "stmt_id")
COMPLAINT_KEYS = ("case_id", "utr", "reported_at", "lane", "victim_tag", "amount_inr", "beneficiary_account",
                  "ticket_kind", "ordering_authority", "ordering_reference", "notice_legal_basis", "fir_reference",
                  "remarks", "corrects_case_id", "complaint_source", "parent_case_id", "from_bank_id", "txn_at",
                  "ack_no")
INTAKE_KEYS = ("match_status", "matched_txn_id", "mismatched", "problems", "near_txn_id", "near_utr", "near_at",
               "near_amount_inr", "related", "related_case_id", "stmt_id")
FMT = "%Y-%m-%d %H:%M:%S"


class StepFailed(RuntimeError):
    """A step could not run; the message says which file and statement, and why."""


def complaint(conn, case_id: str) -> dict:
    row = conn.execute(f"SELECT {', '.join(COMPLAINT_KEYS)} FROM COMPLAINT WHERE case_id = {{case_id}}",
                       {"case_id": case_id}).fetchone()
    if row is None:
        raise StepFailed(f"No ticket {case_id!r} in this bank's inbox (COMPLAINT). The portal delivers tickets when "
                         "the simulated clock reaches them: python -m pipeline.night, or python -m pipeline.agent "
                         f"{case_id}")
    return dict(zip(COMPLAINT_KEYS, row, strict=True))


def intake_result(conn, case_id: str) -> dict | None:
    row = conn.execute(f"SELECT {', '.join(INTAKE_KEYS)} FROM INTAKE_CHECK WHERE case_id = {{c}}",
                       {"c": case_id}).fetchone()
    return dict(zip(INTAKE_KEYS, row, strict=True)) if row else None


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
            raise StepFailed(f"FAILED {file_name} statement {i}: {first}\n{exc}") from exc
    return results


def _audit(conn, event: str, case_id: str, detail: str, sim_at: str | None = None, actor: str = "maker") -> None:
    from pipeline.db import audit

    audit(conn, event, case_id, detail, actor=actor, sim_at=sim_at)


def intake(conn, case_id: str, bank: str, checked_at: str) -> dict:
    """Module 1: exact match, near match, completeness, duplicates (030). Writes INTAKE_CHECK only."""
    complaint(conn, case_id)
    run_step(conn, "030_intake.sql", {"case_id": case_id, "checked_at": checked_at})
    _audit(conn, "intake", case_id, f"bank={bank}", checked_at)
    return intake_result(conn, case_id) or {}


def trace(conn, case_id: str, bank: str, as_of: str | None = None,
          max_hops: int | None = None, window_hours: int | None = None) -> dict:
    c = complaint(conn, case_id)
    params = {
        "case_id": case_id,
        "as_of": as_of or default_as_of(c["reported_at"]),
        "max_hops": int(max_hops or DEFAULTS["max_hops"]),
        "window_hours": int(window_hours or DEFAULTS["window_hours"]),
        "own_bank_id": bank,
    }
    started = time.perf_counter()
    run_step(conn, "040_trace.sql", params)
    elapsed = time.perf_counter() - started
    _audit(conn, "trace", case_id, f"bank={bank} as_of={params['as_of']} hops={params['max_hops']}")
    return {**params, "seconds": elapsed}


def _ts(value) -> str:
    return str(value)[:19]


def _replay_from(conn, case_id: str) -> str:
    """Where the attribution replay starts: the last balance snapshot of the traced accounts at or before the ticket's
    payment, or one second before the payment when there is none."""
    root_ts = _ts(conn.execute("SELECT MIN(t.ts) FROM TXN t JOIN INTAKE_CHECK k ON k.matched_txn_id = t.txn_id "
                               "WHERE k.case_id = {c}", {"c": case_id}).fetchval())
    snapshot = conn.execute(
        "SELECT MAX(ts) FROM BALANCE_SNAPSHOT WHERE ts <= TO_TIMESTAMP({r}, 'YYYY-MM-DD HH24:MI:SS') "
        "AND account_id IN (SELECT to_account FROM TRACE_EDGE WHERE case_id = {c})",
        {"r": root_ts, "c": case_id},
    ).fetchval()
    return _ts(snapshot) if snapshot else (datetime.strptime(root_ts, FMT) - timedelta(seconds=1)).strftime(FMT)


def attribute(conn, case_id: str, bank: str, as_of: str | None = None, rule: str | None = None) -> dict:
    """Step 2. Uses the accounts the trace reached, so run trace first with the same as_of."""
    c = complaint(conn, case_id)
    as_of = as_of or default_as_of(c["reported_at"])
    traced = conn.execute("SELECT COUNT(*) FROM TRACE_EDGE WHERE case_id = {c}", {"c": case_id}).fetchval()
    if not traced:
        raise StepFailed(f"No trace for {case_id}. Run: python -m pipeline.steps trace {case_id}")
    replay_from = _replay_from(conn, case_id)
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
    _audit(conn, "attribute", case_id, f"bank={bank} as_of={as_of} replay_from={replay_from}")
    return {**params, "seconds": elapsed}


def score(conn, case_id: str, bank: str, as_of: str | None = None, targets: bool = True) -> dict:
    """Step 3: the account evidence. targets=True scores the trail's own accounts (055); the watch fills
    SCORE_TARGET itself for one account and passes targets=False."""
    if as_of is None:
        as_of = default_as_of(complaint(conn, case_id)["reported_at"])
    params = {
        "case_id": case_id,
        "as_of": as_of,
        "mule_band": str(SCORING["bands"]["mule"]),
        "review_band": str(SCORING["bands"]["review"]),
    }
    started = time.perf_counter()
    if targets:
        run_step(conn, "055_score_target.sql", {"case_id": case_id, "own_bank_id": bank})
    run_step(conn, "060_score.sql", params)
    elapsed = time.perf_counter() - started
    _audit(conn, "score", case_id, f"as_of={params['as_of']}")
    return {**params, "seconds": elapsed}


def actions(conn, case_id: str, bank: str) -> dict:
    """The rules engine (070). Uses trace, attribution, referrals and account evidence.
    Writes CASE_ACCOUNT and CASE_ACTION: what the facts allow. Places nothing."""
    params = {
        "case_id": case_id,
        "own_bank_id": bank,
        "basis_hold": RULES["legal_basis"]["hold"],
        "basis_narrow": RULES["legal_basis"]["narrow"],
        "basis_freeze": RULES["legal_basis"]["freeze"],
        "basis_trail": RULES["legal_basis"]["trail"],
        "freeze_links_min": str(RULES["full_freeze_evidence"]["complaint_links_min"]),
    }
    started = time.perf_counter()
    run_step(conn, "070_actions.sql", params)
    elapsed = time.perf_counter() - started
    _audit(conn, "actions", case_id, f"bank={bank}")
    return {**params, "seconds": elapsed}


def check(conn, case_id: str, bank: str) -> list[dict]:
    """The checker's case-level checks (075). Run on the checker's connection. Any failure stops every change."""
    # the same window the attribution replayed, so conservation counts exactly the victim payments it was given
    as_of = conn.execute("SELECT MAX(as_of) FROM ATTRIBUTION WHERE case_id = {c}", {"c": case_id}).fetchval()
    as_of = _ts(as_of) if as_of else default_as_of(complaint(conn, case_id)["reported_at"])
    run_step(conn, "075_check.sql", {"case_id": case_id, "own_bank_id": bank, "as_of": as_of,
                                     "replay_from": _replay_from(conn, case_id)})
    _audit(conn, "check", case_id, f"bank={bank}", actor="checker")
    return checks(conn, case_id, bank)


def case_actions(conn, case_id: str, bank: str) -> list[dict]:
    rows = conn.execute(
        f"SELECT {', '.join(ACTION_COLUMNS)} FROM CASE_ACTION "
        "WHERE case_id = {c} AND viewing_bank_id = {b} ORDER BY hop, account_id, act_mode, action_code",
        {"c": case_id, "b": bank}).fetchall()
    return [dict(zip(ACTION_COLUMNS, r, strict=True)) for r in rows]


def checks(conn, case_id: str, bank: str) -> list[dict]:
    rows = conn.execute(
        "SELECT check_name, check_passed, detail, stmt_id FROM CHECK_RESULT "
        "WHERE case_id = {c} AND viewing_bank_id = {b} ORDER BY check_name", {"c": case_id, "b": bank}).fetchall()
    return [{"check_name": n, "check_passed": bool(p), "detail": d, "stmt_id": s} for n, p, d, s in rows]


def execute(conn, file_name: str, case_id: str, bank: str, placed_at: str, account_id: str,
            proposal_id: int) -> None:
    """Carry out one checker action (081..084) for one validated proposal. The SQL itself refuses to act unless the
    case's checks passed. Only the checker's connection has the privileges these statements need."""
    run_step(conn, file_name, {"case_id": case_id, "own_bank_id": bank, "placed_at": placed_at,
                               "account_id": account_id, "proposal_id": str(int(proposal_id))})


def print_actions(conn, case_id: str, bank: str) -> None:
    from pipeline.explain import explain, headline

    for a in case_actions(conn, case_id, bank):
        if a["act_mode"] == "INFO":
            continue
        who = "agent  " if a["act_mode"] == "AUTOMATIC" else "officer"
        print(f"{a['account_id']:24} {who}  {headline(a):44} [{a['action_status']}]  (statement {a['stmt_id']})")
        for line in explain(a):
            print(f"    - {line}")


def print_trace(conn, case_id: str) -> None:
    rows = conn.execute(
        "SELECT hop, from_account, to_account, amount_inr, ts, is_own_bank, is_exit, is_cycle, stmt_id "
        "FROM TRACE_EDGE WHERE case_id = {case_id} ORDER BY hop, ts, txn_id",
        {"case_id": case_id},
    ).fetchall()
    print(f"{'hop':>3}  {'from':14} {'to':24} {'amount (Rs)':>13}  {'time':19}  where      stmt")
    for hop, frm, to, amount, ts, own, is_exit, cycle, stmt in rows:
        where = "exit" if is_exit else ("own bank" if own else "other bank")
        flag = "  LOOP" if cycle else ""
        print(f"{int(hop):>3}  {frm:14} {to:24} {float(amount):>13,.2f}  {str(ts)[:19]:19}  {where:10} {stmt}{flag}")
    print(f"{len(rows)} edges")


def print_attribution(conn, case_id: str, bank: str) -> None:
    rows = conn.execute(
        "SELECT account_id, trace_rule, victim_amount_inr, other_victims_inr, own_funds_inr, stmt_id "
        "FROM ATTRIBUTION WHERE case_id = {c}", {"c": case_id}).fetchall()
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
        "SELECT from_account, bank_id, account_id, amount_inr, victim_amount_inr, trace_rule "
        "FROM REFERRAL_OUT WHERE case_id = {c} AND from_bank_id = {b} ORDER BY ts",
        {"c": case_id, "b": bank}).fetchall()
    if refs:
        print(f"\nonward entries from {bank} (filed on the portal; each becomes that bank's ticket):")
        for fa, to_bank, acc, amount, victim, rule in refs:
            print(f"  {fa} -> {to_bank} {acc}: Rs {float(amount):,.2f} sent, "
                  f"Rs {float(victim):,.2f} of it the victim's ({rule})")


def print_score(conn, case_id: str) -> None:
    scores = conn.execute(
        "SELECT account_id, score, band, stmt_id FROM ACCOUNT_SCORE WHERE case_id = {c} ORDER BY account_id",
        {"c": case_id}).fetchall()
    for acc, value, band, stmt in scores:
        rank_col = "mule_rank" if band in ("MULE", "REVIEW") else "honest_rank"
        reasons = conn.execute(
            f"SELECT sentence FROM ACCOUNT_SIGNALS WHERE case_id = {{c}} AND account_id = {{a}} "
            f"AND {rank_col} <= {int(SCORING['top_reasons'])} ORDER BY {rank_col}",
            {"c": case_id, "a": acc}).fetchall()
        print(f"{acc:14} {band:6} score {float(value):.2f}   (statement {stmt})")
        for (sentence,) in reasons:
            print(f"    - {sentence}")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("step", choices=["intake", "trace", "attribute", "score", "actions", "run"])
    ap.add_argument("case_id")
    ap.add_argument("--bank", help="bank whose schema to use (default: OWN_BANK_ID in .env)")
    ap.add_argument("--as-of", help='replay cut-off, "YYYY-MM-DD HH:MM:SS" (default: reported_at + hold_minutes)')
    ap.add_argument("--max-hops", type=int)
    ap.add_argument("--window-hours", type=int)
    ap.add_argument("--rule", choices=["LIBR", "FIFO", "PRO_RATA"], help="rule for onward shares (default LIBR)")
    args = ap.parse_args(argv)

    from pipeline.db import connect, own_bank

    bank = args.bank or own_bank()
    conn = connect("maker", bank)
    try:
        _run_cli(conn, args, bank)
    except StepFailed as exc:
        print(exc, file=sys.stderr)
        return 1
    finally:
        conn.close()
    return 0


def _run_cli(conn, args, bank: str) -> None:
    c = complaint(conn, args.case_id)
    as_of = args.as_of or default_as_of(c["reported_at"])
    if args.step in ("intake", "run"):
        r = intake(conn, args.case_id, bank, str(c["reported_at"])[:19])
        print(f"intake {args.case_id}: {r.get('match_status')} (related: {r.get('related')} "
              f"{r.get('related_case_id') or ''})")
        for key in ("mismatched", "problems"):
            if r.get(key):
                print(f"  {key}: {r[key]}")
        if r.get("near_txn_id"):
            print(f"  near match: {r['near_txn_id']} UTR {r['near_utr']} at {r['near_at']} Rs {r['near_amount_inr']}")
    if args.step in ("trace", "run"):
        info = trace(conn, args.case_id, bank, as_of, args.max_hops, args.window_hours)
        print(f"trace {args.case_id}  at {bank}  as_of {info['as_of']}  "
              f"max {info['max_hops']} hops / {info['window_hours']} h  took {info['seconds']:.2f}s\n")
        print_trace(conn, args.case_id)
    if args.step in ("attribute", "run"):
        info = attribute(conn, args.case_id, bank, as_of, args.rule)
        print(f"\nattribute {args.case_id}  victim {info['victim_tag']}  as_of {info['as_of']}  "
              f"replay from {info['replay_from']}  took {info['seconds']:.2f}s\n")
        print_attribution(conn, args.case_id, bank)
    if args.step in ("score", "run"):
        info = score(conn, args.case_id, bank, as_of)
        print(f"\nscore {args.case_id}  as_of {info['as_of']}  took {info['seconds']:.2f}s\n")
        print_score(conn, args.case_id)
    if args.step in ("actions", "run"):
        info = actions(conn, args.case_id, bank)
        print(f"\nactions {args.case_id}  at {bank}  took {info['seconds']:.2f}s"
              "\n(nothing is held here: the checker carries out the automatic actions)\n")
        print_actions(conn, args.case_id, bank)


if __name__ == "__main__":
    sys.exit(main())