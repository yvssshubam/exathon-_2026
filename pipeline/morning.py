"""The morning log (write_morning_log): what the agent did overnight, which the officer reads at the start of the day.
Only the four decisions need action; everything else is listed as done.

The facts are gathered by fixed code from the bank's own records of the night (the audit log, the holds, the
filings, the watch events and the proposals), each with the statement IDs behind it. The fixed template words them.
With the local model on, the model may reword the template; every number in its text must already be in the facts,
or the template stands (the same guard as every other model text).
"""
from __future__ import annotations

from decimal import Decimal

from pipeline.explain import inr

SYSTEM = (
    "Rewrite this overnight log for a bank's nodal officer starting the day. Keep it short and plain: what was done "
    "automatically, then exactly the decisions waiting for the officer. Use only the facts given; copy every figure "
    "exactly as written; add nothing, total nothing, round nothing; never call anyone fraudulent or a mule.")
DECISION_WORDS = {"PROPOSE_NARROW": "narrow", "PROPOSE_FULL_FREEZE": "full freeze (widen)",
                  "UNMATCHED_NOTICE": "unmatched notice", "PROPOSE_RELEASE": "release"}


def _d(v) -> Decimal:
    return Decimal(str(v)) if v is not None else Decimal(0)


def facts(conn, bank: str, period_from: str, period_to: str) -> dict:
    """Everything the log may say, read from the bank's schema for the period."""
    p = {"f": period_from, "t": period_to}
    window = ("BETWEEN TO_TIMESTAMP({f}, 'YYYY-MM-DD HH24:MI:SS') AND TO_TIMESTAMP({t}, 'YYYY-MM-DD HH24:MI:SS')")
    names = dict(conn.execute("SELECT account_id, COALESCE(holder_name, account_id) FROM ACCOUNT WHERE account_id IN "
                              "(SELECT account_id FROM HOLD_LEDGER UNION SELECT account_id FROM PROPOSAL "
                              "UNION SELECT account_id FROM WATCH_EVENT)").fetchall())
    tickets = conn.execute(f"SELECT case_id, received_at, ticket_status FROM TICKET WHERE received_at {window} "
                           "ORDER BY received_at", p).fetchall()
    holds = conn.execute(f"SELECT case_id, account_id, hold_kind, amount_inr, hold_tag, placed_at, action_code, "
                         f"stmt_id "
                         f"FROM HOLD_LEDGER WHERE placed_by = 'agent' AND placed_at {window} ORDER BY placed_at, "
                         "hold_id", p).fetchall()
    filings = conn.execute(f"SELECT entry_kind, COUNT(*), MIN(filed_at), MAX(stmt_id) FROM PORTAL_ENTRY "
                           f"WHERE filed_at {window} GROUP BY entry_kind ORDER BY entry_kind", p).fetchall()
    watch = conn.execute(f"SELECT event_at, sentence, stmt_id FROM WATCH_EVENT WHERE event_at {window} "
                         "AND event_kind IN ('NEW_MONEY_HELD', 'NEW_MONEY_ABOVE_NOTICE', 'NEW_MONEY_HONEST', "
                         "'CASHOUT_BLOCKED', 'TRANSFER_BLOCKED', 'RECIPIENT_WATCHED', 'RECIPIENT_OTHER_BANK', "
                         "'RECIPIENT_BUSINESS', 'LINKED_TO_VICTIM', 'RELEASE_FLAGGED') ORDER BY event_at, event_id",
                         p).fetchall()
    splits = conn.execute(f"SELECT account_id, version_no, COUNT(*), MAX(held_inr), MAX(stmt_id) FROM VICTIM_SPLIT "
                          f"WHERE valid_from {window} AND version_no > 1 GROUP BY account_id, version_no "
                          "ORDER BY account_id, version_no", p).fetchall()
    refused = conn.execute(f"SELECT case_id, action_code, account_id, checker_note FROM PROPOSAL "
                           f"WHERE proposal_status = 'REFUSED' AND checked_at {window} ORDER BY proposal_id",
                           p).fetchall()
    failed = conn.execute(f"SELECT case_id FROM AGENT_RUN WHERE run_status IN ('CHECK_FAILED', 'FAILED') "
                          f"AND sim_started_at {window}", p).fetchall()
    waiting = conn.execute("SELECT proposal_id, case_id, account_id, action_code, amount_inr FROM PROPOSAL "
                           "WHERE proposal_status = 'WAITING' ORDER BY proposal_id").fetchall()
    return {"names": names, "tickets": tickets, "holds": holds, "filings": filings, "watch": watch,
            "splits": splits, "refused": refused, "failed": failed, "waiting": waiting}


