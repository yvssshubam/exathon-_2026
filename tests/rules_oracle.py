"""Plain-Python reference for the trace, the rules engine and the checker (sql/steps/040, 070, 075).

Like tests/oracle.py, it shares no code with the SQL: it reads config/rules.yaml and the case files and works the
ticket the slow, obvious way, so the Exasol results can be compared with it and the rules can be tested in CI.
"""
from __future__ import annotations

from collections import deque
from datetime import datetime, timedelta
from decimal import Decimal
from pathlib import Path

import yaml

from pipeline.cases import Case, TxnRow, bank_view
from tests.helpers import run
from tests.score_oracle import classify, signals

ROOT = Path(__file__).resolve().parents[1]
RULES = yaml.safe_load((ROOT / "config" / "rules.yaml").read_text(encoding="utf-8"))
DEFAULTS = yaml.safe_load((ROOT / "config" / "case_defaults.yaml").read_text(encoding="utf-8"))
FMT = "%Y-%m-%d %H:%M:%S"


def _t(ts: str) -> datetime:
    return datetime.strptime(ts[:19], FMT)


def trace(case: Case, complaint: dict, as_of: str, max_hops: int | None = None,
          window_hours: int | None = None, own_bank: str | None = None) -> dict[str, tuple[TxnRow, int]]:
    """txn_id -> (transaction, shortest hop), following the money forward like 040_trace.sql: only out of the
    viewing bank's own accounts (another bank's account ends the path) when own_bank is given."""
    max_hops = max_hops or DEFAULTS["max_hops"]
    window = timedelta(hours=window_hours or DEFAULTS["window_hours"])
    cut = _t(as_of)
    root = next(t for t in case.txns if t.utr == complaint["utr"])
    if _t(root.ts) > cut:
        return {}
    found: dict[str, tuple[TxnRow, int]] = {root.txn_id: (root, 1)}
    queue = deque([(root, 1)])
    own = {a["account_id"] for a in case.accounts if a["bank_id"] == own_bank} if own_bank else None
    while queue:
        parent, hop = queue.popleft()
        if hop >= max_hops or (own is not None and parent.to_account not in own):
            continue
        for child in case.txns:
            if (child.from_account == parent.to_account and child.txn_id != parent.txn_id
                    and _t(parent.ts) <= _t(child.ts) <= min(_t(parent.ts) + window, cut)):
                if child.txn_id not in found or found[child.txn_id][1] > hop + 1:
                    found[child.txn_id] = (child, hop + 1)
                    queue.append((child, hop + 1))
    return found


def facts(case: Case, rows: dict, own_bank: str, complaint: dict | None = None, as_of: str | None = None,
          evidence: bool = True) -> dict:
    """One dict per account or exit on the trail: what CASE_ACCOUNT holds. evidence=False skips the (slow)
    signals, which only the officer proposals read."""
    complaint = complaint or case.complaints[0]
    as_of = as_of or case.tool_as_of
    victim = complaint["victim_tag"]
    case = bank_view(case, own_bank)            # what this bank sees: its own accounts' transactions
    edges = trace(case, complaint, as_of, own_bank=own_bank)
    banks = {a["account_id"]: a for a in case.accounts}
    r = run(case, "LIBR", as_of)
    carried = {}
    for txn_id, component, paise in r.moves:
        if component == victim:
            carried[txn_id] = carried.get(txn_id, 0) + paise
    everywhere = sum(r.tainted(acc, victim) for acc in {*r.holdings, *r.exits})
    out: dict[str, dict] = {}
    for txn, hop in edges.values():
        acc = txn.to_account
        f = out.setdefault(acc, {"account_id": acc, "hop": hop, "is_exit": txn.is_exit, "trail": 0,
                                 "paid_by_own": False, "onward": 0})
        f["hop"] = min(f["hop"], hop)
        f["trail"] += txn.amount_paise
        payer = banks.get(txn.from_account)
        if payer and payer["bank_id"] == own_bank and hop > 1:
            f["paid_by_own"] = True
            if not txn.is_exit and banks.get(acc, {}).get("bank_id") != own_bank:
                f["onward"] += carried.get(txn.txn_id, 0)
    for acc, f in out.items():
        a = banks.get(acc)
        f["bank_id"] = a["bank_id"] if a else None
        f["is_own"] = bool(a) and a["bank_id"] == own_bank
        f["never_full"] = bool(a) and a["account_type"] in ("nodal", "pool", "escrow")
        f["victim"] = r.tainted(acc, victim)
        f["others"] = r.tainted(acc) - f["victim"]
        f["balance"] = sum(r.holdings.get(acc, {}).values())
        f["reported"] = int(round(float(complaint["amount_inr"]) * 100))
        f["victim_everywhere"] = everywhere
        if f["is_own"] and not f["is_exit"]:
            named = f["trail"] if complaint.get("notice_has_amount", True) else f["victim"] + f["others"]
            f["hold"] = min(named, f["balance"], f["reported"])
        else:
            f["hold"] = 0
        f["pattern"] = f["ladder"] = f["fraud_linked"] = None
        if evidence and f["is_own"] and not f["is_exit"]:
            values = signals(rows, case, acc, as_of)
            c = classify(values, f["never_full"])
            f["pattern"], f["ladder"] = c["account_type"], c["ladder"]
            f["fraud_linked"] = values.get("_fraud_money_share")
    return out


