"""Turn a recommendation row into plain sentences (requirements s10: plain words over jargon).

The numbers all come from PH.RECOMMENDATION (computed in Exasol); this module only words them. The console,
notices and report reuse it, so an officer, a holder and a court read the same reasons.
"""
from __future__ import annotations

from decimal import Decimal

BANK_NAMES = {"BANK_X": "Bank X", "BANK_Y": "Bank Y", "BANK_Z": "Bank Z"}


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


def headline(rec: dict) -> str:
    """One short line for a list or a button: 'Hold Rs 1,000 only', 'Refer Rs 1,19,500 to Bank Y'."""
    action, amount = rec["action_code"], rec.get("amount_inr")
    if action == "AMOUNT_HOLD":
        return f"Hold {inr(amount)} only"
    if action == "ACCOUNT_HOLD":
        return "Hold the whole account (last resort)"
    if action == "REFER_OUT":
        return f"Refer {inr(amount)} to {bank_name(rec.get('bank_id'))}"
    if action == "HOLD_MARKER":
        return "Mark to catch later credits"
    return "No action" if not rec["account_id"].startswith("EXIT:") else "Record the exit"


def explain(rec: dict) -> list[str]:
    """Plain sentences for one PH.RECOMMENDATION row (dict with its column names in lower case)."""
    action, code = rec["action_code"], rec["reason_code"]
    out: list[str] = []
    if action == "AMOUNT_HOLD":
        out.append(f"Hold {inr(rec['amount_inr'])} only, not the whole account.")
        if rec.get("draft_narrowing"):
            out.append(f"Only {inr(rec['victim_amount_inr'])} of it can be traced to the victim's money "
                       f"({rec['trace_rule']} rule). A request asking the police to narrow the hold to that amount "
                       f"has been drafted.")
        if rec.get("draft_seizure"):
            out.append("The account shows strong signs of being used as a mule account. Freezing the whole account "
                       "needs a police seizure order under S.106 BNSS after an FIR, so a request for one has been "
                       "drafted instead.")
        if code == "NODAL_POOL_ESCROW":
            out.append("This is a nodal, pool or escrow account, so only the amount is held, never the account.")
    elif action == "ACCOUNT_HOLD":
        out.append(f"Hold the whole account ({inr(rec['balance_now_inr'])}) as a last resort, under the RBI draft "
                   f"rules for suspected mule accounts. The reasons below must be shared with the holder.")
    elif action == "REFER_OUT":
        out.append(f"The money left for {bank_name(rec.get('bank_id'))}. Send the onward trail through CFCFRMS, "
                   f"including that {inr(rec['amount_inr'])} of it was the victim's money.")
    elif action == "HOLD_MARKER":
        out.append("Nothing is left in this first-layer account. Mark it so that money arriving later is held.")
    elif code == "EXIT":
        out.append("The money left the banking system here, so it cannot be held. The exit and its identifiers "
                   "are recorded for the police.")
    elif code == "OTHER_BANK_DOWNSTREAM":
        out.append(f"This account is at {bank_name(rec.get('bank_id'))}. The bank that paid it refers it onward.")
    else:
        out.append("There is no money left here to hold.")
    if rec.get("other_victims_inr") and Decimal(str(rec["other_victims_inr"])) > 0:
        out.append(f"The account also holds {inr(rec['other_victims_inr'])} of other victims' money, "
                   f"to be shared between victims pro rata (SOP para 5(xiii)).")
    if rec.get("needs_comment"):
        out.append("The assessment is borderline (REVIEW), so the officer must write a comment before approving.")
    return out