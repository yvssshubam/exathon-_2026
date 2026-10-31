"""Account scoring v2 in plain Python (DECISIONS D-57 to D-59): the same arithmetic as sql/steps/060_score.sql and
070_actions.sql, used by the tests, the weight gate (pipeline/weights.py) and anyone who wants to check a number by
hand. Exasol does the real work; this file only repeats the rules on values that are already measured.

    m      = clamp((value - low_at) / (high_at - low_at), 0, 1)       a missing value counts as m = 0
    score  = sum(weight * m) / sum(weight)                              per score, pass-through and fraud exposure
    type   = Pipe for fraud, Fast mover, Exposed business, Ordinary customer, or the grey zone (0.4 to under 0.6)
    ladder = NONE unless fraud money leaves fast (E4); then WEAK, MEDIUM or STRONG by the number of fraud cases

The five demo accounts of the scoring doc are here too, with the type and actions each must get: a new set of
weights that moves any of them is refused (pipeline/weights.py check).
"""
from __future__ import annotations

from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
CONFIG = yaml.safe_load((ROOT / "config" / "scoring.yaml").read_text(encoding="utf-8"))
RULES = yaml.safe_load((ROOT / "config" / "rules.yaml").read_text(encoding="utf-8"))
SCORES = ("pass_through", "fraud_exposure")
HIGH_AT = float(CONFIG["cutoffs"]["high_at"])
LOW_BELOW = float(CONFIG["cutoffs"]["low_below"])
TYPE_NAMES = {k: v["name"] for k, v in CONFIG["account_types"].items()}


def signals(score_name: str) -> dict[str, dict]:
    return CONFIG[score_name]


def start_weights() -> dict[str, dict[str, float]]:
    """Version 0: the starting weights in config/scoring.yaml."""
    return {s: {name: float(sig["start_weight"]) for name, sig in signals(s).items()} for s in SCORES}


def m_value(score_name: str, signal_name: str, value: float | None) -> float:
    if value is None:
        return 0.0
    sig = signals(score_name)[signal_name]
    low, high = float(sig["low_at"]), float(sig["high_at"])
    return max(0.0, min(1.0, (float(value) - low) / (high - low)))


def score(score_name: str, values: dict, weights: dict[str, float] | None = None) -> float:
    weights = weights or start_weights()[score_name]
    total = sum(weights.values())
    return sum(weights[n] * m_value(score_name, n, values.get(n)) for n in signals(score_name)) / total


def scores(values: dict, weights: dict | None = None) -> tuple[float, float]:
    weights = weights or start_weights()
    return score("pass_through", values, weights["pass_through"]), score("fraud_exposure", values,
                                                                          weights["fraud_exposure"])


def account_type(pass_through: float, fraud_exposure: float) -> str:
    """The four types and the grey zone. The order matches 060_score.sql."""
    if pass_through >= HIGH_AT and fraud_exposure >= HIGH_AT:
        return "PIPE_FOR_FRAUD"
    if pass_through >= HIGH_AT and fraud_exposure < LOW_BELOW:
        return "FAST_MOVER"
    if pass_through < LOW_BELOW and fraud_exposure >= HIGH_AT:
        return "EXPOSED_BUSINESS"
    if pass_through < LOW_BELOW and fraud_exposure < LOW_BELOW:
        return "ORDINARY_CUSTOMER"
    return "GREY_ZONE"


def fraud_money_leaves_fast(fraud_out_share: float | None, other_out_share: float | None) -> bool:
    """E4: fraud-linked money leaves within the hour at 50% or more, and at least 30 points faster than the rest."""
    fast = CONFIG["fast_exit_check"]
    if fraud_out_share is None:
        return False
    return (fraud_out_share >= fast["fraud_money_out_within_hour_at_least"]
            and fraud_out_share - (other_out_share or 0.0) >= fast["gap_over_other_money_at_least"])


def ladder_step(leaves_fast: bool, cases: float, fraud_money_share: float | None, never_full: bool = False) -> str:
    """The freeze ladder (D-59). The count alone never starts a suggestion."""
    lad = CONFIG["freeze_ladder"]
    if not leaves_fast:
        return "NONE"
    if (fraud_money_share or 0.0) < lad["weak_only_if_fraud_money_share_below"]:
        return "WEAK"
    if cases >= lad["strong_at_cases"] and not never_full:
        return "STRONG"
    if cases >= lad["medium_at_cases"]:
        return "MEDIUM"
    return "WEAK"


def suggestions(pattern: str, ladder: str, never_full: bool = False, refused_withdrawal: bool = False,
                holds_more_than_victims: bool = True) -> dict:
    """What the account type allows on top of the mandatory hold (config/rules.yaml account_types)."""
    allowed = RULES["account_types"][pattern]
    proactive = bool(allowed["proactive"]) and not never_full
    shrink = holds_more_than_victims and (allowed["shrink"] == "yes" or never_full)
    freeze = None
    if refused_withdrawal and pattern in RULES["full_freeze_evidence"]["refused_withdrawal_types"]:
        freeze = "STRONG"
    elif allowed["freeze"] == "ladder" and ladder in ("MEDIUM", "STRONG"):
        freeze = ladder
    if never_full and freeze == "STRONG":
        freeze = CONFIG["freeze_ladder"]["nodal_pool_escrow_max_step"].upper()
    if shrink:
        freeze = None           # one account type, one meaning: never both a shrink and a freeze suggestion
    return {"proactive": proactive, "shrink": shrink, "freeze": freeze}


