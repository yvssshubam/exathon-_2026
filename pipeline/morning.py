"""The morning log (write_morning_log): what the agent did overnight, which the officer reads at the start of the day.
Only the officer's decisions need action; everything else is listed as done.

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
                  "UNMATCHED_NOTICE": "unmatched notice", "PROPOSE_RELEASE": "release",
                  "PROPOSE_PRE_ALERT": "pre-alert"}


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
    # one line per complaint, however many times it was worked
    failed = conn.execute(f"SELECT DISTINCT case_id FROM AGENT_RUN WHERE run_status IN ('CHECK_FAILED', 'FAILED') "
                          f"AND sim_started_at {window} ORDER BY case_id", p).fetchall()
    waiting = conn.execute("SELECT proposal_id, case_id, account_id, action_code, amount_inr FROM PROPOSAL "
                           "WHERE proposal_status = 'WAITING' "
                           "AND proposed_at <= TO_TIMESTAMP({t}, 'YYYY-MM-DD HH24:MI:SS') ORDER BY proposal_id",
                           p).fetchall()
    from pipeline.explain import ticket_labels

    # one story per complaint: what reached the bank, what was held where, what had already left
    stories = []
    for case, received, status in tickets:
        c = conn.execute("SELECT amount_inr, victim_tag FROM COMPLAINT WHERE case_id = {c}", {"c": case}).fetchone()
        run = conn.execute("SELECT r.sim_holds_at, r.run_status FROM AGENT_RUN r JOIN TICKET t ON t.run_id = r.run_id "
                           "WHERE t.case_id = {c}", {"c": case}).fetchone()
        held = conn.execute("SELECT account_id, SUM(amount_inr), MIN(action_code) FROM HOLD_LEDGER WHERE case_id = {c} "
                            "AND hold_kind = 'LIEN' AND hold_status <> 'REPLACED' GROUP BY account_id "
                            "ORDER BY MIN(placed_at), account_id", {"c": case}).fetchall()
        gone = conn.execute("SELECT entry_kind, SUM(victim_amount_inr), MIN(bank_id), MIN(moved_at), MIN(filed_at) "
                            "FROM PORTAL_ENTRY WHERE case_id = {c} AND entry_kind IN ('ONWARD', 'EXIT', 'SEND_BACK') "
                            "GROUP BY entry_kind", {"c": case}).fetchall()
        caught = conn.execute("SELECT MIN(h.placed_at), MIN(h.account_id) FROM HOLD_LEDGER h WHERE h.hold_tag = "
                              "'NOT_LINKED' AND h.replaced_by IN (SELECT hold_id FROM HOLD_LEDGER WHERE case_id = {c})",
                              {"c": case}).fetchone()
        flagged = conn.execute("SELECT MIN(p.proposed_at) FROM WATCH_EVENT w JOIN PROPOSAL p ON p.proposal_id = "
                               "w.proposal_id WHERE w.case_id = {c} AND w.event_kind IN ('PRE_ALERT_NOT_NEEDED', "
                               "'PRE_ALERT_ANSWERED')", {"c": case}).fetchval()
        stories.append({"case_id": case, "received_at": str(received)[:19], "status": status,
                        "flagged_at": str(flagged)[:19] if flagged else None,
                        "amount": c[0] if c else None, "victim_tag": c[1] if c else None,
                        "holds_at": str(run[0])[:19] if run and run[0] else None,
                        "held": [(a, amt, code) for a, amt, code in held],
                        "gone": {k: (amt, b, str(m)[:19] if m else None, str(fl)[:19] if fl else None)
                                 for k, amt, b, m, fl in gone},
                        "caught": (str(caught[0])[:19], caught[1]) if caught and caught[0] else None})
    attempts = conn.execute(f"SELECT event_at, account_id, event_kind, amount_inr, counterparty FROM WATCH_EVENT "
                            f"WHERE event_at {window} AND event_kind IN ('CASHOUT_BLOCKED', 'TRANSFER_BLOCKED', "
                            "'RECIPIENT_WATCHED') ORDER BY event_at, event_id", p).fetchall()

    # why each waiting freeze was proposed, however long ago: refused tries so far, and fraud complaints that paid it
    tries = dict(conn.execute("SELECT account_id, COUNT(*) FROM WATCH_EVENT WHERE event_kind IN ('CASHOUT_BLOCKED', "
                              "'TRANSFER_BLOCKED') AND event_at <= TO_TIMESTAMP({t}, 'YYYY-MM-DD HH24:MI:SS') "
                              "GROUP BY account_id", p).fetchall())
    links = dict(conn.execute("SELECT ca.account_id, MAX(ca.complaint_links) FROM CASE_ACCOUNT ca "
                              "JOIN CASE_ACTION a ON a.case_id = ca.case_id AND a.account_id = ca.account_id "
                              "WHERE a.action_code = 'PROPOSE_FULL_FREEZE' GROUP BY ca.account_id").fetchall())

    return {"names": names, "labels": ticket_labels(conn), "tickets": tickets, "holds": holds, "filings": filings,
            "stories": stories, "attempts": attempts, "tries": tries, "links": links,
            "watch": watch,
            "splits": splits, "refused": refused, "failed": failed, "waiting": waiting}


def freeze_why(f: dict, acc: str) -> str:
    """The reason a freeze is proposed, in a few words: refused tries to move held money (since the hold, not only
    in this log's period), else the fraud complaints that paid the account."""
    tries = int((f.get("tries") or {}).get(acc) or 0) or sum(
        1 for _w, a, k, *_ in f.get("attempts", []) if a == acc and k != "RECIPIENT_WATCHED")
    if tries:
        return f", after {tries} tr{'y' if tries == 1 else 'ies'} to move held money."
    links = int(float((f.get("links") or {}).get(acc) or 0))
    if links > 1:
        return f", money from {links} fraud complaints in 7 days."
    return "."


def victim_name(tag: str | None) -> str:
    """V_LAKSHMI -> Lakshmi; generated cases (V_X03) -> Victim X03."""
    t = (tag or "").removeprefix("V_")
    return f"Victim {t}" if any(ch.isdigit() for ch in t) else t.capitalize() or "the victim"


def rupees(v) -> str:
    """Whole rupees for the note ("Rs 30,788"), the exact figure stays in the hold record."""
    return inr(_d(v).quantize(Decimal(1)))


def _names(accounts: list[str], n: dict) -> str:
    names = [n.get(a, a) for a in accounts]
    return names[0] if len(names) == 1 else ", ".join(names[:-1]) + " and " + names[-1]


def template(f: dict, bank_name: str, period_to: str) -> str:
    """The handover note for the morning desk, in names (no account IDs, no internal codes). A line starting "## "
    is a heading, "- " a point; the first line is the summary. Plain text, so the console, the terminal and the
    model all read the same thing."""
    n = f["names"]

    def at(ts):
        return str(ts)[11:16]

    stories = f.get("stories", [])
    reported = sum((_d(st["amount"]) for st in stories), Decimal(0))
    minutes = []
    for st in stories:
        if st["holds_at"]:
            a, b = st["received_at"], st["holds_at"]
            minutes.append(max(0, (int(b[11:13]) * 60 + int(b[14:16])) - (int(a[11:13]) * 60 + int(a[14:16]))))
    count = len(stories) or len(f["tickets"])
    head = f"{count} complaint{'s' if count != 1 else ''} overnight at {bank_name}, {rupees(reported)} reported."
    if minutes:
        head += f" Each was worked within {max(minutes)} minute{'s' if max(minutes) != 1 else ''}."
    lines = [head]
    for st in stories:
        who = f"{victim_name(st['victim_tag'])}, {at(st['received_at'])}, {rupees(st['amount'])}."
        parts = []
        if "SEND_BACK" in st["gone"]:
            back = at(st["gone"]["SEND_BACK"][3])
            parts.append(f"The police notice does not match a payment here. Sent back at {back}. "
                         "Nothing held.")
        held = [(a, _d(amt)) for a, amt, _ in st["held"]]
        if st["caught"]:
            parts.append(f"Already held: it landed in {n.get(st['caught'][1], st['caught'][1])} at "
                         f"{at(st['caught'][0])}, while that account was watched.")
            if st.get("flagged_at"):
                from pipeline.prealert import duration
                from pipeline.prealert import minutes as gap

                parts.append(f"The watch had flagged the payer as a likely unreported victim "
                             f"{duration(max(0, gap(st['flagged_at'], st['received_at'])))} before the complaint.")
        elif held:
            total = sum((amt for _, amt in held), Decimal(0))
            parts.append(f"{rupees(total)} held here in {_names([a for a, _ in held], n)}.")
        if "ONWARD" in st["gone"] and _d(st["gone"]["ONWARD"][0]) > 0:
            amt, bank, _moved, filed = st["gone"]["ONWARD"]
            from pipeline.db import BANKS

            other = BANKS.get(bank, {}).get("name", bank)
            parts.append(f"{rupees(amt)} had gone to {other}; the onward entry was filed "
                         f"at {at(filed)}.")
        if "EXIT" in st["gone"] and _d(st["gone"]["EXIT"][0]) > 0:
            amt, _bank, moved, _filed = st["gone"]["EXIT"]
            parts.append(f"{rupees(amt)} left as cash at {at(moved)}, before the complaint.")
        lines.append(f"- {who} " + " ".join(parts) if parts else f"- {who}")
    for (case,) in f["failed"]:
        lines.append(f"- {f.get('labels', {}).get(case, case)}: the checks failed, so nothing was held automatically. "
                     "Please look at it.")
    stopped = [a for a in f.get("attempts", []) if a[2] != "RECIPIENT_WATCHED"]
    if stopped:
        lines.append(f"## The holds stopped {rupees(sum((_d(a) for *_, a, _ in stopped), Decimal(0)))} leaving")
    for w, acc, kind, amt, to in f.get("attempts", []):
        who = n.get(acc, acc)
        if kind == "CASHOUT_BLOCKED":
            lines.append(f"- {who} tried to take {rupees(amt)} from an ATM at {at(w)}. Refused.")
        elif kind == "TRANSFER_BLOCKED":
            dest = n.get(to, to) if to else "another account"
            lines.append(f"- {who} tried to send {rupees(amt)} to {dest} at {at(w)}. "
                         "Refused.")
        else:                               # the watched account is the event's own; the payer is its counterparty
            payer = n.get(to, to) if to else "a held account"
            lines.append(f"- {who} is now watched, after {payer} tried to pay it at {at(w)}.")
    # a refused duplicate (the same proposal already waiting) is the checker doing its job, not news for the desk
    refused = list(dict.fromkeys((acc, note) for _case, _code, acc, note in f["refused"]
                                 if "already waiting" not in (note or "")))
    if refused:
        lines.append("## Needs a look")
        lines += [f"- The checker refused a step on {n.get(acc, acc)}: {note}" for acc, note in refused]
    if f["waiting"]:
        lines.append("## Waiting for the morning desk")
        for _pid, case, acc, code, amount in f["waiting"]:
            who = n.get(acc, acc)
            story = next((st for st in stories if st["case_id"] == case), None)
            vic = victim_name(story["victim_tag"]) if story else "the victim"
            if code == "UNMATCHED_NOTICE":
                lines.append(f"- Near match: {vic}'s notice. Hold {rupees(amount)} in {who}, or wait for the police?")
            elif code == "PROPOSE_NARROW":
                lines.append(f"- Smaller hold: reduce {who}'s hold to {rupees(amount)}.")
            elif code == "PROPOSE_FULL_FREEZE":
                lines.append(f"- Freeze: {who}{freeze_why(f, acc)}")
            elif code == "PROPOSE_PRE_ALERT":
                lines.append(f"- Pre-alert: {who} received {rupees(amount)} from a payer who had not paid it before, "
                             f"while it was held. Send the pre-alert that they may be an unreported victim?")
            else:
                lines.append(f"- Release: the hold on {who}, no police direction in 75 days.")
    else:
        lines.append("## Nothing is waiting for the morning desk")
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
    """The handover note: the latest log written at the morning desk's start (09:00), else the latest log."""
    row = conn.execute("SELECT log_id, period_from, period_to, log_text, written_by, facts_text, stmt_id "
                       "FROM MORNING_LOG WHERE viewing_bank_id = {b} "
                       "ORDER BY CASE WHEN TO_CHAR(period_to, 'HH24:MI') = '09:00' THEN 0 ELSE 1 END, log_id DESC "
                       "LIMIT 1", {"b": bank}).fetchone()
    if not row:
        return None
    keys = ("log_id", "period_from", "period_to", "text", "written_by", "facts", "stmt_id")
    return {k: (str(v)[:19] if k.startswith("period") and v is not None else v) for k, v in zip(keys, row, strict=True)}