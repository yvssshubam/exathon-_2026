"""The watch: the agent keeps working after the holds go on (docs/DESIGN.md, "The watch"; DECISIONS D-38).

A ticket's run ends when its holds are placed, but the money keeps moving. The watch replays every transaction that
touches a watched account after that moment, in time order, as the bank's core system would see it:

  watched   own-bank accounts the agent held, plus accounts with a whole-account freeze waiting for the officer
  BLOCKED   a debit that needs held money is refused (the balance stays); the attempt is evidence for the officer
  MOVED     a mule-like account moves money that was not held: evidence that it is still passing money on
  NEW_MONEY a mule-like account receives new money: money that a freeze would now protect
  MARKER_HOLD a first-layer marker catches a later credit (rules: HOLD_MARKER), up to the trail amount

For every event the rules decide what is allowed (`options`); then the controller picks one:
  * pipeline  a fixed policy: alert the officer out of hours when an exit is refused or a large amount is at stake
  * llm       the local model picks from the allowed options and may word the alert; any failure, and the fixed
              policy decides that event
The only money the watch can hold is a marker's catch, and only within the rule's cap. Alerts and evidence change
nothing in the bank; whole-account freezes stay with the officer.

    python -m pipeline.watch                 # replay the watch over every worked ticket (fixed policy)
    python -m pipeline.watch --mode llm      # let the local model choose the responses
"""
from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass, field
from decimal import Decimal

from pipeline.explain import inr

FMT = "%Y-%m-%d %H:%M:%S"
DESK_HOURS = (10, 18)
ALERT_MIN_INR = Decimal("25000")          # out of hours, the fixed policy alerts at this amount or on any exit

OPTIONS = {
    "evidence": "Add it to the evidence on the officer's decision for this account; nothing else.",
    "evidence_and_alert": "Add it to the evidence and alert the officer now, out of hours.",
    "hold": "Hold the credit (the marker rule requires it), within the rule's cap.",
    "hold_and_alert": "Hold the credit and alert the officer now, out of hours.",
}
SYSTEM_PICK = (
    "You watch bank accounts that hold a fraud victim's money, after the holds were placed, while the desk is "
    "closed. For one event, call exactly one of the offered tools. Alert the officer only when waking them is "
    "worth it: money leaving to cash or crypto, or a large amount at stake. You never compute amounts; the rules "
    "already decided what is allowed.")
SYSTEM_WRITE = (
    "Write one short out-of-hours alert for a bank's nodal officer: what happened, when, and what is waiting for "
    "them. One or two sentences. Copy every rupee figure exactly as written; add no other figures.")


def out_of_hours(ts: str) -> bool:
    h = int(ts[11:13])
    return h < DESK_HOURS[0] or h >= DESK_HOURS[1]


# ---------------------------------------------------------------- the replay (pure: no database)

@dataclass
class Txn:
    txn_id: str
    ts: str
    from_account: str
    to_account: str
    amount: Decimal
    channel: str
    is_exit: bool
    exit_identifier: str | None = None


@dataclass
class Watched:
    account_id: str
    name: str
    since: str                      # first hold or proposal on it (simulated clock)
    balance: Decimal                # balance at `since` (LIBR replay at the hold time)
    mule_like: bool                 # band MULE or REVIEW, or a freeze waiting
    marker_cap: Decimal = Decimal(0)   # HOLD_MARKER: how much later credit it may still catch
    cases: list[str] = field(default_factory=list)


@dataclass
class Hold:
    account_id: str
    placed_at: str
    amount: Decimal
    kind: str                       # LIEN, MARKER or ACCOUNT_FREEZE
    ends_at: str | None = None      # when a narrower or wider hold replaced it


@dataclass
class Event:
    account_id: str
    txn_id: str
    at: str
    kind: str
    amount: Decimal
    counterparty: str
    channel: str
    held: Decimal
    balance: Decimal
    is_exit: bool
    cases: list[str]
    options: list[str] = field(default_factory=list)
    response: str = ""
    chosen_by: str = ""
    alert_text: str | None = None
    hold_amount: Decimal = Decimal(0)


