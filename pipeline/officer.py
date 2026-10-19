"""What the bank officer does with a recommendation (FR-10, FR-11, FR-12, FR-02). Nothing is held until approved here.

    python -m pipeline.officer tickets                                     # FR-02: which notices are incomplete
    python -m pipeline.officer approve CASE_DEMO ACC_C --officer priya     # viewed as OWN_BANK_ID (default BANK_X)
    python -m pipeline.officer approve CASE_DEMO ACC_D --bank BANK_Z --officer kavitha
    python -m pipeline.officer reject CASE_DEMO ACC_A --officer priya --reason "Police asked us to wait"
    python -m pipeline.officer grievance 3 --received "2026-10-20 11:00:00"
    python -m pipeline.officer deadlines
    python -m pipeline.officer notice 3 --lang ta

Rules enforced (AT-09): an officer name is always required; rejecting needs a reason; so do editing the amount,
holding a whole account, and approving a REVIEW-band account. Exits, downstream accounts and "no action" rows
cannot be approved. Decisions are append-only.
"""
from __future__ import annotations

import argparse
import sys
from datetime import datetime
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
BANKS = yaml.safe_load((ROOT / "config" / "banks.yaml").read_text(encoding="utf-8"))
LANES_CFG = yaml.safe_load((ROOT / "config" / "lanes.yaml").read_text(encoding="utf-8"))
FMT = "%Y-%m-%d %H:%M:%S"
APPROVABLE = ("AMOUNT_HOLD", "ACCOUNT_HOLD", "HOLD_MARKER", "REFER_OUT")
HOLDS = ("AMOUNT_HOLD", "ACCOUNT_HOLD", "HOLD_MARKER")


class DecisionError(ValueError):
    """A decision the rules do not allow; the message says why in plain words."""


def _ts(value) -> str:
    return str(value)[:19]


def _hold_time(conn, case_id: str) -> str:
    at = conn.execute("SELECT MAX(as_of) FROM PH.ATTRIBUTION WHERE case_id = {c}", {"c": case_id}).fetchval()
    if at is None:
        raise DecisionError(f"No attribution for {case_id}. Run: python -m pipeline.steps run {case_id}")
    return _ts(at)


def _recommendation(conn, case_id: str, bank: str, account_id: str) -> dict:
    from pipeline.steps import recommendations

    for rec in recommendations(conn, case_id, bank):
        if rec["account_id"] == account_id:
            return rec
    raise DecisionError(f"No recommendation for {account_id} in {case_id} viewed as {bank}. "
                        f"Run: python -m pipeline.steps run {case_id} --bank {bank}")


def _statement_ids(conn, case_id: str, account_id: str, rec: dict) -> str:
    att = conn.execute("SELECT MAX(stmt_id) FROM PH.ATTRIBUTION WHERE case_id = {c} AND account_id = {a}",
                       {"c": case_id, "a": account_id}).fetchval()
    sc = conn.execute("SELECT MAX(stmt_id) FROM PH.ACCOUNT_SCORE WHERE case_id = {c} AND account_id = {a}",
                      {"c": case_id, "a": account_id}).fetchval()
    return f"recommend:{rec['stmt_id']} attribute:{att} score:{sc}"


def _insert_decision(conn, **v) -> int:
    conn.execute(
        "INSERT INTO PH.DECISION (case_id, viewing_bank_id, account_id, action_code, amount_inr, recommended_action, "
        "recommended_amount, lane, decided_by, decided_at, reason_text, statement_ids) VALUES ({case_id}, {bank}, "
        "{account_id}, {action}, {amount}, {rec_action}, {rec_amount}, {lane}, {officer}, "
        "TO_TIMESTAMP({at}, 'YYYY-MM-DD HH24:MI:SS'), {reason}, {stmts})", v)
    return int(conn.execute(
        "SELECT MAX(decision_id) FROM PH.DECISION WHERE case_id = {case_id} AND account_id = {account_id} "
        "AND viewing_bank_id = {bank}", v).fetchval())


