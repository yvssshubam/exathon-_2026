"""Written reasons (Module 4), no database: the rules decide the reasons, the model only words them, every claim is
checked against what it cites, the case against is always there, citations come from the fixed list, and the holder
is told only the authority, the notice and the amount."""
import json

import pytest

from pipeline.reasons import RULES, claims_check, holder_message, mandatory_text, numbers, template, word

NARROW = {
    "action_code": "PROPOSE_NARROW", "account_id": "ACC_D", "name": "Murugan (Sri Murugan Fuels)",
    "case_id": "CASE_DEMO", "rule": RULES["officer"]["PROPOSE_NARROW"], "citation": RULES["legal_basis"]["narrow"],
    "numbers": {"proposed": "500.00", "held": "1000.00", "victim": "500.00", "balance": "820000.00"},
    "support": [{"id": "S1", "text": "The account is 3.0 years old."},
                {"id": "S2",
                 "text": "It is paid by many different small payers (12% from its top three), like a shop."}],
    "against": [{"id": "C1", "text": "It received Rs 1,000 directly from account C, which is held."}],
}


class Model:
    model = "fake"

    def __init__(self, sentences=None, raw=None, fail=False):
        self.sentences, self.raw, self.fail = sentences, raw, fail

    def write(self, system, facts):
        if self.fail:
            raise RuntimeError("down")
        return self.raw if self.raw is not None else json.dumps({"sentences": self.sentences})


def test_the_template_has_the_proposal_the_reasons_the_case_against_and_the_fixed_citation():
    text = template(NARROW)
    assert text.splitlines()[0].startswith("Narrow the hold on Murugan (Sri Murugan Fuels) from Rs 1,000 to Rs 500")
    assert "Why: The account is 3.0 years old." in text
    assert "Case against: It received Rs 1,000 directly from account C, which is held." in text
    assert text.endswith("Legal basis: " + RULES["legal_basis"]["narrow"])


def test_a_model_draft_that_cites_its_reasons_is_kept_and_the_citation_is_still_fixed():
    good = [{"text": "The account is 3.0 years old and is paid by many small payers, like a shop.",
             "cites": ["S1", "S2"]},
            {"text": "It did receive Rs 1,000 straight from account C, which is held.", "cites": ["C1"]}]
    out = word(NARROW, Model(good))
    assert out["by"] == "model" and out["note"] is None
    assert out["text"].endswith("Legal basis: " + RULES["legal_basis"]["narrow"])


@pytest.mark.parametrize("sentences,why", [
    ([{"text": "It is 3.0 years old.", "cites": ["S1"]}], "the case against is missing"),
    ([{"text": "It is 3.0 years old.", "cites": []}, {"text": "From C.", "cites": ["C1"]}], "points to no given"),
    ([{"text": "It is 3.0 years old.", "cites": ["S9"]}, {"text": "From C.", "cites": ["C1"]}], "not given"),
    ([{"text": "It is 4 years old.", "cites": ["S1"]}, {"text": "From C.", "cites": ["C1"]}], "a number not in"),
    ([{"text": "It is clearly a shop.", "cites": ["S2"]}, {"text": "From C.", "cites": ["C1"]}], "overstates"),
    ([{"text": "The holder is not a mule.", "cites": ["S1"]}, {"text": "From C.", "cites": ["C1"]}], "verdict"),
])
def test_the_claims_check_rejects(sentences, why):
    note = claims_check(sentences, NARROW)
    assert note and why in note
    out = word(NARROW, Model(sentences))
    assert out["by"] == "template" and why in out["note"]


def test_a_number_from_another_reason_does_not_count():
    # 12 is in S2, not in S1: a sentence citing only S1 may not use it
    note = claims_check([{"text": "It is 3.0 years old and 12% comes from three payers.", "cites": ["S1"]},
                         {"text": "From C.", "cites": ["C1"]}], NARROW)
    assert note and "12" in note
    assert numbers("12% and 1,000") == {"12", "1000"}
    # but the proposal's own numbers (held 1,000, proposed 500) may be repeated anywhere
    assert claims_check([{"text": "Hold Rs 500 instead of Rs 1,000.", "cites": ["S1"]},
                         {"text": "From C.", "cites": ["C1"]}], NARROW) is None


@pytest.mark.parametrize("model", [None, Model(fail=True), Model(raw="not json at all")])
def test_no_model_or_a_failed_model_gives_the_template(model):
    out = word(NARROW, model)
    assert out["by"] == "template" and out["text"] == template(NARROW)


def test_mandatory_holds_are_never_model_worded():
    r = dict(NARROW, action_code="MANDATORY_HOLD")
    assert word(r, Model([{"text": "x", "cites": ["S1"]}]))["by"] == "template"
    c = {"case_id": "CASE_DEMO", "ordering_reference": "VLR/CCPS/2026/1018-01",
         "ordering_authority": "Cyber Crime Police Station, Vellore (simulated)",
         "notice_legal_basis": "S.168 r/w S.94 BNSS"}
    text = mandatory_text(c, "IMPS2026101801520001", "50000.00")
    assert text.startswith("Notice VLR/CCPS/2026/1018-01 from Cyber Crime Police Station, Vellore (simulated), "
                           "transaction IMPS2026101801520001: held (amount only): Rs 50,000 under S.168 r/w S.94 BNSS")


def test_the_holder_is_told_the_authority_the_notice_and_the_amount_only():
    c = {"case_id": "CASE_DEMO", "ordering_reference": "VLR/CCPS/2026/1018-01",
         "ordering_authority": "Cyber Crime Police Station, Vellore (simulated)",
         "notice_legal_basis": "S.168 r/w S.94 BNSS"}
    text = holder_message(c, "ACC_D", "500.00")
    assert "Rs 500" in text and "VLR/CCPS/2026/1018-01" in text and "Vellore" in text and "XXXXCC_D" in text
    for evidence in ("mule", "pass", "complaint", "victim", "score", "ticket", "fraud"):
        assert evidence not in text.lower()
    assert "frozen" in holder_message(c, "ACC_C", "29500.00", "ACCOUNT_FREEZE")


def test_every_action_cites_from_the_fixed_list():
    from pipeline.reasons import CITATION

    assert set(CITATION.values()) <= set(RULES["legal_basis"])