def held_at(holds: list[Hold], account: str, t: str) -> tuple[Decimal, bool]:
    """(money under lien, frozen?) on an account at moment t: holds placed by then and not yet replaced."""
    live = [h for h in holds if h.account_id == account and h.placed_at <= t and (h.ends_at is None or h.ends_at > t)]
    frozen = any(h.kind == "ACCOUNT_FREEZE" for h in live)
    return sum((h.amount for h in live if h.kind == "LIEN"), Decimal(0)), frozen


def replay(txns: list[Txn], watched: dict[str, Watched], holds: list[Hold],
           names: dict[str, str] | None = None) -> list[Event]:
    """Every event on the watched accounts, in time order. A refused debit never reaches its receiver."""
    names = names or {}
    balance = {a: w.balance for a, w in watched.items()}
    caught = {a: Decimal(0) for a in watched}
    events: list[Event] = []
    for t in sorted(txns, key=lambda x: (x.ts, x.txn_id)):
        src = watched.get(t.from_account)
        if src and t.ts > src.since:
            held, frozen = held_at(holds, src.account_id, t.ts)
            other = t.exit_identifier or names.get(t.to_account, t.to_account)
            if frozen or t.amount > balance[src.account_id] - held:
                events.append(Event(src.account_id, t.txn_id, t.ts, "BLOCKED", t.amount, other, t.channel, held,
                                    balance[src.account_id], t.is_exit, src.cases))
                continue                                  # refused: nothing left the account, nothing arrives
            balance[src.account_id] -= t.amount
            if src.mule_like:
                events.append(Event(src.account_id, t.txn_id, t.ts, "MOVED", t.amount, other, t.channel, held,
                                    balance[src.account_id], t.is_exit, src.cases))
        dst = watched.get(t.to_account)
        if dst and t.ts > dst.since:
            balance[dst.account_id] += t.amount
            held, _ = held_at(holds, dst.account_id, t.ts)
            other = names.get(t.from_account, t.from_account)
            room = dst.marker_cap - caught[dst.account_id]
            if room > 0:
                take = min(room, t.amount)
                caught[dst.account_id] += take
                holds.append(Hold(dst.account_id, t.ts, take, "LIEN"))
                ev = Event(dst.account_id, t.txn_id, t.ts, "MARKER_HOLD", t.amount, other, t.channel, held + take,
                           balance[dst.account_id], False, dst.cases, hold_amount=take)
                events.append(ev)
            elif dst.mule_like:
                events.append(Event(dst.account_id, t.txn_id, t.ts, "NEW_MONEY", t.amount, other, t.channel, held,
                                    balance[dst.account_id], False, dst.cases))
    return events


def options_for(e: Event) -> list[str]:
    """What the rules allow for one event. Alerts only out of hours; a marker's catch is always held."""
    first = "hold" if e.kind == "MARKER_HOLD" else "evidence"
    return [first, f"{first}_and_alert"] if out_of_hours(e.at) else [first]


def fixed_policy(e: Event) -> str:
    """The default choice: out of hours, alert on any refused exit or when the amount is large."""
    alert = [o for o in e.options if o.endswith("_and_alert")]
    if alert and (e.kind == "BLOCKED" and e.is_exit or e.amount >= ALERT_MIN_INR or e.kind == "MARKER_HOLD"):
        return alert[0]
    return e.options[0]


def describe(e: Event, name: str) -> str:
    """One plain sentence for the officer; also the facts given to the model."""
    when = e.at[11:16]
    if e.kind == "BLOCKED":
        where = "to cash at " + e.counterparty if e.channel == "ATM" else (
            "to a crypto wallet" if e.channel == "CRYPTO" else "to " + e.counterparty)
        return (f"{when}: {name} tried to move {inr(e.amount)} {where}; refused, {inr(e.held)} is held. "
                f"A decision on {name} is waiting for you.")
    if e.kind == "MOVED":
        return f"{when}: {name} moved {inr(e.amount)} that was not held, to {e.counterparty}."
    if e.kind == "NEW_MONEY":
        return (f"{when}: {name} received {inr(e.amount)} from {e.counterparty}; a freeze would protect it. "
                f"A decision on {name} is waiting for you.")
    return f"{when}: {name} received {inr(e.amount)}; the marker held {inr(e.hold_amount)} of it."


