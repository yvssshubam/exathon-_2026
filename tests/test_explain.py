"""Plain-language wording of the agent's actions (no database)."""
from decimal import Decimal

from pipeline.explain import explain, headline, inr


def act(**kw):
    base = dict(account_id="ACC_D", bank_id="BANK_X", hop=3, action_code="MANDATORY_HOLD", act_mode="AUTOMATIC",
                amount_inr=Decimal("1000"), current_hold_inr=None, victim_amount_inr=Decimal("500"),
                other_victims_inr=Decimal("0"), balance_now_inr=Decimal("820000"), trail_amount_inr=Decimal("1000"),
                band="HONEST", score=0.01, reason_code="MONEY_STILL_THERE", legal_basis="S.168 r/w S.94 BNSS",
                action_status="DONE")
    base.update(kw)
    return base


def test_indian_grouping():
    assert inr(119500) == "Rs 1,19,500"
    assert inr(Decimal("819000")) == "Rs 8,19,000"
    assert inr(Decimal("498.75")) == "Rs 498.75"
    assert inr(200000) == "Rs 2,00,000"
    assert inr(None) == "Rs 0"


def test_murugans_mandatory_hold_is_amount_only():
    a = act()
    assert headline(a) == "Held Rs 1,000 (amount only)"
    text = " ".join(explain(a))
    assert "not the account" in text and "Only Rs 500 of it" in text


def test_narrowing_proposal_says_why():
    a = act(action_code="PROPOSE_NARROW", act_mode="OFFICER", amount_inr=Decimal("500"),
            current_hold_inr=Decimal("1000"), reason_code="LIKELY_HONEST_RECEIVER")
    assert headline(a) == "Narrow the hold to Rs 500?"
    text = " ".join(explain(a))
    assert "Rs 1,000" in text and "Rs 500" in text and "honest receiver" in text


def test_emptied_account_hold_takes_what_was_left():
    a = act(account_id="ACC_C", amount_inr=Decimal("29500"), trail_amount_inr=Decimal("80000"),
            balance_now_inr=Decimal("29500"), victim_amount_inr=Decimal("29500"), band="MULE")
    assert "only Rs 29,500 was left" in " ".join(explain(a))


def test_full_freeze_is_only_ever_a_proposal_with_material():
    a = act(account_id="ACC_C", action_code="PROPOSE_FULL_FREEZE", act_mode="OFFICER", band="MULE",
            reason_code="PASS_THROUGH_AND_CASH_OUT")
    assert headline(a) == "Freeze the whole account?"
    text = " ".join(explain(a))
    assert "Only an officer can place it" in text and "label alone is not a reason" in text


def test_trail_paperwork_wording():
    exit_ = act(account_id="EXIT:ATM:BX-ATM-0042", bank_id=None, action_code="RECORD_EXIT",
                amount_inr=Decimal("50000"), reason_code="EXIT_FROM_OWN_ACCOUNT")
    assert headline(exit_) == "Exit recorded: Rs 50,000 of the victim's money"
    onward = act(account_id="ACC_B", bank_id="BANK_Y", action_code="ONWARD_ENTRY", amount_inr=Decimal("70000"),
                 reason_code="MOVED_TO_OTHER_BANK")
    assert headline(onward) == "Onward entry to Bank Y: Rs 70,000"
    assert "gets its ticket now" in " ".join(explain(onward))


def test_other_victims_are_mentioned():
    a = act(other_victims_inr=Decimal("1200"))
    assert "Rs 1,200 of other victims' money" in " ".join(explain(a))