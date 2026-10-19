"""Plain-language wording of recommendations (no database)."""
from decimal import Decimal

from pipeline.explain import explain, headline, inr


def rec(**kw):
    base = dict(account_id="ACC_D", bank_id="BANK_Z", hop=3, action_code="AMOUNT_HOLD", amount_inr=Decimal("1000"),
                notice_amount_inr=Decimal("1000"), victim_amount_inr=Decimal("500"), other_victims_inr=Decimal("0"),
                balance_now_inr=Decimal("820000"), band="HONEST", score=0.01, lane="A", trace_rule="LIBR",
                legal_basis="S.168 read with S.94 BNSS", reason_code="LANE_A_HONEST_OR_REVIEW",
                draft_narrowing=True, draft_seizure=False, last_resort=False, needs_comment=False)
    base.update(kw)
    return base


def test_indian_grouping():
    assert inr(119500) == "Rs 1,19,500"
    assert inr(Decimal("819000")) == "Rs 8,19,000"
    assert inr(Decimal("498.75")) == "Rs 498.75"
    assert inr(200000) == "Rs 2,00,000"
    assert inr(1000) == "Rs 1,000"
    assert inr(None) == "Rs 0"


def test_murugan_hold_amount_only_with_narrowing():
    r = rec()
    assert headline(r) == "Hold Rs 1,000 only"
    text = " ".join(explain(r))
    assert "not the whole account" in text and "Rs 500" in text and "narrow" in text
    assert "seizure" not in text


def test_mule_gets_seizure_request_not_a_freeze():
    r = rec(account_id="ACC_C", bank_id="BANK_X", amount_inr=Decimal("80000"), victim_amount_inr=Decimal("80000"),
            band="MULE", draft_narrowing=False, draft_seizure=True, reason_code="LANE_A_MULE")
    text = " ".join(explain(r))
    assert "Hold Rs 80,000 only" in text and "S.106" in text


def test_referral_and_exit_wording():
    assert headline(rec(action_code="REFER_OUT", bank_id="BANK_Y", amount_inr=Decimal("119500"))) == \
        "Refer Rs 1,19,500 to Bank Y"
    exit_rec = rec(account_id="EXIT:ATM:BY-ATM-0042", action_code="NONE", reason_code="EXIT", band=None)
    assert headline(exit_rec) == "Record the exit"
    assert "cannot be held" in explain(exit_rec)[0]


def test_other_victims_and_review_are_mentioned():
    text = " ".join(explain(rec(other_victims_inr=Decimal("7771.21"), needs_comment=True, band="REVIEW")))
    assert "Rs 7,771.21 of other victims" in text and "comment" in text