class Controller:
    """Picks the response for each event: the fixed policy, or the local model within the allowed options."""

    def __init__(self, mode: str = "pipeline", llm=None):
        self.mode, self.llm = mode, llm
        self.llm_ok = mode == "llm" and llm is not None

    def choose(self, e: Event, sentence: str) -> tuple[str, str]:
        if len(e.options) == 1:
            return e.options[0], "rule"
        if not self.llm_ok:
            return fixed_policy(e), "pipeline" if self.mode == "pipeline" else "fallback"
        tools = [{"type": "function", "function": {"name": o, "description": OPTIONS[o],
                                                   "parameters": {"type": "object", "properties": {}}}}
                 for o in e.options]
        brief = json.dumps({"event": sentence, "kind": e.kind, "to_cash_or_crypto": e.is_exit,
                            "amount_inr": float(e.amount), "time": e.at[11:16]})
        try:
            name = self.llm.pick_tool(SYSTEM_PICK, brief, tools)
        except Exception:
            self.llm_ok = False                         # the fixed policy decides the rest of this watch
            return fixed_policy(e), "fallback"
        return (name, "llm") if name in e.options else (fixed_policy(e), "fallback")

    def alert(self, sentence: str) -> str:
        if not self.llm_ok:
            return sentence
        from pipeline.llm import numbers_are_grounded

        try:
            text = self.llm.write(SYSTEM_WRITE, sentence)
        except Exception:
            return sentence
        return text if numbers_are_grounded(text, sentence) else sentence


def decide(events: list[Event], names: dict[str, str], controller: Controller) -> list[Event]:
    for e in events:
        e.options = options_for(e)
        sentence = describe(e, names.get(e.account_id, e.account_id))
        choice, e.chosen_by = controller.choose(e, sentence)
        e.response = {"evidence": "EVIDENCE", "hold": "HELD"}.get(choice, "ALERT")
        e.alert_text = controller.alert(sentence) if choice.endswith("_and_alert") else None
    return events


# ---------------------------------------------------------------- Exasol

def _d(v) -> Decimal:
    return Decimal(str(v)) if v is not None else Decimal(0)


def _ts(v) -> str:
    return str(v)[:19]


