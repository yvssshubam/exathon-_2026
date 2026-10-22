"""The reasons behind a whole-account freeze, drafted for the officer (docs/DESIGN.md, "Where AI fits"; D-39).

Courts ask for the material behind a whole-account freeze, so the officer has to write it down before approving
one. The agent drafts it from what the database already holds about the account:

  * the tickets that name it and the victims' money in it          PH.CASE_ACTION, PH.COMPLAINT
  * its 90-day history, as measured by the scoring step           PH.ACCOUNT_SIGNALS
  * what it did after the hold (refused withdrawals, new money)   PH.WATCH_EVENT (pipeline/watch.py)

The facts are gathered and worded by fixed code first (the template). With the local model on, the model rewrites
them as a paragraph; every number in its text (rupees, percentages, days, times) must already be in the facts, or
the template is used. Either way the officer reads it, edits it, and approves; the text then goes into the audit log
and the reply to the police.
"""
from __future__ import annotations

import re
from decimal import Decimal

from pipeline.explain import inr

SYSTEM = (
    "You draft the reasons for a whole-account freeze of a bank account, for a bank officer to check and send to "
    "the police and, if challenged, a court. Write one factual paragraph of 3 to 6 sentences in plain words. Use only "
    "the facts given. Copy every number exactly as written (amounts, percentages, days, counts, times); add no other "
    "number, no estimate and no opinion about guilt. End by saying the freeze stops money that arrives later from "
    "being moved on.")

NUMBER = re.compile(r"\d[\d,]*(?:\.\d+)?")


def numbers(text: str) -> set[str]:
    """Every number in a text, normalised: '1,19,500' -> '119500', '50,000.00' -> '50000', '05:10' -> {'05', '10'}."""
    out = set()
    for m in NUMBER.finditer(text):
        v = m.group(0).replace(",", "")
        if "." in v:
            v = v.rstrip("0").rstrip(".")
        out.add(v.lstrip("0") or "0")
    return out


def grounded(written: str, facts: str) -> bool:
    """The guard on the model: it may reword the facts, never add or change a number."""
    return numbers(written) <= numbers(facts)


def _age(days: float | None) -> str | None:
    if days is None:
        return None
    days = int(round(days))
    if days >= 730:
        return f"about {days // 365} years"
    return f"{days} days"


def template(f: dict) -> str:
    """The facts as plain sentences, in a fixed order. Also what the model is given."""
    name = f["name"]
    tickets = f["tickets"]
    parts = [f"{name} holds a {f['account_type']} account at {f['bank_name']}"
             + (f", opened {_age(f['age_days'])} before the first ticket" if f.get("age_days") is not None else "")
             + "."]
    if tickets:
        listed = "; ".join(f"the {t['received_at'][11:16]} ticket, {inr(t['reported_inr'])} reported, "
                           f"{inr(t['victim_inr'])} of it traced into this account" for t in tickets)
        parts.append(f"It is named in {len(tickets)} fraud ticket{'s' if len(tickets) != 1 else ''}: {listed}.")
    s = f["signals"]
    history = []
    if s.get("median_dwell_minutes") is not None:
        history.append(f"money it receives usually stays {round(s['median_dwell_minutes'])} minutes")
    if s.get("pass_through_ratio") is not None:
        history.append(f"{round(s['pass_through_ratio'] * 100)}% of what it received was sent on within an hour")
    if s.get("cash_or_crypto_exits"):
        history.append(f"{round(s['cash_or_crypto_exits'])} withdrawals went to cash or crypto")
    if s.get("complaint_linked_credits_7d"):
        n = round(s["complaint_linked_credits_7d"])
        history.append(f"it was paid by {n} reported fraud{'s' if n != 1 else ''} in the last 7 days")
    if history:
        parts.append("Over the last 90 days " + "; ".join(history) + ".")
    for e in f["since_hold"]:
        parts.append(e)
    parts.append(f"Its balance is {inr(f['balance_inr'])}, of which {inr(f['held_inr'])} is already held amount-only.")
    parts.append("A whole-account freeze is proposed so that money arriving later cannot be moved on before the "
                 "police act.")
    return " ".join(parts)


