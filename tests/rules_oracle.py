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

from pipeline.cases import Case, TxnRow
from tests.helpers import run
from tests.score_oracle import score, signals

ROOT = Path(__file__).resolve().parents[1]
RULES = yaml.safe_load((ROOT / "config" / "rules.yaml").read_text(encoding="utf-8"))
DEFAULTS = yaml.safe_load((ROOT / "config" / "case_defaults.yaml").read_text(encoding="utf-8"))
FMT = "%Y-%m-%d %H:%M:%S"


def _t(ts: str) -> datetime:
    return datetime.strptime(ts[:19], FMT)


def trace(case: Case, complaint: dict, as_of: str, max_hops: int | None = None,
          window_hours: int | None = None) -> dict[str, tuple[TxnRow, int]]:
    """txn_id -> (transaction, shortest hop), following the money forward like 040_trace.sql."""
    max_hops = max_hops or DEFAULTS["max_hops"]
    window = timedelta(hours=window_hours or DEFAULTS["window_hours"])
    cut = _t(as_of)
    root = next(t for t in case.txns if t.utr == complaint["utr"])
    if _t(root.ts) > cut:
        return {}
    found: dict[str, tuple[TxnRow, int]] = {root.txn_id: (root, 1)}
    queue = deque([(root, 1)])
    while queue:
        parent, hop = queue.popleft()
        if hop >= max_hops:
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
    """One dict per account or exit on the trail: what PH.CASE_ACCOUNT holds. evidence=False skips the (slow)
    signals, which only the officer proposals read."""
    complaint = complaint or case.complaints[0]
    as_of = as_of or case.tool_as_of
    victim = complaint["victim_tag"]
    edges = trace(case, complaint, as_of)
    banks = {a["account_id"]: a for a in case.accounts}
    r = run(case, "LIBR", as_of)
    carried = {}
    for txn_id, component, paise in r.moves:
        if component == victim:
            carried[txn_id] = carried.get(txn_id, 0) + paise
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
        if f["is_own"] and not f["is_exit"]:
            named = f["trail"] if complaint.get("notice_has_amount", True) else f["victim"] + f["others"]
            f["hold"] = min(named, f["balance"])
        else:
            f["hold"] = 0
        f["band"] = f["pass_through"] = f["cash_exits"] = f["complaint_links"] = None
        if evidence and f["is_own"] and not f["is_exit"]:
            values = signals(rows, case, acc, as_of)
            f["band"] = score(values)[1]
            f["pass_through"] = values["pass_through_ratio"]
            f["cash_exits"] = values["cash_or_crypto_exits"]
            f["complaint_links"] = values["complaint_linked_credits_7d"]
    return out


def actions(facts_by_account: dict) -> list[dict]:
    """The rules engine: every action, automatic and officer, as in 070_actions.sql. Amounts in paise."""
    ev = RULES["full_freeze_evidence"]
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
            amount = None
        else:
            code, amount = "MANDATORY_HOLD", f["hold"]
        out.append({"account_id": f["account_id"], "action_code": code, "mode": "INFO" if code == "NONE"
                    else "AUTOMATIC", "amount": amount})
        if not f["is_own"] or f["is_exit"]:
            continue
        tainted = f["victim"] + f["others"]
        if f["hold"] > 0 and tainted < f["hold"] and (f["band"] in ("HONEST", "REVIEW") or f["never_full"]):
            out.append({"account_id": f["account_id"], "action_code": "PROPOSE_NARROW", "mode": "OFFICER",
                        "amount": tainted})
        pattern = ((f["complaint_links"] or 0) >= ev["complaint_links_min"]
                   or ((f["pass_through"] or 0) >= ev["pass_through_min"]
                       and (f["cash_exits"] or 0) >= ev["cash_exits_min"]))
        if f["band"] == "MULE" and not f["never_full"] and pattern:
            out.append({"account_id": f["account_id"], "action_code": "PROPOSE_FULL_FREEZE", "mode": "OFFICER",
                        "amount": f["balance"]})
        if f["band"] == "REVIEW" and not f["never_full"] and f["hold"] > 0 and tainted >= f["hold"]:
            out.append({"account_id": f["account_id"], "action_code": "CONFIRM_HOLD", "mode": "OFFICER",
                        "amount": f["hold"]})
    return out


def check(case: Case, facts_by_account: dict, acts: list[dict], complaint: dict | None = None) -> dict[str, bool]:
    """The checker, as in 075_check.sql."""
    complaint = complaint or case.complaints[0]
    paid = sum(t.amount_paise for t in case.txns if t.utr == complaint["utr"])
    on_trail = sum(f["victim"] for f in facts_by_account.values())
    holds = [(a, facts_by_account[a["account_id"]]) for a in acts if a["action_code"] == "MANDATORY_HOLD"]
    return {
        "conservation": on_trail == paid,
        "holds_within_balance": all(a["amount"] <= f["balance"] for a, f in holds),
        "holds_within_trail": all(a["amount"] <= f["trail"] for a, f in holds),
        "automatic_allowed": all(a["action_code"] in ("MANDATORY_HOLD", "HOLD_MARKER", "RECORD_EXIT", "ONWARD_ENTRY")
                                 for a in acts if a["mode"] == "AUTOMATIC"),
        "own_accounts_only": all(facts_by_account[a["account_id"]]["is_own"] for a in acts
                                 if a["action_code"] in ("MANDATORY_HOLD", "HOLD_MARKER")),
        "never_full_respected": not any(facts_by_account[a["account_id"]]["never_full"] for a in acts
                                        if a["action_code"] == "PROPOSE_FULL_FREEZE"),
    }


def rupees(paise: int | None) -> Decimal | None:
    return None if paise is None else Decimal(paise) / 100