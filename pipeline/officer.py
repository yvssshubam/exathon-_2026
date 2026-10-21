"""What the officer decides (docs/DESIGN.md, "Automatic vs officer"). The agent proposes; only this module, called
with an officer's name, can narrow a hold, freeze a whole account or send a KYC packet.

    python -m pipeline.officer waiting                                         # what is waiting, every case
    python -m pipeline.officer approve CASE_DEMO ACC_D PROPOSE_NARROW --officer priya
    python -m pipeline.officer approve CASE_DEMO ACC_C PROPOSE_FULL_FREEZE --officer priya \\
        --comment "Fourteen days old, forwards within minutes, two cash-outs: the pattern in the reasons"
    python -m pipeline.officer reject CASE_DEMO ACC_A PROPOSE_FULL_FREEZE --officer priya --reason "Wait for the IO"
    python -m pipeline.officer send-kyc CASE_DEMO 7 --officer priya

Rules enforced: an officer name is always required; rejecting needs a reason; so does editing an amount and
approving a full freeze (courts ask for the material behind a whole-account freeze). A narrowed hold can only be
smaller than the mandatory hold and at least the victims' money. Decisions are append-only; the hold ledger keeps
the replaced hold, so the history of every account is visible.
"""
from __future__ import annotations

import argparse
import sys
from decimal import Decimal

PROPOSALS = ("PROPOSE_NARROW", "PROPOSE_FULL_FREEZE", "CONFIRM_HOLD")


class DecisionError(ValueError):
    """A decision the rules do not allow; the message says why in plain words."""


def _proposal(conn, case_id: str, bank: str, account_id: str, action_code: str) -> dict:
    if action_code not in PROPOSALS:
        raise DecisionError(f"{action_code} is not something an officer decides here; automatic actions are done "
                            f"by the agent and judgment calls are {', '.join(PROPOSALS)}.")
    row = conn.execute(
        "SELECT amount_inr, current_hold_inr, victim_amount_inr, other_victims_inr, balance_now_inr, action_status, "
        "stmt_id FROM PH.CASE_ACTION WHERE case_id = {c} AND viewing_bank_id = {b} AND account_id = {a} "
        "AND action_code = {k} AND act_mode = 'OFFICER'",
        {"c": case_id, "b": bank, "a": account_id, "k": action_code}).fetchone()
    if row is None:
        raise DecisionError(f"No {action_code} proposal for {account_id} in {case_id}. The agent may not have worked "
                            f"the ticket yet.")
    keys = ("amount_inr", "current_hold_inr", "victim_amount_inr", "other_victims_inr", "balance_now_inr",
            "action_status", "stmt_id")
    p = dict(zip(keys, row, strict=True))
    if p["action_status"] in ("APPROVED", "EDITED", "REJECTED"):
        raise DecisionError(f"This proposal was already decided ({p['action_status'].lower()}).")
    return p


def _active_lien(conn, case_id: str, bank: str, account_id: str):
    return conn.execute(
        "SELECT hold_id, amount_inr FROM PH.HOLD_LEDGER WHERE case_id = {c} AND viewing_bank_id = {b} "
        "AND account_id = {a} AND hold_kind = 'LIEN' AND hold_status = 'ACTIVE' ORDER BY hold_id DESC LIMIT 1",
        {"c": case_id, "b": bank, "a": account_id}).fetchone()


def _statement_ids(conn, case_id: str, account_id: str, proposal: dict) -> str:
    att = conn.execute("SELECT MAX(stmt_id) FROM PH.ATTRIBUTION WHERE case_id = {c} AND account_id = {a}",
                       {"c": case_id, "a": account_id}).fetchval()
    sc = conn.execute("SELECT MAX(stmt_id) FROM PH.ACCOUNT_SCORE WHERE case_id = {c} AND account_id = {a}",
                      {"c": case_id, "a": account_id}).fetchval()
    return f"actions:{proposal['stmt_id']} attribute:{att} evidence:{sc}"


def _record(conn, **v) -> int:
    conn.execute(
        "INSERT INTO PH.DECISION (case_id, viewing_bank_id, account_id, action_code, decision_outcome, amount_inr, "
        "proposed_amount, decided_by, decided_at, reason_text, statement_ids) VALUES ({case_id}, {bank}, "
        "{account_id}, {action}, {outcome}, {amount}, {proposed}, {officer}, CURRENT_TIMESTAMP, {reason}, {stmts})", v)
    return int(conn.execute(
        "SELECT MAX(decision_id) FROM PH.DECISION WHERE case_id = {case_id} AND account_id = {account_id} "
        "AND viewing_bank_id = {bank}", v).fetchval())