def template(f: dict, bank_name: str, period_to: str) -> str:
    n = f["names"]
    lines = [f"Overnight at {bank_name}, up to {period_to[11:16]} on {period_to[:10]}."]
    if f["tickets"]:
        n_t = len(f["tickets"])
        lines.append(f"Tickets: {n_t} {'arrived and was' if n_t == 1 else 'arrived and were'} worked the minute "
                     f"{'it' if n_t == 1 else 'they'} landed ("
                     + ", ".join(f"{c} at {str(t)[11:16]}" for c, t, _ in f["tickets"]) + ").")
    done = []
    for case, acc, kind, amount, tag, at, code, _stmt in f["holds"]:
        who = n.get(acc, acc)
        if kind == "MARKER":
            done.append(f"{str(at)[11:16]} {who}: marked to catch later credits ({case})")
        elif tag == "NOT_LINKED":
            done.append(f"{str(at)[11:16]} {who}: {inr(amount)} of new money held, not linked to a reported victim "
                        f"yet ({case})")
        elif code == "LINKED_HOLD":
            done.append(f"{str(at)[11:16]} {who}: {inr(amount)} already held, now linked to the victim of {case}")
        else:
            done.append(f"{str(at)[11:16]} {who}: {inr(amount)} held, amount only ({case})")
    if done:
        lines.append("Done automatically: " + "; ".join(done) + ".")
    if f["filings"]:
        words = {"ONWARD": ("onward entry", "onward entries"), "EXIT": ("exit", "exits"),
                 "KYC": ("KYC packet", "KYC packets"), "SEND_BACK": ("send-back", "send-backs")}

        def count(k, cnt):
            one, many = words.get(k, (k.lower(), k.lower()))
            return f"{int(cnt)} {one if int(cnt) == 1 else many}"

        lines.append("Filed on the portal, not left as drafts: "
                     + ", ".join(count(k, cnt) for k, cnt, _, _ in f["filings"]) + ".")
    for _at, sentence, _stmt in f["watch"]:
        lines.append(sentence)
    for acc, version, victims, held, _stmt in f["splits"]:
        lines.append(f"{n.get(acc, acc)}: the split between {int(victims)} victims was recalculated (version "
                     f"{int(version)}); the total held, {inr(held)}, is unchanged.")
    for case, code, acc, note in f["refused"]:
        lines.append(f"The checker refused {code} on {n.get(acc, acc)} ({case}): {note}.")
    for (case,) in f["failed"]:
        lines.append(f"{case}: the checks failed, so nothing was held automatically. Please look at the case.")
    if f["waiting"]:
        lines.append(f"Waiting for you: {len(f['waiting'])} decision{'s' if len(f['waiting']) != 1 else ''}.")
        for _pid, case, acc, code, amount in f["waiting"]:
            extra = f" to {inr(amount)}" if code == "PROPOSE_NARROW" else ""
            lines.append(f"- {DECISION_WORDS.get(code, code)}{extra}: {n.get(acc, acc)} ({case})")
    else:
        lines.append("Nothing is waiting for you.")
    return "\n".join(lines)


def write(conn, bank: str, period_from: str, period_to: str, llm=None) -> dict:
    """Write the morning log for the period and store it (MORNING_LOG). Runs as the maker."""
    from pipeline.db import BANKS
    from pipeline.llm import numbers_are_grounded
    from pipeline.reasons import numbers, verdicts

    f = facts(conn, bank, period_from, period_to)
    text = template(f, BANKS[bank]["name"], period_to)
    written, by = text, "template"
    if llm is not None:
        try:
            draft = llm.write(SYSTEM, text).strip()
        except Exception:  # the template stands
            draft = ""
        if draft and numbers_are_grounded(draft, text) and numbers(draft) <= numbers(text) and not verdicts(draft):
            written, by = draft, "model"
    conn.execute("INSERT INTO MORNING_LOG (viewing_bank_id, period_from, period_to, log_text, written_by, facts_text, "
                 "written_at, stmt_id) VALUES ({b}, TO_TIMESTAMP({f}, 'YYYY-MM-DD HH24:MI:SS'), "
                 "TO_TIMESTAMP({t}, 'YYYY-MM-DD HH24:MI:SS'), {x}, {by}, {facts}, CURRENT_TIMESTAMP, "
                 "CURRENT_STATEMENT)",
                 {"b": bank, "f": period_from, "t": period_to, "x": written[:8000], "by": by, "facts": text[:8000]})
    return {"text": written, "written_by": by, "facts": text, "waiting": len(f["waiting"])}


def latest(conn, bank: str) -> dict | None:
    row = conn.execute("SELECT log_id, period_from, period_to, log_text, written_by, facts_text, stmt_id "
                       "FROM MORNING_LOG "
                       "WHERE viewing_bank_id = {b} ORDER BY log_id DESC LIMIT 1", {"b": bank}).fetchone()
    if not row:
        return None
    keys = ("log_id", "period_from", "period_to", "text", "written_by", "facts", "stmt_id")
    return {k: (str(v)[:19] if k.startswith("period") and v is not None else v) for k, v in zip(keys, row, strict=True)}