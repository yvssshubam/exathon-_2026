"""Proactive holds (DECISIONS D-60 to D-65), no database: the calendar and the draft's clocks, the percentile behind
the Fast mover's size check, the fixed pre-alert and call texts, and the demo data's scenarios."""
from decimal import Decimal

import pytest

from pipeline import proactive
from pipeline.cases import PROACTIVE_CASE, load_case

pro = load_case(PROACTIVE_CASE)


def test_a_hold_on_saturday_night_is_due_for_its_check_on_monday_at_six():
    assert proactive.next_day_check_due("2026-10-17 23:40:00") == "2026-10-19 18:00:00"     # Sunday skipped


def test_a_bank_holiday_is_skipped_too():
    assert proactive.next_day_check_due("2026-11-07 22:00:00") == "2026-11-10 18:00:00"     # Sun 8, holiday Mon 9


def test_the_draft_s_clocks_are_calendar_days_from_the_hold():
    c = proactive.clocks("2026-10-18 02:41:00")
    assert c["explanation_due_on"] == "2026-11-07 02:41:00"
    assert c["decide_by"] == "2026-11-17 02:41:00"
    assert c["must_decide_on"] == "2026-12-07 02:41:00"
    assert c["expires_on"] == "2026-12-17 02:41:00"


def test_an_explanation_on_day_12_moves_the_decision_to_day_22():
    placed = "2026-10-18 02:41:00"
    explained = proactive.add_days(placed, 12)
    assert proactive.add_days(explained, proactive.P["decide_after_explanation_days"]) == proactive.add_days(placed, 22)


def test_percentile_is_a_value_the_account_really_received():
    amounts = [Decimal(x) for x in (3000, 3000, 45000, 60000, 80000, 90000)]
    p90 = proactive.percentile(amounts, 90)
    assert p90 == Decimal(90000) and p90 in amounts
    assert proactive.percentile([], 90) is None
    assert proactive.percentile([Decimal(5)], 90) == Decimal(5)


def test_every_failed_check_has_plain_words():
    names = {"amount", "new_payer", "not_automatic", "not_reversal", "not_already_named", "fast_mover_extra"}
    assert names == set(proactive.CHECK_WORDS)
    for words in proactive.CHECK_WORDS.values():
        assert proactive.no_forbidden_words(words)


class _Result:
    def __init__(self, value):
        self.value = value

    def fetchval(self):
        return self.value


class _Ck:
    """Just enough of the checker for the alert template."""
    bank = "BANK_X"

    def _q(self, sql, **params):
        if "FROM ACCOUNT" in sql:
            return _Result("BANK_Y")
        return _Result("31231018000101")


def test_the_pre_alert_is_facts_only_and_never_says_victim_or_mule():
    hold = {"case_id": "CASE_P01", "legal_basis": "rbi_draft"}
    txn = {"ts": "2026-10-18 02:41:00", "amount_inr": Decimal("48000"), "utr": "IMPS6123202610180241",
           "from_account": "ACC_PY4417"}
    text = proactive.alert_text(_Ck(), hold, txn)
    assert text.startswith("At 02:41 on 18 Oct, Rs 48,000 (UTR IMPS6123202610180241) was paid from an account at "
                           "Bank Y (ending 4417)")
    assert "already under police notice 31231018000101 for a reported fraud" in text
    assert "temporary debit hold" in text and text.endswith(proactive.CLOSING)
    assert proactive.no_forbidden_words(text) and proactive.no_forbidden_words(proactive.CALL_SCRIPT)
    sop = proactive.alert_text(_Ck(), {**hold, "legal_basis": "police_notice"}, txn)
    assert "under the existing notice" in sop
    reg = proactive.alert_text(_Ck(), {**hold, "case_id": "REGISTRY:ACC_P07"}, txn)
    assert "listed in the I4C Suspect Registry" in reg


def test_the_demo_case_seeds_every_branch():
    txns = {t.txn_id: t for t in pro.txns}
    stranger, own, flagged, fast, regular = (txns[k] for k in ("T_PRO_07", "T_PRO_09", "T_PRO_10", "T_PRO_11",
                                                               "T_PRO_12"))
    bank = {a["account_id"]: a["bank_id"] for a in pro.accounts}
    assert bank[stranger.from_account] != "BANK_X" and stranger.to_account == "ACC_P10"           # A
    assert bank[fast.from_account] != "BANK_X" and fast.to_account == "ACC_P20"                   # B
    assert bank[own.from_account] == "BANK_X" and own.to_account == "ACC_P10"                     # C
    assert flagged.from_account == "ACC_P07" and flagged.to_account == "ACC_P10"                  # D
    assert any(h.from_account == regular.from_account and h.to_account == "ACC_P20" for h in pro.history)   # F
    assert all(t.amount_inr >= 1000 for t in (stranger, own, flagged, fast, regular))
    registry = {r["account_id"] for r in pro.sim_inputs["suspect_registry"]}
    named = {c["beneficiary_account"] for c in pro.complaints}
    assert registry & named and registry - named                     # one with a ticket, one without
    answers = {r["txn_id"]: r["response"] for r in pro.sim_inputs["payer_responses"]}
    assert answers == {"T_PRO_07": "reported_fraud", "T_PRO_09": "genuine"}
    assert pro.complaint("CASE_P03")["utr"] == stranger.utr          # A's payer complains later


@pytest.mark.parametrize("word", ["victim", "mule"])
def test_holder_and_layering_texts_avoid_the_words(word):
    src = (proactive.ROOT / "pipeline" / "proactive.py").read_text(encoding="utf-8")
    start = src.index("def holder_text(")
    end = src.index("def place(")
    assert word not in src[start:end].lower().replace("victim money", "")