"""Reference implementation of account scoring v2's signals (sql/steps/060_score.sql, DECISIONS D-57) in plain Python,
over the same rows the loader puts in Exasol. Used to predict each demo account's type without a database. Slow and
simple on purpose; the arithmetic after the signals is pipeline/scoring.py, the same functions the weight gate uses.

Two things the database knows and this file approximates: the traced trails (E2) are followed forward from each
reported payment within the trace's hop window, and an account counts as flagged once a ticket naming it has reached
the bank (or the Suspect Registry lists it); the database also counts accounts held under a notice.
"""
from __future__ import annotations

import statistics
from datetime import datetime, timedelta
from pathlib import Path

import yaml

from pipeline import scoring
from pipeline.cases import Case
from tests.oracle import replay

ROOT = Path(__file__).resolve().parents[1]
CONFIG = scoring.CONFIG
DEFAULTS = yaml.safe_load((ROOT / "config" / "case_defaults.yaml").read_text(encoding="utf-8"))
FMT = "%Y-%m-%d %H:%M:%S"
AUTOMATIC_CHANNELS = ("NACH", "AUTOPAY")
CASH_CHANNELS = ("ATM", "CASH", "CRYPTO")


def match_credits():
    """The MATCH_CREDITS UDF's own Python (sql/udf/match_credits.py)."""
    import importlib.util

    spec = importlib.util.spec_from_file_location("match_credits", ROOT / "sql" / "udf" / "match_credits.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _txns(rows: dict) -> list[dict]:
    return [dict(id=r[0], utr=r[1], ts=datetime.strptime(r[2][:19], FMT), f=r[3], t=r[4], amt=float(r[5]),
                 ch=r[6], exit=r[7] == "TRUE") for r in rows["TXN"]]


def speed(credits: list[dict], debits: list[dict], now: datetime) -> tuple[float | None, float | None]:
    """P1 (median minutes from each credit to the next debit, automatic payments left out) and P2 with the
    per-credit matching (each debit matched once)."""
    moved = [d for d in debits if d["ch"] not in AUTOMATIC_CHANNELS]
    dwell = []
    for c in credits:
        later = [d["ts"] for d in moved if d["ts"] >= c["ts"]]
        dwell.append(((min(later) if later else now) - c["ts"]).total_seconds() / 60)
    matched = matched_credits(credits, debits, set())
    total_in = sum(c["amt"] for c in credits)
    passed = sum(m["out_paise"] for m in matched) / 100
    return (statistics.median(dwell) if dwell else None), (passed / total_in if total_in else None)


def matched_credits(credits: list[dict], debits: list[dict], fraud: set[str]) -> list[dict]:
    events = [{"direction": "C", "ts": c["ts"], "txn_id": c["id"], "amount_paise": round(c["amt"] * 100),
               "exit_kind": "NONE", "fraud_linked": c["id"] in fraud} for c in credits]
    events += [{"direction": "D", "ts": d["ts"], "txn_id": d["id"], "amount_paise": round(d["amt"] * 100),
                "exit_kind": "AUTO" if d["ch"] in AUTOMATIC_CHANNELS else "CASH" if d["ch"] in CASH_CHANNELS
                else "MOVE", "fraud_linked": False} for d in debits]
    return match_credits().match(events)


def reported(rows: dict, as_of: str) -> list[dict]:
    """Tickets that had reached the bank by as_of (an IO notice repeats an earlier ticket)."""
    return [c for c in rows["COMPLAINTS"] if str(c["reported_at"]) <= as_of
            and c.get("ticket_kind", "POLICE") != "IO_NOTICE"]


def flagged(rows: dict, as_of: str) -> set[str]:
    out = {c["beneficiary_account"] for c in reported(rows, as_of) if c.get("beneficiary_account")}
    return out | {r[0] for r in rows.get("SUSPECT_REGISTRY", []) if str(r[1])[:19] <= as_of}


def trails(rows: dict, txns: list[dict], as_of: str) -> dict[str, dict[str, datetime]]:
    """account -> {victim_tag: reported_at} for every reported trail that reached it (forward, within the hop
    window, before each ticket's hold time)."""
    out: dict[str, dict[str, datetime]] = {}
    by_from: dict[str, list[dict]] = {}
    for t in txns:
        by_from.setdefault(t["f"], []).append(t)
    for c in reported(rows, as_of):
        root = next((t for t in txns if t["utr"] == c["utr"]), None)
        if root is None:
            continue
        cut = datetime.strptime(str(c["reported_at"])[:19], FMT) + timedelta(minutes=int(DEFAULTS["hold_minutes"]))
        frontier, seen = [(root, 1)], {root["id"]}
        while frontier:
            t, hop = frontier.pop()
            out.setdefault(t["t"], {})[c["victim_tag"]] = datetime.strptime(str(c["reported_at"])[:19], FMT)
            if hop >= int(DEFAULTS["max_hops"]):
                continue
            for n in by_from.get(t["t"], []):
                if n["id"] not in seen and t["ts"] <= n["ts"] <= min(cut, t["ts"] + timedelta(
                        hours=int(DEFAULTS["window_hours"]))):
                    seen.add(n["id"])
                    frontier.append((n, hop + 1))
    return out


def signals(rows: dict, case: Case | None, account: str, as_of: str, attribution: bool = True) -> dict:
    """v2 signal values for one own account at as_of (None where the SQL would have no value). attribution=False
    leaves P4 out, as a watch rescoring does (no ticket, so no victim money traced in)."""
    now = datetime.strptime(as_of, FMT)
    txns = [t for t in _txns(rows) if t["ts"] <= now]
    window = [t for t in txns if t["ts"] > now - timedelta(days=90)]
    credits = [x for x in window if x["t"] == account]
    debits = [x for x in window if x["f"] == account]
    roots = {c["utr"]: c for c in reported(rows, as_of)}
    flags = flagged(rows, as_of)
    v: dict = {}
    v["median_dwell_minutes"], v["passed_on_within_hour"] = speed(credits, debits, now)
    fraud = {c["id"] for c in credits if c["f"] in flags or c["utr"] in roots}
    matched = matched_credits(credits, debits, fraud)
    total_in = sum(c["amt"] for c in credits)
    v["cashed_out_within_hour"] = (sum(m["cash_paise"] for m in matched) / 100 / total_in) if total_in else None
    v["balance_kept_ratio"] = None
    if attribution and case is not None:
        result = replay([t for t in _oracle(case)], "LIBR", case.opening_paise(), as_of)
        into = {t.txn_id for t in case.txns if t.to_account == account}
        tainted_in = sum(p for txn_id, comp, p in result.moves if comp != "clean" and txn_id in into) / 100
        balance = sum(result.holdings.get(account, {}).values()) / 100
        v["balance_kept_ratio"] = balance / tainted_in if tainted_in else None
    by_payer: dict[str, float] = {}
    for c in credits:
        if c["ch"] != "NACH":
            by_payer[c["f"]] = by_payer.get(c["f"], 0) + c["amt"]
    top = sorted(by_payer.values(), reverse=True)
    v["top3_payer_share"] = sum(top[:3]) / sum(top) if top else None
    direct = {roots[c["utr"]]["victim_tag"] for c in credits if c["utr"] in roots and c["ts"] > now - timedelta(days=7)}
    v["direct_complaints_7d"] = float(len(direct)) if direct else None
    touched = trails(rows, txns, as_of).get(account, {})
    v["trail_touches_90d"] = (sum(1.0 if at > now - timedelta(days=30) else 0.5 for at in touched.values())
                              if touched else None)
    people = [c for c in credits if c["ch"] != "NACH"]
    people_in = sum(c["amt"] for c in people)
    v["money_from_flagged_share"] = (sum(c["amt"] for c in people if c["f"] in flags) / people_in
                                     if people_in else None)
    paid = {d["t"] for d in debits if not d["exit"]}
    payers = {c["f"] for c in credits}
    v["pays_back_its_payers"] = len(paid & payers) / len(paid) if paid else None
    fraud_in = sum(c["amt"] for c in credits if c["id"] in fraud)
    other_in = total_in - fraud_in
    out = {m["txn_id"]: m["out_paise"] / 100 for m in matched}
    v["_fraud_out_share"] = sum(out[c] for c in fraud) / fraud_in if fraud_in else None
    v["_other_out_share"] = (sum(out[c["id"]] for c in credits if c["id"] not in fraud) / other_in
                             if other_in else None)
    v["_fraud_money_share"] = fraud_in / total_in if total_in else None
    return v


def _oracle(case: Case):
    from tests.helpers import oracle_txns

    return oracle_txns(case)


def classify(values: dict, never_full: bool = False) -> dict:
    """Scores, type and ladder from measured values (pipeline/scoring.py)."""
    acc = {"values": {k: v for k, v in values.items() if not k.startswith("_")},
           "fraud_out_share": values.get("_fraud_out_share"), "other_out_share": values.get("_other_out_share"),
           "cases": values.get("trail_touches_90d") or 0, "fraud_money_share": values.get("_fraud_money_share"),
           "never_full": never_full}
    return scoring.classify(acc)