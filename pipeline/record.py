"""The hold record (Module 7, idea 12): one record per account the agent touched, for the people who ask later.

The nodal officer is not the one who asks "why only ₹500 on Murugan?". The grievance officer is, when Murugan
contests (7 days to file), the investigating officer verifying it (the bank must answer within 2 days), and the
bank's lawyers if he goes to the High Court. Today each of them rebuilds the case from statements weeks later.

The record is generated from the bank's append-only records, never by a model, so it cannot be wrong in a way the
log isn't. It holds: the notice and the transaction it matched; the path the money took to the account; the
lowest-balance calculation step by step; the rule that fired; who decided (the agent, or the officer by name and
time); every change since; and the Exasol statement ID behind every number. It exports as JSON and as text.

    python -m pipeline.record ACC_D                 # print Murugan's record at OWN_BANK_ID
    python -m pipeline.record ACC_D --json > d.json
"""
from __future__ import annotations

import argparse
import json
import sys
from decimal import Decimal

from pipeline.explain import inr


def _d(v) -> Decimal:
    return Decimal(str(v)) if v is not None else Decimal(0)


def _ts(v) -> str | None:
    return str(v)[:19] if v is not None else None


def _rows(conn, sql: str, params: dict) -> list[dict]:
    stmt = conn.execute(sql, params)
    names = [c.lower() for c in stmt.column_names()]
    return [dict(zip(names, r, strict=True)) for r in stmt.fetchall()]


def libr_steps(conn, case_id: str, account_id: str, victim_tag: str, until: str) -> list[dict]:
    """The lowest-balance calculation for one account, step by step: every payment in or out up to `until`, the
    balance after it, and how much of it was this victim's money (from the attribution's flows, statement IDs
    kept). Clean money leaves first; the victim's claim never exceeds the lowest balance since it arrived."""
    snap = conn.execute("SELECT ts, balance_inr FROM BALANCE_SNAPSHOT WHERE account_id = {a} "
                        "AND ts <= TO_TIMESTAMP({u}, 'YYYY-MM-DD HH24:MI:SS') ORDER BY ts DESC LIMIT 1",
                        {"a": account_id, "u": until}).fetchone()
    start, balance = (_ts(snap[0]), _d(snap[1])) if snap else (None, Decimal(0))
    steps = [{"at": start, "what": "opening balance (last known)", "amount_inr": None, "victim_in_inr": None,
              "balance_inr": balance, "victim_inr": Decimal(0), "stmt_id": None}] if snap else []
    victim = Decimal(0)
    for txn_id, ts, frm, to, amount, channel, flow, stmt in conn.execute(
            "SELECT t.txn_id, t.ts, t.from_account, t.to_account, t.amount_inr, t.channel, f.victim_inr, f.stmt_id "
            "FROM TXN_LIVE t LEFT JOIN ATTRIBUTION_FLOW f ON f.txn_id = t.txn_id AND f.case_id = {c} "
            "AND f.trace_rule = 'LIBR' AND f.victim_tag = {v} "
            "WHERE (t.from_account = {a} OR t.to_account = {a}) "
            "AND t.ts > COALESCE(TO_TIMESTAMP({s}, 'YYYY-MM-DD HH24:MI:SS'), TIMESTAMP '1900-01-01 00:00:00') "
            "AND t.ts <= TO_TIMESTAMP({u}, 'YYYY-MM-DD HH24:MI:SS') ORDER BY t.ts, t.txn_id",
            {"c": case_id, "v": victim_tag, "a": account_id, "s": start, "u": until}).fetchall():
        incoming = to == account_id
        balance += _d(amount) if incoming else -_d(amount)
        moved = _d(flow)
        victim += moved if incoming else -moved
        steps.append({"at": _ts(ts), "txn_id": txn_id, "what": f"{'in from' if incoming else 'out to'} "
                      f"{frm if incoming else to} ({channel})", "amount_inr": _d(amount),
                      "victim_in_inr": moved if incoming else -moved, "balance_inr": balance,
                      "victim_inr": victim, "stmt_id": stmt})
    return steps


