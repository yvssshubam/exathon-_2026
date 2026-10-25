"""Re-run only what changed (Module 2, idea 4). A ticket's first pass is a snapshot; when a new ticket names an account
an earlier case already reached (a second victim on account A, a corrected ticket), the split of the money held
there changes. The hold does not: the bank cannot refuse the second victim's alert, so that victim gets a hold of
their own, and the money is divided later, at refund, by the investigating officer. So the split is a record handed
forward, and recording it is automatic: it moves no money.

  1. one query finds every case at this bank whose trail shares an own account with the trigger ticket's trail
  2. only those cases are re-attributed (the same UDF, as of now; no model anywhere in the calculation)
  3. the maker proposes RECORD_SPLIT for each shared account that is held; the checker writes a new version of the
     split (VICTIM_SPLIT keeps every earlier version, the reason and the statement ID)
"""
from __future__ import annotations

import json

from pipeline.explain import inr


def propose(conn, case_id: str, account_id: str, action_code: str, act_mode: str, amount, proposed_by: str,
            now: str, reasons: dict, text: str | None = None, run_id: int | None = None) -> int:
    """Insert one proposal as the maker (the only write the maker makes outside its work tables)."""
    conn.execute(
        "INSERT INTO PROPOSAL (case_id, account_id, action_code, act_mode, amount_inr, proposed_by, proposed_at, "
        "run_id, "
        "reasons_json, draft_text, draft_by, proposal_status, stmt_id) VALUES ({c}, {a}, {k}, {m}, {amt}, {by}, "
        "TO_TIMESTAMP({t}, 'YYYY-MM-DD HH24:MI:SS'), {r}, {j}, {txt}, {db}, 'NEW', CURRENT_STATEMENT)",
        {"c": case_id, "a": account_id, "k": action_code, "m": act_mode,
         "amt": None if amount is None else str(amount), "by": proposed_by, "t": now,
         "r": None if run_id is None else str(int(run_id)), "j": json.dumps(reasons, default=str)[:20000],
         "txt": text, "db": "template" if text else None})
    return int(conn.execute("SELECT MAX(proposal_id) FROM PROPOSAL WHERE case_id = {c} AND account_id = {a} "
                            "AND action_code = {k}", {"c": case_id, "a": account_id, "k": action_code}).fetchval())


def related_cases(conn, case_id: str, bank: str) -> list[str]:
    """Every other case at this bank whose trail reached one of this case's own accounts (one query)."""
    return [c for (c,) in conn.execute(
        "SELECT DISTINCT e2.case_id FROM TRACE_EDGE e1 "
        "JOIN ACCOUNT a ON a.account_id = e1.to_account AND a.bank_id = {b} "
        "JOIN TRACE_EDGE e2 ON e2.to_account = e1.to_account AND e2.case_id <> e1.case_id "
        "WHERE e1.case_id = {c} AND NOT e1.is_exit ORDER BY 1", {"c": case_id, "b": bank}).fetchall()]


def shared_held_accounts(conn, case_ids: list[str], bank: str) -> list[str]:
    return [a for (a,) in conn.execute(
        "SELECT DISTINCT e.to_account FROM TRACE_EDGE e "
        "JOIN ACCOUNT a ON a.account_id = e.to_account AND a.bank_id = {b} "
        "JOIN ACTIVE_HOLD h ON h.account_id = e.to_account "
        "WHERE e.case_id IN ({cs}) AND NOT e.is_exit ORDER BY 1", {"cs": case_ids, "b": bank}).fetchall()]


def recalculate(maker, checker, bank: str, trigger_case: str, now: str, trigger_kind: str = "new_victim") -> dict:
    """Recalculate the cases that share an account with trigger_case, and record the new split (via the checker)."""
    from pipeline import steps
    from pipeline.db import audit

    cases = related_cases(maker, trigger_case, bank)
    if not cases:
        return {"recalculated": [], "accounts": [], "text": "no other case shares an account"}
    for case in cases:
        steps.attribute(maker, case, bank, as_of=now)
    accounts = shared_held_accounts(maker, [trigger_case, *cases], bank)
    reason = {"new_victim": f"Ticket {trigger_case} named an account these cases already reached",
              "corrected_ticket": f"Corrected ticket {trigger_case} arrived"}.get(trigger_kind, trigger_kind)
    ids = [propose(maker, trigger_case, acc, "RECORD_SPLIT", "AUTOMATIC", None, "pipeline", now,
                   {"trigger": trigger_kind, "reason": reason, "recalculated": cases})
           for acc in accounts]
    audit(maker, "recalculate", trigger_case, f"re-attributed {', '.join(cases)}; shared held accounts "
                                              f"{', '.join(accounts) or 'none'}", sim_at=now)
    from pipeline.checker import Checker

    result = Checker(checker, bank).process(None, now, ids) if ids else {"text": "no held account to split"}
    held = {a: checker.execute("SELECT liens_inr FROM ACTIVE_HOLD WHERE account_id = {a}", {"a": a}).fetchval()
            for a in accounts}
    text = (f"recalculated {', '.join(cases)} (they share an account with {trigger_case}); "
            + "; ".join(f"{a}: total held {inr(v)}, unchanged" for a, v in held.items()))
    return {"recalculated": cases, "accounts": accounts, "checker": result, "text": text}
    