def load_state(conn, bank: str) -> tuple[list[Txn], dict[str, Watched], list[Hold], dict[str, str]]:
    """What the watch needs from Exasol: watched accounts with their balance at the first hold, the hold timeline,
    and every later transaction touching them. Holds the watch placed before are left out (it places them again)."""
    q = lambda sql, **p: conn.execute(sql, {"b": bank, **p}).fetchall()  # noqa: E731
    rows = q("""
        SELECT ca.account_id, ca.case_id, r.sim_holds_at, ca.balance_now_inr, ca.band,
               COALESCE(acc.holder_name, ca.account_id), ca.trail_amount_inr,
               (SELECT MAX(CASE WHEN x.action_code = 'HOLD_MARKER' THEN 1 ELSE 0 END) FROM PH.CASE_ACTION x
                 WHERE x.case_id = ca.case_id AND x.viewing_bank_id = ca.viewing_bank_id
                   AND x.account_id = ca.account_id) AS is_marker,
               (SELECT MAX(CASE WHEN x.action_code = 'PROPOSE_FULL_FREEZE'
                                 AND x.action_status IN ('PROPOSED', 'WAITING') THEN 1 ELSE 0 END)
                  FROM PH.CASE_ACTION x WHERE x.case_id = ca.case_id AND x.viewing_bank_id = ca.viewing_bank_id
                   AND x.account_id = ca.account_id) AS freeze_waiting,
               (SELECT COUNT(*) FROM PH.HOLD_LEDGER h WHERE h.case_id = ca.case_id
                   AND h.viewing_bank_id = ca.viewing_bank_id AND h.account_id = ca.account_id
                   AND h.placed_by = 'agent' AND h.action_code <> 'WATCH_HOLD') AS agent_holds
        FROM PH.CASE_ACCOUNT ca
        JOIN PH.TICKET t ON t.case_id = ca.case_id
        JOIN PH.AGENT_RUN r ON r.run_id = t.run_id AND r.run_status = 'DONE'
        LEFT JOIN PH.ACCOUNT acc ON acc.account_id = ca.account_id
        WHERE ca.viewing_bank_id = {b} AND ca.is_own_bank AND NOT ca.is_exit
        ORDER BY r.sim_holds_at, ca.case_id""")
    watched: dict[str, Watched] = {}
    names: dict[str, str] = {}
    for acc, case, since, bal, band, name, trail, is_marker, freeze, agent_holds in rows:
        names[acc] = name
        if not (int(agent_holds or 0) or int(freeze or 0)):
            continue
        w = watched.get(acc)
        if w is None:
            w = watched[acc] = Watched(acc, name, _ts(since), _d(bal), False)
        w.cases.append(case)
        w.mule_like |= band in ("MULE", "REVIEW") or bool(int(freeze or 0))
        if int(is_marker or 0):
            w.marker_cap += _d(trail)
    holds = [Hold(a, _ts(p), _d(amt), kind, _ts(end) if end else None) for a, p, amt, kind, end in q("""
        SELECT h.account_id, h.placed_at, h.amount_inr, h.hold_kind, n.placed_at AS ended_at
        FROM PH.HOLD_LEDGER h LEFT JOIN PH.HOLD_LEDGER n ON n.hold_id = h.replaced_by
        WHERE h.viewing_bank_id = {b} AND h.action_code <> 'WATCH_HOLD' AND h.hold_kind <> 'MARKER'""")]
    if not watched:
        return [], watched, holds, names
    start = min(w.since for w in watched.values())
    txns = [Txn(i, _ts(ts), f, to, _d(amt), ch, bool(ex), ident) for i, ts, f, to, amt, ch, ex, ident in q("""
        SELECT txn_id, ts, from_account, to_account, amount_inr, channel, is_exit, exit_identifier
        FROM PH.TXN
        WHERE ts > TO_TIMESTAMP({s}, 'YYYY-MM-DD HH24:MI:SS')
          AND (from_account IN ({accs}) OR to_account IN ({accs}))""", s=start, accs=list(watched))]
    # Accounts outside the trails are named only by their bank: a payer may be a victim whose ticket has not
    # arrived yet, and victims' details stay off the bank's screens (SOP 9.1(ii)).
    others = ({t.from_account for t in txns} | {t.to_account for t in txns}) - set(names)
    for a, b, own in q("SELECT a.account_id, COALESCE(b.bank_name, a.bank_id), b.is_own_bank FROM PH.ACCOUNT a "
                       "LEFT JOIN PH.BANK b ON b.bank_id = a.bank_id WHERE a.account_id IN ({a})",
                       a=list(others) or ["-"]):
        names[a] = counterparty_label(b, bool(own))
    return txns, watched, holds, names


def counterparty_label(bank_name: str, own_bank: bool) -> str:
    return "another account at this bank" if own_bank else f"an account at {bank_name}"