def hold_record(conn, bank: str, account_id: str) -> dict:
    """Everything about one account's holds at this bank, from the records, with statement IDs."""
    acc = _rows(conn, "SELECT a.account_id, a.holder_name, a.account_type, a.holder_type, a.opened_on, b.bank_name "
                      "FROM ACCOUNT a LEFT JOIN BANK b ON b.bank_id = a.bank_id WHERE a.account_id = {a} "
                      "AND a.bank_id = {b}", {"a": account_id, "b": bank})
    if not acc:
        raise LookupError(f"{account_id} is not an account at {bank}")
    holds = _rows(conn, "SELECT hold_id, case_id, hold_kind, amount_inr, hold_tag, source_txn_id, placed_at, "
                        "placed_by, "
                        "hold_status, replaced_by, action_code, reason_text, proposal_id, stmt_id FROM HOLD_LEDGER "
                        "WHERE account_id = {a} ORDER BY hold_id", {"a": account_id})
    cases_here = sorted({h["case_id"] for h in holds} | {c for (c,) in conn.execute(
        "SELECT DISTINCT case_id FROM PROPOSAL WHERE account_id = {a}", {"a": account_id}).fetchall()})
    cases = []
    for case in cases_here:
        notice = _rows(conn, "SELECT c.case_id, c.ticket_kind, c.from_bank_id, c.complaint_source, c.ack_no, c.utr, "
                             "c.amount_inr, "
                             "c.txn_at, c.reported_at, c.ordering_authority, c.ordering_reference, "
                             "c.notice_legal_basis, "
                             "c.fir_reference, k.match_status, k.matched_txn_id, k.stmt_id AS intake_stmt_id, "
                             "t.amount_inr AS matched_amount_inr, t.ts AS matched_at FROM COMPLAINT c "
                             "LEFT JOIN INTAKE_CHECK k ON k.case_id = c.case_id LEFT JOIN TXN t ON t.txn_id = "
                             "k.matched_txn_id WHERE c.case_id = {c}", {"c": case})
        if not notice:
            continue
        n = notice[0]
        path = _rows(conn, "SELECT hop, from_account, to_account, txn_id, amount_inr, ts, trail_path, stmt_id "
                           "FROM TRACE_EDGE WHERE case_id = {c} ORDER BY hop, ts", {"c": case})
        into = [e for e in path if e["to_account"] == account_id]
        rule = _rows(conn, "SELECT action_code, act_mode, amount_inr, current_hold_inr, victim_amount_inr, "
                           "other_victims_inr, balance_now_inr, trail_amount_inr, reason_code, legal_basis, stmt_id "
                           "FROM CASE_ACTION WHERE case_id = {c} AND account_id = {a} AND act_mode <> 'INFO' "
                           "ORDER BY act_mode, action_code", {"c": case, "a": account_id})
        first = next((h for h in holds if h["case_id"] == case), None)
        until = _ts(first["placed_at"]) if first else _ts(n["reported_at"])
        victim_tag = conn.execute("SELECT victim_tag FROM COMPLAINT WHERE case_id = {c}", {"c": case}).fetchval()
        steps = libr_steps(conn, case, account_id, victim_tag, until) if into else []
        proposals = _rows(conn, "SELECT proposal_id, action_code, act_mode, amount_inr, proposed_by, proposed_at, "
                                "proposal_status, checker_note, checked_at, draft_text, draft_by, stmt_id "
                                "FROM PROPOSAL "
                                "WHERE case_id = {c} AND account_id = {a} ORDER BY proposal_id",
                          {"c": case, "a": account_id})
        decisions = _rows(conn, "SELECT decision_id, action_code, decision_outcome, amount_inr, proposed_amount, "
                                "decided_by, decided_at, reason_text, draft_text, signed_text, statement_ids FROM "
                                "DECISION WHERE case_id = {c} AND account_id = {a} ORDER BY decision_id",
                          {"c": case, "a": account_id})
        from pipeline.explain import ticket_label

        cases.append({"case_id": case, "label": ticket_label(n), "notice": n, "path_into_account": into,
                      "trail_path": into[0]["trail_path"] if into else None, "rules": rule, "libr_steps": steps,
                      "proposals": proposals, "decisions": decisions})
    splits = _rows(conn, "SELECT version_no, victim_tag, case_id, victim_inr, all_victims_inr, held_inr, "
                         "share_of_held_inr, "
                         "valid_from, superseded_at, trigger_kind, reason_text, stmt_id FROM VICTIM_SPLIT "
                         "WHERE account_id = {a} ORDER BY version_no, victim_tag", {"a": account_id})
    watch = _rows(conn, "SELECT event_at, event_kind, amount_inr, sentence, stmt_id FROM WATCH_EVENT "
                        "WHERE account_id = {a} ORDER BY event_at, event_id", {"a": account_id})
    on_watch = _rows(conn, "SELECT case_id, first_pass, added_at, added_why, left_at, left_why, stmt_id "
                           "FROM WATCH_LIST "
                           "WHERE account_id = {a} ORDER BY added_at", {"a": account_id})
    messages = _rows(conn, "SELECT written_at, message_text, stmt_id FROM HOLDER_MESSAGE WHERE account_id = {a} "
                           "ORDER BY message_id", {"a": account_id})
    audit = _rows(conn, "SELECT event_at, sim_at, actor, event_name, case_id, detail, stmt_id FROM AUDIT_LOG "
                        "WHERE account_id = {a} ORDER BY event_id", {"a": account_id})
    return {"account": acc[0], "bank": bank, "holds": holds, "cases": cases, "splits": splits, "watch_list": on_watch,
            "watch_events": watch, "holder_messages": messages, "audit": audit,
            "held_now_inr": sum((_d(h["amount_inr"]) for h in holds
                                 if h["hold_status"] == "ACTIVE" and h["hold_kind"] == "LIEN"), Decimal(0))}


