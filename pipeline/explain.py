"""Turn an action row into plain sentences: plain words over jargon, action first, reasons after.

Every number comes from CASE_ACTION (computed in Exasol); this module only words it. The console, the agent's
case summary and the officer's review all use it, so the same numbers are always worded the same way.
The question each account's reasons answer is not "is this a mule?" but "how much should we hold here, and can we
defend it?" (docs/DESIGN.md).
"""
from __future__ import annotations

from decimal import Decimal
from pathlib import Path

import yaml

BANK_NAMES = {k: v["name"] for k, v in yaml.safe_load(
    (Path(__file__).resolve().parents[1] / "config" / "banks.yaml").read_text(encoding="utf-8")).items()}
AUTOMATIC = ("MANDATORY_HOLD", "HOLD_MARKER", "RECORD_EXIT", "ONWARD_ENTRY")
PROPOSALS = ("PROPOSE_NARROW", "PROPOSE_FULL_FREEZE", "UNMATCHED_NOTICE", "PROPOSE_RELEASE")


def inr(amount) -> str:
    """Indian grouping: 119500 -> 'Rs 1,19,500'; 498.75 -> 'Rs 498.75'."""
    value = Decimal(str(amount or 0)).quantize(Decimal("0.01"))
    sign = "-" if value < 0 else ""
    whole, paise = divmod(abs(value) * 100, 100)
    digits = str(int(whole))
    if len(digits) > 3:
        head, tail = digits[:-3], digits[-3:]
        groups = []
        while len(head) > 2:
            groups.insert(0, head[-2:])
            head = head[:-2]
        if head:
            groups.insert(0, head)
        digits = ",".join(groups + [tail])
    return f"Rs {sign}{digits}" + (f".{int(paise):02d}" if paise else "")


def bank_name(bank_id: str | None) -> str:
    return BANK_NAMES.get(bank_id or "", bank_id or "another bank")


def _d(value) -> Decimal:
    return Decimal(str(value or 0))


def headline(a: dict) -> str:
    """One short line for a list or a button."""
    code, amount = a["action_code"], a.get("amount_inr")
    if code == "MANDATORY_HOLD":
        return f"Held {inr(amount)} (amount only)"
    if code == "HOLD_MARKER":
        return "Marked to catch later credits"
    if code == "RECORD_EXIT":
        return f"Exit recorded: {inr(amount)} of the victim's money"
    if code == "ONWARD_ENTRY":
        return f"Onward entry to {bank_name(a.get('bank_id'))}: {inr(amount)}"
    if code == "PROPOSE_NARROW":
        return f"Narrow the hold to {inr(amount)}?"
    if code == "PROPOSE_FULL_FREEZE":
        return "Freeze the whole account?"
    if code == "UNMATCHED_NOTICE":
        return "Unmatched notice: act on the near match?"
    if code == "PROPOSE_RELEASE":
        return f"Release the {inr(amount)} hold?"
    if str(a.get("account_id", "")).startswith("EXIT:"):
        return "Exit at another bank"
    return "No action here"


def explain(a: dict) -> list[str]:
    """Plain sentences for one CASE_ACTION row (dict with its column names in lower case)."""
    code, reason = a["action_code"], a.get("reason_code")
    victim, others = _d(a.get("victim_amount_inr")), _d(a.get("other_victims_inr"))
    out: list[str] = []
    if code == "MANDATORY_HOLD":
        trail, balance = _d(a.get("trail_amount_inr")), _d(a.get("balance_now_inr"))
        if trail > balance:
            out.append(f"The trail names {inr(trail)} arriving here; only {inr(balance)} was left, so all of it is "
                       f"held. The rules require this hold (SOP 9.1(i)), so the agent placed it at once.")
        else:
            out.append(f"The trail names {inr(trail)} arriving here, so {inr(a.get('amount_inr'))} is held: the "
                       f"amount only, not the account. The rules require this hold (SOP 9.1(i)), so the agent "
                       f"placed it at once.")
        if victim < _d(a.get("amount_inr")):
            out.append(f"Only {inr(victim)} of it can be traced to the victim's money (lowest-balance rule).")
        if reason == "NODAL_POOL_ESCROW":
            out.append("This is a nodal, pool or escrow account: only ever the amount, never the account.")
    elif code == "HOLD_MARKER":
        out.append("Nothing is left in this first-layer account. It is marked so that money arriving later is held "
                   "(SOP 9.1(i)). The victim is not told about an empty account.")
    elif code == "RECORD_EXIT":
        out.append(f"{inr(a.get('amount_inr'))} of the victim's money left banking here. It cannot be held; the exit "
                   f"and its identifiers were filed on the portal for the police.")
    elif code == "ONWARD_ENTRY":
        out.append(f"The money moved on to {bank_name(a.get('bank_id'))}. The onward entry was filed with the "
                   f"victim's share ({inr(a.get('amount_inr'))}), so that bank gets its ticket now and can size its "
                   f"hold.")
    elif code == "PROPOSE_NARROW":
        held = _d(a.get("current_hold_inr"))
        out.append(f"The mandatory hold here is {inr(held)}, but only {inr(victim + others)} of it is victims' money "
                   f"(lowest-balance rule). The rest is the holder's own.")
        if reason == "NODAL_POOL_ESCROW":
            out.append("This is a nodal, pool or escrow account, so the hold should stay as small as the trail allows.")
        else:
            out.append("The account's history reads like an honest receiver, so narrowing the lien to the victims' "
                       "money is proposed. Receiving one tainted payment is not complicity (AP HC, 22 Jun 2026).")
    elif code == "PROPOSE_FULL_FREEZE":
        out.append("The account's history shows the exceptional pattern courts accept for a whole-account freeze "
                   "(Rajasthan HC, 20 Aug 2026). A label alone is not a reason, so the evidence is listed below.")
        if reason == "REPEATED_COMPLAINTS":
            out.append("It received money from three or more reported frauds in the last 7 days.")
        else:
            out.append("After the hold it tried to take the money out as cash, and the hold refused it.")
        out.append("A freeze also stops money that arrives later from being moved. Only an officer can place it.")
    elif code == "UNMATCHED_NOTICE":
        out.append("The ticket does not match a transaction exactly, so nothing is held and it was sent back for "
                   "correction at once. A payment on the account it names may be the one it means: you decide.")
    elif code == "PROPOSE_RELEASE":
        out.append("No police direction has confirmed this hold in 75 days. Lifting it needs your approval.")
    elif reason == "EXIT_AT_OTHER_BANK":
        out.append("The money left banking from another bank's account; that bank files the exit.")
    elif reason == "OTHER_BANK_DOWNSTREAM":
        out.append(f"This account is at {bank_name(a.get('bank_id'))}, further down the trail. The bank that paid it "
                   f"files the onward entry.")
    else:
        out.append("There is no money left here to hold.")
    if others > 0 and code in ("MANDATORY_HOLD", "PROPOSE_NARROW"):
        out.append(f"The account also holds {inr(others)} of other victims' money, shared between victims pro rata "
                   f"at refund (SOP para 5(xiii)).")
    return out