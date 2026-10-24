"""Freeze reasons drafted for the officer (pipeline/reasons.py, DECISIONS D-39). No database."""
from decimal import Decimal as D

from pipeline import reasons as r

FACTS = {
    "account_id": "ACC_A", "name": "A", "account_type": "savings", "bank_name": "Bank X", "age_days": 20,
    "tickets": [{"case_id": "CASE_DEMO", "received_at": "2026-10-18 02:13:00", "reported_inr": D("200000"),
                 "victim_inr": D("50000"), "balance": D("50000")}],
    "signals": {"median_dwell_minutes": 14.0, "pass_through_ratio": 0.882, "cash_or_crypto_exits": 3.0,
                "complaint_linked_credits_7d": 1.0},
    "since_hold": ["05:10: A tried to move Rs 50,000 to a crypto wallet; refused, Rs 50,000 is held."],
    "balance_inr": D("50000"), "held_inr": D("50000"),
}


def test_the_template_states_every_fact_once_in_plain_words():
    text = r.template(FACTS)
    assert text.startswith("A holds a savings account at Bank X, opened 20 days before the first ticket.")
    assert "It is named in 1 fraud ticket: the 02:13 ticket, Rs 2,00,000 reported, Rs 50,000 of it traced" in text
    assert ("Over the last 90 days money it receives usually stays 14 minutes; 88% of what it received was sent on "
            "within an hour; 3 withdrawals went to cash or crypto; it was paid by 1 reported fraud in the last 7 "
            "days.") in text
    assert "05:10: A tried to move Rs 50,000 to a crypto wallet; refused" in text
    assert text.endswith("cannot be moved on before the police act.")


def test_numbers_are_normalised():
    assert r.numbers("Rs 1,19,500 at 05:10, 88% of 50,000.00") == {"119500", "5", "10", "88", "50000"}


class Model:
    def __init__(self, text=None, fail=False):
        self.text, self.fail = text, fail

    def write(self, system, facts):
        if self.fail:
            raise RuntimeError("down")
        return self.text


def test_a_grounded_model_paragraph_is_used():
    para = ("Account A at Bank X was opened 20 days before the first ticket and received Rs 50,000 of a reported "
            "Rs 2,00,000 fraud. 88% of what it receives leaves within an hour, and 3 withdrawals went to cash or "
            "crypto. At 05:10 it tried to send Rs 50,000 to a crypto wallet. A freeze stops money arriving later "
            "from being moved on.")
    d = r.draft(FACTS, Model(para))
    assert d["written_by"] == "model" and d["text"] == para


def test_a_model_paragraph_with_an_invented_number_is_thrown_away():
    d = r.draft(FACTS, Model("A sent 95% of its money on within an hour."))
    assert d["written_by"] == "template" and d["text"] == r.template(FACTS)


def test_no_model_or_a_failed_model_gives_the_template():
    assert r.draft(FACTS)["written_by"] == "template"
    assert r.draft(FACTS, Model(fail=True))["written_by"] == "template"


def test_an_account_with_no_watch_events_and_unknown_age():
    f = {**FACTS, "age_days": None, "since_hold": [], "signals": {}}
    text = r.template(f)
    assert text.startswith("A holds a savings account at Bank X.") and "Over the last 90 days" not in text


def test_the_model_may_not_call_the_holder_fraudulent():
    from pipeline.reasons import grounded

    facts = "2 accounts at Bank X act as one network: A, C. A sent Rs 80,000 to C."
    assert grounded("Accounts A and C at Bank X act together; A sent Rs 80,000 to C.", facts)
    assert not grounded("Accounts A and C are involved in fraudulent activity; A sent Rs 80,000 to C.", facts)
    assert not grounded("The criminals moved Rs 80,000.", facts)