def _set_status(conn, case_id: str, bank: str, account_id: str, action_code: str, status: str) -> None:
    conn.execute("UPDATE PH.CASE_ACTION SET action_status = {s} WHERE case_id = {c} AND viewing_bank_id = {b} "
                 "AND account_id = {a} AND action_code = {k} AND act_mode = 'OFFICER'",
                 {"s": status, "c": case_id, "b": bank, "a": account_id, "k": action_code})


def _place(conn, case_id: str, bank: str, account_id: str, kind: str, amount, officer: str, action_code: str,
           reason: str | None) -> int:
    conn.execute(
        "INSERT INTO PH.HOLD_LEDGER (case_id, viewing_bank_id, account_id, hold_kind, amount_inr, placed_at, "
        "placed_by, hold_status, action_code, reason_text, stmt_id) VALUES ({c}, {b}, {a}, {k}, {amt}, "
        "CURRENT_TIMESTAMP, {o}, 'ACTIVE', {code}, {r}, CURRENT_STATEMENT)",
        {"c": case_id, "b": bank, "a": account_id, "k": kind, "amt": str(amount), "o": officer, "code": action_code,
         "r": reason})
    return int(conn.execute("SELECT MAX(hold_id) FROM PH.HOLD_LEDGER WHERE case_id = {c} AND account_id = {a}",
                            {"c": case_id, "a": account_id}).fetchval())


def approve(conn, case_id: str, bank: str, account_id: str, action_code: str, officer: str,
            comment: str | None = None, amount: float | None = None) -> int:
    officer, comment = (officer or "").strip(), (comment or "").strip() or None
    if not officer:
        raise DecisionError("An officer name is required: nothing is approved anonymously.")
    p = _proposal(conn, case_id, bank, account_id, action_code)
    proposed = Decimal(str(p["amount_inr"] or 0))
    final = proposed
    if amount is not None:
        if action_code != "PROPOSE_NARROW":
            raise DecisionError("Only a narrowing can be edited: a freeze takes the whole account, a confirmation "
                                "keeps the hold as it is.")
        if not comment:
            raise DecisionError("Write a reason when you change the proposed amount.")
        final = Decimal(str(amount))
    if action_code == "PROPOSE_FULL_FREEZE" and not comment:
        raise DecisionError("A whole-account freeze needs the reasons written down: courts ask for the material "
                            "behind it, not a label.")

    outcome = "EDITED" if final != proposed else "APPROVED"
    if action_code == "PROPOSE_NARROW":
        lien = _active_lien(conn, case_id, bank, account_id)
        if lien is None:
            raise DecisionError("There is no active hold on this account to narrow.")
        held = Decimal(str(lien[1]))
        floor = Decimal(str(p["victim_amount_inr"] or 0)) + Decimal(str(p["other_victims_inr"] or 0))
        if not (floor <= final < held):
            raise DecisionError(f"A narrowed hold must be at least the victims' money ({floor:,.2f}) and less than "
                                f"the current hold ({held:,.2f}).")
    decision_id = _record(conn, case_id=case_id, bank=bank, account_id=account_id, action=action_code,
                          outcome=outcome, amount=str(final), proposed=str(proposed), officer=officer,
                          reason=comment, stmts=_statement_ids(conn, case_id, account_id, p))
    if action_code == "PROPOSE_NARROW":
        new_id = _place(conn, case_id, bank, account_id, "LIEN", final, officer, action_code, comment)
        conn.execute("UPDATE PH.HOLD_LEDGER SET hold_status = 'REPLACED', replaced_by = {n!d} WHERE hold_id = {h!d}",
                     {"n": new_id, "h": int(lien[0])})
    elif action_code == "PROPOSE_FULL_FREEZE":
        _place(conn, case_id, bank, account_id, "ACCOUNT_FREEZE", p["balance_now_inr"], officer, action_code, comment)
    _set_status(conn, case_id, bank, account_id, action_code, outcome)
    from pipeline.db import audit

    audit(conn, "officer_" + outcome.lower(), case_id,
          f"decision={decision_id} account={account_id} action={action_code} by={officer}", actor=officer)
    return decision_id