def as_text(r: dict) -> str:
    """The record as plain text: what a grievance officer attaches to the file."""
    a = r["account"]
    out = [f"HOLD RECORD — {a['holder_name'] or a['account_id']} ({a['account_id']}), {a['bank_name']}",
           f"Held now (amount only): {inr(r['held_now_inr'])}", ""]
    for c in r["cases"]:
        n = c["notice"]
        out.append(f"The {c['label']} — from {n['complaint_source']}, acknowledgement {n['ack_no']} "
                   f"(internal case {c['case_id']})")
        out.append(f"  Notice: {n['ordering_reference'] or '-'} from {n['ordering_authority'] or '-'}, "
                   f"{n['notice_legal_basis'] or '-'}" + (f"; {n['fir_reference']}" if n.get("fir_reference") else ""))
        out.append(f"  Ticket names UTR {n['utr']}, {inr(n['amount_inr'])}; matched: {n['match_status']} "
                   f"{n['matched_txn_id'] or ''} (statement {n['intake_stmt_id']})")
        if c["trail_path"]:
            out.append(f"  Path the money took: {c['trail_path']}")
        if c["libr_steps"]:
            out.append("  Lowest-balance calculation:")
            for s in c["libr_steps"]:
                amt = "" if s["amount_inr"] is None else f"{inr(s['amount_inr']):>14}"
                vin = "" if s["victim_in_inr"] in (None, Decimal(0)) else f" (victim {inr(s['victim_in_inr'])})"
                out.append(f"    {str(s['at'])[:16]}  {s['what'][:40]:40} {amt}{vin}  balance {inr(s['balance_inr'])}"
                           f", victim's money {inr(s['victim_inr'])}" + (f"  [stmt {s['stmt_id']}]" if s["stmt_id"]
                                                                          else ""))
        for rule in c["rules"]:
            out.append(f"  Rule: {rule['action_code']} ({rule['act_mode'].lower()}), {rule['reason_code']}: "
                       f"{inr(rule['amount_inr'])} — {rule['legal_basis'] or ''} [stmt {rule['stmt_id']}]")
        for p in c["proposals"]:
            amount = inr(p["amount_inr"]) if p["amount_inr"] else ""
            out.append(f"  Proposal {p['proposal_id']}: {p['action_code']} {amount}"
                       f" by {p['proposed_by']} at {str(p['proposed_at'])[:16]} → {p['proposal_status']}"
                       f" ({p['checker_note'] or ''}) [stmt {p['stmt_id']}]")
        for d in c["decisions"]:
            out.append(f"  Decision {d['decision_id']}: {d['decision_outcome']} {d['action_code']} "
                       f"{inr(d['amount_inr']) if d['amount_inr'] is not None else ''} by {d['decided_by']} at "
                       f"{str(d['decided_at'])[:16]} [{d['statement_ids']}]")
            if d.get("signed_text") and d["signed_text"] != d.get("draft_text"):
                out.append("    The officer edited the reasons before signing.")
        out.append("")
    out.append("Holds, in order:")
    for h in r["holds"]:
        out.append(f"  PH-H{int(h['hold_id'])} {h['hold_kind']} {inr(h['amount_inr'])} ({h['hold_tag'] or '-'}) "
                   f"placed {str(h['placed_at'])[:16]} by {h['placed_by']}, {h['hold_status']}"
                   + (f" → PH-H{int(h['replaced_by'])}" if h["replaced_by"] else "") + f" [stmt {h['stmt_id']}]")
    if r["splits"]:
        out.append("Split between victims (a record for the refund stage; moves no money):")
        for s in r["splits"]:
            out.append(f"  v{int(s['version_no'])} {s['victim_tag']}: {inr(s['share_of_held_inr'])} of "
                       f"{inr(s['held_inr'])} held ({s['trigger_kind']}{', superseded' if s['superseded_at'] else ''})"
                       f" [stmt {s['stmt_id']}]")
    if r["watch_events"]:
        out.append("Since the first pass:")
        for w in r["watch_events"]:
            out.append(f"  {w['sentence']} [stmt {w['stmt_id']}]")
    if r["holder_messages"]:
        out.append("What the holder was told:")
        for m in r["holder_messages"]:
            out.append(f"  {str(m['written_at'])[:16]} {m['message_text']}")
    return "\n".join(out)


def to_json(r: dict) -> str:
    return json.dumps(r, default=lambda v: float(v) if isinstance(v, Decimal) else str(v), indent=2)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("account_id")
    ap.add_argument("--bank")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)
    from pipeline.db import connect, own_bank

    bank = args.bank or own_bank()
    conn = connect("maker", bank)
    try:
        r = hold_record(conn, bank, args.account_id)
        print(to_json(r) if args.json else as_text(r))
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())