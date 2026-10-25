"""Reference implementation of the eight scoring signals (sql/steps/060_score.sql, DECISIONS D-22) in plain Python.

Used to check the SQL's numbers and to predict bands without Exasol. Slow and simple on purpose.
"""
from __future__ import annotations

import statistics
from datetime import datetime, timedelta
from pathlib import Path

import yaml

from pipeline.cases import Case
from tests.oracle import replay

ROOT = Path(__file__).resolve().parents[1]
CONFIG = yaml.safe_load((ROOT / "config" / "scoring.yaml").read_text(encoding="utf-8"))
FMT = "%Y-%m-%d %H:%M:%S"


def _txns(rows: dict) -> list[dict]:
    return [dict(id=r[0], utr=r[1], ts=datetime.strptime(r[2][:19], FMT), f=r[3], t=r[4], amt=float(r[5]),
                 ch=r[6], exit=r[7] == "TRUE") for r in rows["TXN"]]


def signals(rows: dict, case: Case, account: str, as_of: str) -> dict[str, float | None]:
    """Signal values for one account. rows: pipeline.load_cases.rows_for(...); case: the account's case."""
    now = datetime.strptime(as_of, FMT)
    txns = _txns(rows)
    acct = {r[0]: r for r in rows["ACCOUNT"]}[account]
    # reported payments: tickets that had reached the bank by as_of (an IO notice repeats an earlier ticket)
    roots = {c["utr"] for c in rows["COMPLAINTS"]
             if str(c["reported_at"]) <= as_of and c.get("ticket_kind", "POLICE") != "IO_NOTICE"}
    credits = [x for x in txns if x["t"] == account and now - timedelta(days=90) < x["ts"] <= now]
    debits = [x for x in txns if x["f"] == account and x["ts"] <= now]
    v: dict[str, float | None] = {}
    v["account_age_days"] = (now.date() - datetime.strptime(acct[3], "%Y-%m-%d").date()).days
    dwell, passed = [], 0.0
    for c in credits:
        later = [d["ts"] for d in debits if d["ts"] >= c["ts"]]
        dwell.append(((min(later) if later else now) - c["ts"]).total_seconds() / 60)
        passed += min(c["amt"], sum(d["amt"] for d in debits if c["ts"] <= d["ts"] <= c["ts"] + timedelta(hours=1)))
    total_in = sum(c["amt"] for c in credits)
    v["median_dwell_minutes"] = statistics.median(dwell) if dwell else None
    v["pass_through_ratio"] = passed / total_in if total_in else None
    v["complaint_linked_credits_7d"] = len({c["utr"] for c in credits
                                            if c["utr"] in roots and c["ts"] > now - timedelta(days=7)})
    by_payer: dict[str, float] = {}
    for c in credits:
        by_payer[c["f"]] = by_payer.get(c["f"], 0) + c["amt"]
    top = sorted(by_payer.values(), reverse=True)
    v["large_payer_share"] = sum(top[:3]) / sum(top) if top else None
    from tests.helpers import oracle_txns

    result = replay(oracle_txns(case), "LIBR", case.opening_paise(), as_of)
    into = {t.txn_id for t in case.txns if t.to_account == account}
    tainted_in = sum(p for txn_id, comp, p in result.moves if comp != "clean" and txn_id in into) / 100
    balance = sum(result.holdings.get(account, {}).values()) / 100
    v["balance_retained_ratio"] = balance / tainted_in if tainted_in else None
    v["cash_or_crypto_exits"] = len([d for d in debits if d["exit"] and d["ch"] in ("ATM", "CASH", "CRYPTO")
                                     and d["ts"] > now - timedelta(days=90)])
    expected = CONFIG["profile_band_expected_monthly_credit_inr"][acct[6]]
    v["credit_vs_profile"] = sum(c["amt"] for c in credits if c["ts"] > now - timedelta(days=30)) / expected
    return v


def score(values: dict[str, float | None]) -> tuple[float, str]:
    total_weight = sum(s["weight"] for s in CONFIG["signals"].values())
    total = 0.0
    for name, s in CONFIG["signals"].items():
        x = values.get(name)
        m = 0.0 if x is None else max(0.0, min(1.0, (x - s["honest_at"]) / (s["mule_at"] - s["honest_at"])))
        total += s["weight"] * m / total_weight
    bands = CONFIG["bands"]
    band = "MULE" if total >= bands["mule"] else "REVIEW" if total >= bands["review"] else "HONEST"
    return total, band