def facts(conn, bank: str, account_id: str) -> dict:
    """Everything the draft may say, read from Exasol."""
    from pipeline.watch import Event, describe

    q = lambda sql, **p: conn.execute(sql, {"b": bank, "a": account_id, **p})  # noqa: E731
    acc = q("SELECT COALESCE(a.holder_name, a.account_id), a.account_type, COALESCE(b.bank_name, a.bank_id), "
            "a.opened_on FROM PH.ACCOUNT a LEFT JOIN PH.BANK b ON b.bank_id = a.bank_id "
            "WHERE a.account_id = {a}").fetchone()
    if acc is None:
        raise LookupError(f"no account {account_id}")
    name, account_type, bank_name, opened_on = acc
    tickets = [{"case_id": c, "received_at": str(t)[:19], "reported_inr": Decimal(str(amt or 0)),
                "victim_inr": Decimal(str(v or 0)), "balance": Decimal(str(bal or 0))}
               for c, t, amt, v, bal in q(
                   "SELECT x.case_id, t.received_at, c.amount_inr, x.victim_amount_inr, x.balance_now_inr "
                   "FROM PH.CASE_ACTION x JOIN PH.TICKET t ON t.case_id = x.case_id "
                   "JOIN PH.COMPLAINT c ON c.case_id = x.case_id "
                   "WHERE x.viewing_bank_id = {b} AND x.account_id = {a} AND x.action_code = 'PROPOSE_FULL_FREEZE' "
                   "ORDER BY t.received_at, x.case_id").fetchall()]
    latest = tickets[-1]["case_id"] if tickets else None
    signals = {n: (None if v is None else float(v)) for n, v in q(
        "SELECT signal_name, signal_value FROM PH.ACCOUNT_SIGNALS WHERE case_id = {c} AND account_id = {a}",
        c=latest).fetchall()} if latest else {}
    first = tickets[0]["received_at"] if tickets else None
    age = None
    if opened_on is not None and first:
        from datetime import date

        age = (date.fromisoformat(first[:10]) - date.fromisoformat(str(opened_on)[:10])).days
    since = []
    for at, kind, amt, cp, ch, held, bal in q(
            "SELECT event_at, event_kind, amount_inr, counterparty, channel, held_inr, balance_inr FROM PH.WATCH_EVENT "
            "WHERE viewing_bank_id = {b} AND account_id = {a} AND event_kind IN ('BLOCKED', 'NEW_MONEY', 'MOVED') "
            "ORDER BY CASE WHEN event_kind = 'BLOCKED' THEN 0 ELSE 1 END, event_at LIMIT 4").fetchall():
        e = Event(account_id, "", str(at)[:19], kind, Decimal(str(amt)), cp or "", ch or "", Decimal(str(held or 0)),
                  Decimal(str(bal or 0)), ch in ("ATM", "CRYPTO", "CASH"), [])
        since.append(re.sub(r" A decision on .* is waiting for you\.$", "", describe(e, name))
                     .replace("; a freeze would protect it.", ", money a freeze would now protect."))
    held = q("SELECT SUM(amount_inr) FROM PH.HOLD_LEDGER WHERE viewing_bank_id = {b} AND account_id = {a} "
             "AND hold_status = 'ACTIVE' AND hold_kind = 'LIEN'").fetchval()
    # Balance now: after the watch's last event on the account if there is one, else at the latest hold time.
    last = q("SELECT balance_inr FROM PH.WATCH_EVENT WHERE viewing_bank_id = {b} AND account_id = {a} "
             "ORDER BY event_at DESC, event_id DESC LIMIT 1").fetchval()
    balance = Decimal(str(last)) if last is not None else tickets[-1]["balance"] if tickets else Decimal(0)
    return {"account_id": account_id, "name": name, "account_type": account_type or "bank", "bank_name": bank_name,
            "age_days": age, "tickets": tickets, "signals": signals, "since_hold": since,
            "balance_inr": balance, "held_inr": Decimal(str(held or 0))}


def draft(f: dict, llm=None) -> dict:
    """The draft the officer starts from: the model's paragraph if it is on and grounded, else the template."""
    text = template(f)
    if llm is not None:
        try:
            written = llm.write(SYSTEM, text).strip()
        except Exception:
            written = ""
        if written and grounded(written, text):
            return {"text": written, "written_by": "model", "facts": text}
    return {"text": text, "written_by": "template", "facts": text}


def draft_for(conn, bank: str, account_id: str) -> dict:
    from pipeline.llm import client

    return draft(facts(conn, bank, account_id), client())