def save(conn, bank: str, events: list[Event]) -> None:
    conn.execute("DELETE FROM PH.WATCH_EVENT WHERE viewing_bank_id = {b}", {"b": bank})
    conn.execute("DELETE FROM PH.HOLD_LEDGER WHERE viewing_bank_id = {b} AND action_code = 'WATCH_HOLD'", {"b": bank})
    for e in events:
        hold_id = None
        if e.kind == "MARKER_HOLD" and e.hold_amount > 0:
            case = e.cases[0]
            conn.execute(
                "INSERT INTO PH.HOLD_LEDGER (case_id, viewing_bank_id, account_id, hold_kind, amount_inr, placed_at, "
                "placed_by, hold_status, action_code, reason_text, stmt_id) SELECT {c}, {b}, {a}, 'LIEN', {amt!d}, "
                "TO_TIMESTAMP({t}, 'YYYY-MM-DD HH24:MI:SS'), 'agent', 'ACTIVE', 'WATCH_HOLD', {why}, CURRENT_STATEMENT",
                {"c": case, "b": bank, "a": e.account_id, "amt": str(e.hold_amount), "t": e.at,
                 "why": f"Marker caught a later credit ({e.txn_id})"})
            hold_id = conn.execute("SELECT MAX(hold_id) FROM PH.HOLD_LEDGER WHERE viewing_bank_id = {b} "
                                   "AND action_code = 'WATCH_HOLD'", {"b": bank}).fetchval()
        conn.execute(
            "INSERT INTO PH.WATCH_EVENT (viewing_bank_id, account_id, txn_id, event_at, event_kind, amount_inr, "
            "counterparty, channel, held_inr, balance_inr, case_ids, response, allowed_responses, chosen_by, "
            "alert_text, hold_id, stmt_id) "
            "SELECT {b}, {a}, {tx}, TO_TIMESTAMP({t}, 'YYYY-MM-DD HH24:MI:SS'), {k}, {amt!d}, {cp}, {ch}, "
            "{held!d}, {bal!d}, {cases}, {r}, {opts}, {by}, {alert}, {hid}, CURRENT_STATEMENT",
            {"b": bank, "a": e.account_id, "tx": e.txn_id, "t": e.at, "k": e.kind, "amt": str(e.amount),
             "cp": e.counterparty[:200], "ch": e.channel, "held": str(e.held), "bal": str(e.balance),
             "cases": ",".join(e.cases)[:400], "r": e.response, "opts": ",".join(e.options), "by": e.chosen_by,
             "alert": e.alert_text, "hid": None if hold_id is None else str(int(hold_id))})


def run_watch(conn, bank: str, mode: str = "pipeline", llm=None) -> dict:
    """Replay the watch over every worked ticket and store what the agent saw and did."""
    if mode == "llm" and llm is None:
        from pipeline.llm import client

        llm = client()
    txns, watched, holds, names = load_state(conn, bank)
    events = decide(replay(txns, watched, holds, names), names, Controller(mode, llm))
    save(conn, bank, events)
    count = lambda k: sum(1 for e in events if e.kind == k)  # noqa: E731
    return {"watched": len(watched), "events": len(events), "blocked": count("BLOCKED"),
            "new_money": count("NEW_MONEY"), "moved": count("MOVED"), "caught": count("MARKER_HOLD"),
            "alerts": sum(1 for e in events if e.response == "ALERT")}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", choices=["pipeline", "llm"], default="pipeline")
    ap.add_argument("--bank")
    args = ap.parse_args(argv)
    from pipeline.db import connect, settings

    bank = args.bank or settings()["own_bank_id"]
    conn = connect()
    try:
        r = run_watch(conn, bank, args.mode)
        print(f"watched {r['watched']} accounts: {r['events']} events, {r['blocked']} refused, "
              f"{r['new_money']} new money, {r['moved']} moved, {r['caught']} caught; {r['alerts']} alerts")
        for at, kind, name, amount, resp, by, alert in conn.execute(
                "SELECT w.event_at, w.event_kind, COALESCE(a.holder_name, w.account_id), w.amount_inr, w.response, "
                "w.chosen_by, w.alert_text FROM PH.WATCH_EVENT w LEFT JOIN PH.ACCOUNT a ON a.account_id = w.account_id "
                "WHERE w.viewing_bank_id = {b} ORDER BY w.event_at, w.event_id", {"b": bank}).fetchall():
            print(f"  {str(at)[:16]}  {kind:11} {name[:26]:26} {inr(amount):>12}  {resp:8} [{by}]"
                  + (f"  alert: {alert}" if alert else ""))
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())