def add_deadlines(conn, decision_id: int, lane: str, anchor: str, at: str) -> None:
    """Start the clocks anchored to `anchor` at time `at`, then any clock anchored to those (<rule>_due)."""
    params = {"d": decision_id, "lane": lane, "anchor": anchor, "at": at}
    conn.execute(
        "INSERT INTO PH.DEADLINE (decision_id, lane, rule_name, anchor_at, due_at, rule_text, deadline_status, "
        "stmt_id) SELECT {d}, r.lane, r.rule_name, TO_TIMESTAMP({at}, 'YYYY-MM-DD HH24:MI:SS'), "
        "ADD_DAYS(TO_TIMESTAMP({at}, 'YYYY-MM-DD HH24:MI:SS'), r.days), r.rule_text, 'OPEN', CURRENT_STATEMENT "
        "FROM PH.LANE_RULE r WHERE r.lane = {lane} AND r.anchor = {anchor}", params)
    for _ in range(4):                      # chains such as officer decision -> district -> state review
        conn.execute(
            "INSERT INTO PH.DEADLINE (decision_id, lane, rule_name, anchor_at, due_at, rule_text, deadline_status, "
            "stmt_id) SELECT {d}, r.lane, r.rule_name, d.due_at, ADD_DAYS(d.due_at, r.days), r.rule_text, 'OPEN', "
            "CURRENT_STATEMENT FROM PH.LANE_RULE r "
            "JOIN PH.DEADLINE d ON d.decision_id = {d} AND d.lane = r.lane AND r.anchor = d.rule_name || '_due' "
            "LEFT JOIN PH.DEADLINE x ON x.decision_id = {d} AND x.rule_name = r.rule_name "
            "WHERE x.rule_name IS NULL", params)


def _notice_facts(conn, decision_id: int) -> dict:
    from pipeline.explain import inr

    d = conn.execute(
        "SELECT d.case_id, d.viewing_bank_id, d.account_id, d.action_code, d.amount_inr, d.lane, d.decided_at, "
        "a.holder_name, c.ordering_authority, c.ordering_reference "
        "FROM PH.DECISION d JOIN PH.ACCOUNT a ON a.account_id = d.account_id "
        "JOIN PH.COMPLAINT c ON c.case_id = d.case_id WHERE d.decision_id = {d}", {"d": decision_id}).fetchone()
    case_id, bank, account_id, action, amount, lane, decided_at, holder, authority, reference = d
    edge = conn.execute(
        "SELECT t.utr, t.ts, t.amount_inr FROM PH.TRACE_EDGE e JOIN PH.TXN t ON t.txn_id = e.txn_id "
        "WHERE e.case_id = {c} AND e.to_account = {a} ORDER BY t.ts LIMIT 1",
        {"c": case_id, "a": account_id}).fetchone()
    rec = _recommendation(conn, case_id, bank, account_id)
    respond_by = conn.execute(
        "SELECT MIN(due_at) FROM PH.DEADLINE WHERE decision_id = {d} AND rule_name = 'customer_explanation'",
        {"d": decision_id}).fetchval()
    signals = conn.execute(
        "SELECT sentence FROM PH.ACCOUNT_SIGNALS WHERE case_id = {c} AND account_id = {a} AND mule_rank <= 3 "
        "ORDER BY mule_rank", {"c": case_id, "a": account_id}).fetchall()
    when = datetime.strptime(_ts(edge[1]), FMT) if edge else None
    bank_cfg = BANKS.get(bank, {})
    return {
        "holder_name": holder or account_id,
        "bank_name": bank_cfg.get("name", bank),
        "notice_date": datetime.strptime(_ts(decided_at), FMT).strftime("%d-%m-%Y"),
        "hold_ref": f"PH-{decision_id}",
        "account_id": account_id,
        "txn_time": when.strftime("%d-%m-%Y %H:%M") if when else "",
        "txn_amount": inr(edge[2]).replace("Rs ", "₹") if edge else "",
        "txn_utr": edge[0] if edge else "",
        "hold_amount": inr(amount).replace("Rs ", "₹"),
        "scope": "account" if action == "ACCOUNT_HOLD" else "amount",
        "lane": lane,
        "authority": authority or "the police unit named in the hold request",
        "reference": reference or "-",
        "legal_basis": rec["legal_basis"] or "",
        "respond_by": datetime.strptime(_ts(respond_by), FMT).strftime("%d-%m-%Y") if respond_by else "",
        "grievance_officer": bank_cfg.get("grievance_officer", "Nodal Grievance Officer"),
        "grievance_contact": bank_cfg.get("grievance_contact", ""),
        "signals": " ".join(s for (s,) in signals),
    }