def actions(facts_by_account: dict) -> list[dict]:
    """The rules engine: every action, automatic and officer, as in 070_actions.sql. Amounts in paise."""
    out = []
    for f in sorted(facts_by_account.values(), key=lambda f: (f["hop"], f["account_id"])):
        if f["is_exit"]:
            code = "RECORD_EXIT" if f["paid_by_own"] else "NONE"
            amount = f["victim"] if code == "RECORD_EXIT" else None
        elif not f["is_own"]:
            code = "ONWARD_ENTRY" if f["paid_by_own"] else "NONE"
            amount = f["onward"] if code == "ONWARD_ENTRY" else None
        elif f["hold"] <= 0:
            code = "HOLD_MARKER" if f["hop"] == 1 and not f["never_full"] else "NONE"
            amount = min(f["trail"], f["reported"]) if code == "HOLD_MARKER" else None
        else:
            code, amount = "MANDATORY_HOLD", f["hold"]
        out.append({"account_id": f["account_id"], "action_code": code, "mode": "INFO" if code == "NONE"
                    else "AUTOMATIC", "amount": amount})
        if not f["is_own"] or f["is_exit"]:
            continue
        tainted = f["victim"] + f["others"]
        # what the account type allows (D-57): shrink only an Ordinary customer (or nodal, pool, escrow); the
        # freeze ladder only on a Pipe for fraud or an Exposed business
        if f["hold"] > 0 and tainted < f["hold"] and (f["pattern"] == "ORDINARY_CUSTOMER" or f["never_full"]):
            out.append({"account_id": f["account_id"], "action_code": "PROPOSE_NARROW", "mode": "OFFICER",
                        "amount": tainted})
        elif f["pattern"] in ("PIPE_FOR_FRAUD", "EXPOSED_BUSINESS") and f["ladder"] == "STRONG" and not f["never_full"]:
            out.append({"account_id": f["account_id"], "action_code": "PROPOSE_FULL_FREEZE", "mode": "OFFICER",
                        "amount": f["balance"]})
    return out


def check(case: Case, facts_by_account: dict, acts: list[dict], complaint: dict | None = None) -> dict[str, bool]:
    """The checker, as in 075_check.sql."""
    complaint = complaint or case.complaints[0]
    paid = min(sum(t.amount_paise for t in case.txns if t.utr == complaint["utr"]),
               int(round(float(complaint["amount_inr"]) * 100)))
    # the victim's money anywhere in the bank's view: on the trail, plus any that came back into one of the bank's
    # own accounts through another bank (that bank files its onward entry; this bank's trace stops at the border)
    on_trail = next(iter(facts_by_account.values()), {}).get("victim_everywhere",
                                                             sum(f["victim"] for f in facts_by_account.values()))
    holds = [(a, facts_by_account[a["account_id"]]) for a in acts if a["action_code"] == "MANDATORY_HOLD"]
    return {
        "conservation": on_trail == paid,
        "holds_within_balance": all(a["amount"] <= f["balance"] for a, f in holds),
        "holds_within_trail": all(a["amount"] <= f["trail"] for a, f in holds),
        "holds_within_report": all(a["amount"] <= f["reported"] for a, f in holds),
        "automatic_allowed": all(a["action_code"] in ("MANDATORY_HOLD", "HOLD_MARKER", "RECORD_EXIT", "ONWARD_ENTRY")
                                 for a in acts if a["mode"] == "AUTOMATIC"),
        "own_accounts_only": all(facts_by_account[a["account_id"]]["is_own"] for a in acts
                                 if a["action_code"] in ("MANDATORY_HOLD", "HOLD_MARKER")),
        "never_full_respected": not any(facts_by_account[a["account_id"]]["never_full"] for a in acts
                                        if a["action_code"] == "PROPOSE_FULL_FREEZE"),
    }


def rupees(paise: int | None) -> Decimal | None:
    return None if paise is None else Decimal(paise) / 100