# The five demo accounts of the scoring doc (tests and the weight gate). Values are the measured signals.
DEMO_ACCOUNTS = {
    "classic_mule": {
        "about": "20 days old, money out in about 10 minutes, 2 victims this week, 40% of money from flagged accounts",
        "values": {"median_dwell_minutes": 10, "passed_on_within_hour": 0.90, "cashed_out_within_hour": 0.60,
                   "balance_kept_ratio": 0.1, "top3_payer_share": 0.90, "direct_complaints_7d": 2,
                   "trail_touches_90d": 3, "money_from_flagged_share": 0.40, "pays_back_its_payers": 0.05},
        "fraud_out_share": 0.92, "other_out_share": 0.90, "cases": 3, "fraud_money_share": 0.45,
        "expected_type": "PIPE_FOR_FRAUD"},
    "murugan_fuel_pump": {
        "about": "Years old, about 300 small payers a day, one Rs 1,000 victim payment",
        "values": {"median_dwell_minutes": 480, "passed_on_within_hour": 0.10, "cashed_out_within_hour": 0.0,
                   "balance_kept_ratio": 40, "top3_payer_share": 0.12, "direct_complaints_7d": 1,
                   "trail_touches_90d": 1, "money_from_flagged_share": 0.01, "pays_back_its_payers": 0.0},
        "fraud_out_share": 0.0, "other_out_share": 0.10, "cases": 1, "fraud_money_share": 0.001,
        "expected_type": "ORDINARY_CUSTOMER"},
    "jeweller_helping_the_ring": {
        "about": "Old account, keeps a balance, 30% of money from flagged accounts, fraud-linked money cashed out "
                 "within an hour, 3 cases",
        "values": {"median_dwell_minutes": 600, "passed_on_within_hour": 0.25, "cashed_out_within_hour": 0.10,
                   "balance_kept_ratio": 10, "top3_payer_share": 0.30, "direct_complaints_7d": 3,
                   "trail_touches_90d": 3, "money_from_flagged_share": 0.30, "pays_back_its_payers": 0.20},
        "fraud_out_share": 0.70, "other_out_share": 0.10, "cases": 3, "fraud_money_share": 0.30,
        "expected_type": "EXPOSED_BUSINESS"},
    "honest_fuel_pump_near_the_ring": {
        "about": "Same exposure as the jeweller, but fraud-linked money stays like its normal takings",
        "values": {"median_dwell_minutes": 600, "passed_on_within_hour": 0.25, "cashed_out_within_hour": 0.10,
                   "balance_kept_ratio": 10, "top3_payer_share": 0.30, "direct_complaints_7d": 3,
                   "trail_touches_90d": 3, "money_from_flagged_share": 0.30, "pays_back_its_payers": 0.20},
        "fraud_out_share": 0.12, "other_out_share": 0.10, "cases": 3, "fraud_money_share": 0.30,
        "expected_type": "EXPOSED_BUSINESS"},
    "salaried_person_who_sold_a_phone": {
        "about": "Salary by NACH from one employer, one victim payment",
        "values": {"median_dwell_minutes": 4000, "passed_on_within_hour": 0.05, "cashed_out_within_hour": 0.0,
                   "balance_kept_ratio": 8, "top3_payer_share": 1.0, "direct_complaints_7d": 1,
                   "trail_touches_90d": 1, "money_from_flagged_share": 0.0, "pays_back_its_payers": 0.0},
        "fraud_out_share": 0.0, "other_out_share": 0.05, "cases": 1, "fraud_money_share": 0.2,
        "expected_type": "ORDINARY_CUSTOMER"},
}


def classify(acc: dict, weights: dict | None = None) -> dict:
    """Scores, type, E4, ladder and suggestions for one demo account (or any dict of the same shape)."""
    pt, fe = scores(acc["values"], weights)
    pattern = account_type(pt, fe)
    fast = fraud_money_leaves_fast(acc.get("fraud_out_share"), acc.get("other_out_share"))
    ladder = ladder_step(fast, acc.get("cases", 0), acc.get("fraud_money_share"), acc.get("never_full", False))
    return {"pass_through": pt, "fraud_exposure": fe, "account_type": pattern, "leaves_fast": fast,
            "ladder": ladder, **suggestions(pattern, ladder, acc.get("never_full", False),
                                            acc.get("refused_withdrawal", False))}


def demo_types_hold(weights: dict | None = None) -> list[str]:
    """The demo accounts whose type a set of weights would change (empty: the gate passes)."""
    return [name for name, acc in DEMO_ACCOUNTS.items()
            if classify(acc, weights)["account_type"] != acc["expected_type"]]