def write_notices(conn, decision_id: int) -> None:
    from pipeline.notices import LANGS, render

    facts = _notice_facts(conn, decision_id)
    for lang in LANGS:
        conn.execute(
            "INSERT INTO PH.NOTICE (decision_id, lang, notice_body, sent_at) SELECT {d}, {lang}, {body}, decided_at "
            "FROM PH.DECISION WHERE decision_id = {d}", {"d": decision_id, "lang": lang, "body": render(lang, facts)})


def approve(conn, case_id: str, bank: str, account_id: str, officer: str, comment: str | None = None,
            amount: float | None = None, at: str | None = None) -> int:
    officer, comment = (officer or "").strip(), (comment or "").strip() or None
    if not officer:
        raise DecisionError("An officer name is required: nothing is approved anonymously.")
    rec = _recommendation(conn, case_id, bank, account_id)
    action = rec["action_code"]
    if action not in APPROVABLE:
        raise DecisionError(f"Nothing to approve for {account_id}: the recommendation is {action} "
                            f"({rec['reason_code']}).")
    if rec["needs_comment"] and not comment:
        raise DecisionError("This account is in the REVIEW band: write a comment explaining the approval.")
    if action == "ACCOUNT_HOLD" and not comment:
        raise DecisionError("A whole-account hold is a last resort: write the reason for approving it.")
    final = rec["amount_inr"]
    if amount is not None and action in ("AMOUNT_HOLD", "ACCOUNT_HOLD"):
        if not comment:
            raise DecisionError("Write a reason when you change the recommended amount.")
        if amount <= 0 or amount > float(rec["balance_now_inr"] or 0):
            raise DecisionError(f"The amount must be above zero and at most the balance "
                                f"({float(rec['balance_now_inr'] or 0):,.2f}).")
        final = amount
    at = at or _hold_time(conn, case_id)
    decision_id = _insert_decision(
        conn, case_id=case_id, bank=bank, account_id=account_id, action=action, amount=final,
        rec_action=action, rec_amount=rec["amount_inr"], lane=rec["lane"], officer=officer, at=at,
        reason=comment, stmts=_statement_ids(conn, case_id, account_id, rec))
    if action in HOLDS:
        add_deadlines(conn, decision_id, rec["lane"], "hold_placed", at)
    if action in ("AMOUNT_HOLD", "ACCOUNT_HOLD"):
        write_notices(conn, decision_id)
    from pipeline.db import audit

    audit(conn, "approve", case_id, f"decision={decision_id} account={account_id} action={action} by={officer}",
          actor=officer)
    return decision_id


def reject(conn, case_id: str, bank: str, account_id: str, officer: str, reason: str | None,
           at: str | None = None) -> int:
    officer, reason = (officer or "").strip(), (reason or "").strip()
    if not officer:
        raise DecisionError("An officer name is required: nothing is rejected anonymously.")
    if not reason:
        raise DecisionError("A rejection needs a written reason.")
    rec = _recommendation(conn, case_id, bank, account_id)
    decision_id = _insert_decision(
        conn, case_id=case_id, bank=bank, account_id=account_id, action="REJECTED", amount=None,
        rec_action=rec["action_code"], rec_amount=rec["amount_inr"], lane=rec["lane"], officer=officer,
        at=at or _hold_time(conn, case_id), reason=reason, stmts=_statement_ids(conn, case_id, account_id, rec))
    from pipeline.db import audit

    audit(conn, "reject", case_id, f"decision={decision_id} account={account_id} by={officer}", actor=officer)
    return decision_id