def reject(conn, case_id: str, bank: str, account_id: str, action_code: str, officer: str,
           reason: str | None) -> int:
    officer, reason = (officer or "").strip(), (reason or "").strip()
    if not officer:
        raise DecisionError("An officer name is required: nothing is rejected anonymously.")
    if not reason:
        raise DecisionError("A rejection needs a written reason.")
    p = _proposal(conn, case_id, bank, account_id, action_code)
    decision_id = _record(conn, case_id=case_id, bank=bank, account_id=account_id, action=action_code,
                          outcome="REJECTED", amount=None, proposed=str(p["amount_inr"] or 0), officer=officer,
                          reason=reason, stmts=_statement_ids(conn, case_id, account_id, p))
    _set_status(conn, case_id, bank, account_id, action_code, "REJECTED")
    from pipeline.db import audit

    audit(conn, "officer_rejected", case_id, f"decision={decision_id} account={account_id} by={officer}",
          actor=officer)
    return decision_id


def send_kyc(conn, case_id: str, bank: str, entry_id: int, officer: str) -> int:
    officer = (officer or "").strip()
    if not officer:
        raise DecisionError("An officer name is required to send a KYC packet.")
    row = conn.execute("SELECT account_id, entry_status FROM PH.PORTAL_ENTRY WHERE entry_id = {e!d} AND case_id = {c} "
                       "AND viewing_bank_id = {b} AND entry_kind = 'KYC'",
                       {"e": entry_id, "c": case_id, "b": bank}).fetchone()
    if row is None:
        raise DecisionError(f"No KYC draft {entry_id} in {case_id}.")
    if row[1] == "FILED":
        raise DecisionError("This KYC packet was already sent.")
    conn.execute("UPDATE PH.PORTAL_ENTRY SET entry_status = 'FILED', filed_at = CURRENT_TIMESTAMP, filed_by = {o} "
                 "WHERE entry_id = {e!d}", {"o": officer, "e": entry_id})
    decision_id = _record(conn, case_id=case_id, bank=bank, account_id=row[0], action="SEND_KYC",
                          outcome="APPROVED", amount=None, proposed=None, officer=officer, reason=None,
                          stmts=f"portal_entry:{entry_id}")
    from pipeline.db import audit

    audit(conn, "kyc_sent", case_id, f"entry={entry_id} by={officer}", actor=officer)
    return decision_id


def waiting(conn, bank: str) -> list[tuple]:
    return conn.execute(
        "SELECT a.case_id, a.account_id, a.action_code, a.amount_inr, a.current_hold_inr FROM PH.CASE_ACTION a "
        "WHERE a.viewing_bank_id = {b} AND a.act_mode = 'OFFICER' AND a.action_status IN ('PROPOSED', 'WAITING') "
        "ORDER BY a.case_id, a.hop, a.account_id", {"b": bank}).fetchall()


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("waiting")
    for name in ("approve", "reject"):
        p = sub.add_parser(name)
        p.add_argument("case_id")
        p.add_argument("account_id")
        p.add_argument("action_code", choices=PROPOSALS)
        p.add_argument("--officer", default="")
        if name == "approve":
            p.add_argument("--comment")
            p.add_argument("--amount", type=float)
        else:
            p.add_argument("--reason")
    k = sub.add_parser("send-kyc")
    k.add_argument("case_id")
    k.add_argument("entry_id", type=int)
    k.add_argument("--officer", default="")
    ap.add_argument("--bank")
    args = ap.parse_args(argv)

    from pipeline.db import connect, settings

    bank = args.bank or settings()["own_bank_id"]
    conn = connect()
    try:
        if args.cmd == "waiting":
            for case_id, acc, code, amount, held in waiting(conn, bank):
                print(f"{case_id:10} {acc:12} {code:20} proposed {amount}  (held now {held})")
        elif args.cmd == "approve":
            d = approve(conn, args.case_id, bank, args.account_id, args.action_code, args.officer, args.comment,
                        args.amount)
            print(f"approved: decision {d}")
        elif args.cmd == "reject":
            d = reject(conn, args.case_id, bank, args.account_id, args.action_code, args.officer, args.reason)
            print(f"rejected: decision {d}")
        else:
            d = send_kyc(conn, args.case_id, bank, args.entry_id, args.officer)
            print(f"KYC sent: decision {d}")
    except DecisionError as exc:
        print(f"Not allowed: {exc}", file=sys.stderr)
        return 2
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())