def grievance(conn, decision_id: int, received_at: str) -> None:
    """The holder complained at the branch. Lane A clocks start (forwarding is projected same-day, DECISIONS D-12)."""
    lane = conn.execute("SELECT lane FROM PH.DECISION WHERE decision_id = {d}", {"d": decision_id}).fetchval()
    if lane is None:
        raise DecisionError(f"No decision {decision_id}.")
    add_deadlines(conn, decision_id, lane, "grievance_received", received_at)
    add_deadlines(conn, decision_id, lane, "grievance_forwarded", received_at)
    from pipeline.db import audit

    audit(conn, "grievance_received", None, f"decision={decision_id} at={received_at}")


def deadlines(conn, now: datetime | None = None) -> list[dict]:
    now = now or datetime.now()
    warn = int(LANES_CFG.get("warn_when_due_within_days", 3))
    rows = conn.execute(
        "SELECT d.decision_id, c.case_id, c.viewing_bank_id, c.account_id, d.lane, d.rule_name, d.due_at, "
        "d.rule_text, d.deadline_status FROM PH.DEADLINE d JOIN PH.DECISION c ON c.decision_id = d.decision_id "
        "ORDER BY d.due_at, d.decision_id").fetchall()
    out = []
    for r in rows:
        due = datetime.strptime(_ts(r[6]), FMT)
        days_left = (due - now).total_seconds() / 86400
        out.append({"decision_id": int(r[0]), "case_id": r[1], "bank": r[2], "account_id": r[3], "lane": r[4],
                    "rule_name": r[5], "due_at": _ts(r[6]), "text": r[7], "status": r[8],
                    "days_left": days_left, "due_soon": r[8] == "OPEN" and days_left <= warn})
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("tickets")
    for name in ("approve", "reject"):
        p = sub.add_parser(name)
        p.add_argument("case_id")
        p.add_argument("account_id")
        p.add_argument("--officer", required=False, default="")
        p.add_argument("--bank")
        p.add_argument("--at", help='decision time "YYYY-MM-DD HH:MM:SS" (default: the case hold time)')
        if name == "approve":
            p.add_argument("--comment")
            p.add_argument("--amount", type=float)
        else:
            p.add_argument("--reason")
    g = sub.add_parser("grievance")
    g.add_argument("decision_id", type=int)
    g.add_argument("--received", required=True)
    sub.add_parser("deadlines")
    n = sub.add_parser("notice")
    n.add_argument("decision_id", type=int)
    n.add_argument("--lang", default="en", choices=["en", "hi", "ta"])
    args = ap.parse_args(argv)

    from pipeline.db import connect, settings

    conn = connect()
    try:
        if args.cmd == "tickets":
            for case_id, status, problems in conn.execute(
                    "SELECT case_id, notice_status, problems FROM PH.COMPLAINT_CHECK ORDER BY case_id").fetchall():
                print(f"{case_id:10} {status:10} {problems or ''}")
        elif args.cmd in ("approve", "reject"):
            bank = args.bank or settings()["own_bank_id"]
            if args.cmd == "approve":
                d = approve(conn, args.case_id, bank, args.account_id, args.officer, args.comment, args.amount,
                            args.at)
                print(f"approved: decision {d} (hold reference PH-{d}). Notices written in en, hi, ta where a hold "
                      f"was placed. See: python -m pipeline.officer notice {d} --lang ta")
            else:
                d = reject(conn, args.case_id, bank, args.account_id, args.officer, args.reason, args.at)
                print(f"rejected: decision {d}")
        elif args.cmd == "grievance":
            grievance(conn, args.decision_id, args.received)
            print(f"grievance recorded for decision {args.decision_id}; clocks started")
        elif args.cmd == "deadlines":
            for d in deadlines(conn):
                flag = "  <-- due within 3 days" if d["due_soon"] else ""
                print(f"PH-{d['decision_id']:<4} {d['account_id']:10} lane {d['lane']}  {d['due_at']}  "
                      f"{d['days_left']:6.1f} days  {d['rule_name']:32} {d['text']}{flag}")
        elif args.cmd == "notice":
            body = conn.execute("SELECT notice_body FROM PH.NOTICE WHERE decision_id = {d} AND lang = {l}",
                                {"d": args.decision_id, "l": args.lang}).fetchval()
            print(body or f"No {args.lang} notice for decision {args.decision_id}.")
    except DecisionError as exc:
        print(f"Not allowed: {exc}", file=sys.stderr)
        return 2
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())