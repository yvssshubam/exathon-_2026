"""Police notices (pipeline/notices.py, DECISIONS D-41). No database."""
from datetime import date
from decimal import Decimal as D

import pytest
import yaml

from pipeline import notices as nt
from pipeline.load_cases import NOTICE_COLUMNS, NOTICES, ROOT

TEXTS = {n["notice_id"]: n for n in yaml.safe_load(NOTICES.read_text(encoding="utf-8"))["notices"]}


def test_the_parser_reads_the_clean_and_the_messy_notices():
    a = nt.parse(TEXTS["NTC_01"]["text"])
    assert a == {"utr": "IMPS2026101801520001", "amount_inr": D("200000"), "txn_date": date(2026, 10, 18),
                 "legal_basis": "S.168 r/w S.94 BNSS", "authority": "Cyber Crime Police Station, Vellore",
                 "notice_ref": "VLR/CCPS/2026/1018-01", "ack_no": "31025101800001"}
    b = nt.parse(TEXTS["NTC_02"]["text"])
    assert (b["utr"], b["amount_inr"], b["legal_basis"], b["ack_no"]) == (
        "IMPS202610181631000056", D("48100"), "u/s 94 BNSS", "34646522879626")


def test_what_is_missing_stays_missing():
    c = nt.parse(TEXTS["NTC_05"]["text"])
    assert c["amount_inr"] is None and c["legal_basis"] is None and c["notice_ref"] is None


def test_amounts_in_words_and_dates_in_words():
    assert nt.amounts("Rupees two lakh only") == [D("200000")]
    assert nt.dates("paid on 18th October 2026") == [date(2026, 10, 18)]


def test_whole_account_freeze_demands_are_spotted():
    assert nt.FULL_FREEZE.search(TEXTS["NTC_03"]["text"]) and nt.FULL_FREEZE.search(TEXTS["NTC_05"]["text"])
    assert not nt.FULL_FREEZE.search(TEXTS["NTC_01"]["text"])     # "hold the beneficiary a/c" is not a freeze


def test_a_model_value_is_kept_only_if_the_notice_bears_it_out():
    text = TEXTS["NTC_01"]["text"]
    model = {"utr": "IMPS2026101801520001", "amount_inr": 250000, "txn_date": "2026-10-18",
             "legal_basis": "S.94 BNSS", "authority": "Cyber Crime Police Station, Vellore", "notice_ref": None,
             "ack_no": "31025101800099"}
    fields, source = nt.ground(model, nt.parse(text), text)
    assert source == {"utr": "model", "amount_inr": "parser", "txn_date": "model", "legal_basis": "model",
                      "authority": "model", "notice_ref": "parser", "ack_no": "parser"}
    assert fields["amount_inr"] == D("200000") and fields["ack_no"] == "31025101800001"


def facts(**kw):
    return nt.Facts(**kw)


def test_a_notice_that_checks_out_is_linked_to_its_ticket():
    f = nt.parse(TEXTS["NTC_01"]["text"])
    status, problems, notes = nt.decide(f, facts(found=True, own_bank=True, amount_inr=D("200000"),
                                                 ts="2026-10-18 01:52:00", case_id="CASE_DEMO"), "Bank X", False)
    assert (status, problems, notes) == ("LINKED", [], [])


def test_a_mistyped_utr_wrong_amount_and_no_provision_are_each_named():
    f = nt.parse(TEXTS["NTC_03"]["text"])
    near = [{"utr": "IMPS202610181401000030", "amount_inr": D("246800"), "ts": "2026-10-18 14:01:00"}]
    status, problems, notes = nt.decide(f, facts(near=near), "Bank X", True)
    assert status == "SENT_BACK"
    assert problems == [
        "UTR IMPS202610181401000039 is not found at Bank X. The nearest match received that day is "
        "IMPS202610181401000030: Rs 2,46,800 at 14:01 on 2026-10-18. Please confirm the UTR.",
        "The notice states Rs 2,50,000; that nearest match is Rs 2,46,800. Please confirm the amount.",
        "The notice cites no legal provision (for example S.94 BNSS).",
    ]
    assert notes[0].startswith("The notice asks for the whole account to be frozen.")


def test_an_amount_that_differs_from_the_transaction_sends_it_back():
    f = {**nt.parse(TEXTS["NTC_01"]["text"]), "amount_inr": D("250000")}
    status, problems, _ = nt.decide(f, facts(found=True, own_bank=True, amount_inr=D("200000"),
                                              ts="2026-10-18 01:52:00", case_id="CASE_DEMO"), "Bank X", False)
    assert status == "SENT_BACK" and problems == [
        "The notice states Rs 2,50,000; the transaction with that UTR is Rs 2,00,000."]


def test_a_utr_nowhere_near_this_bank():
    f = nt.parse(TEXTS["NTC_04"]["text"])
    _, problems, _ = nt.decide(f, facts(), "Bank X", False)
    assert problems == ["UTR IMPS202610180930009999 is not found at Bank X, and no payment received that day "
                        "comes close; the notice may be meant for another bank."]


def test_valid_but_no_ticket_yet():
    f = nt.parse(TEXTS["NTC_01"]["text"])
    assert nt.decide(f, facts(found=True, own_bank=True, amount_inr=D("200000"), ts="2026-10-18 01:52:00"),
                     "Bank X", False)[0] == "NO_TICKET"


def test_the_letter_lists_every_problem_and_the_note():
    f = nt.parse(TEXTS["NTC_03"]["text"])
    n = {"sent_from": "SHO, Cyber Police Station, Coimbatore", "received_at": "2026-10-18 16:10:00",
         "channel": "scanned letter", **f}
    text = nt.letter(n, ["First problem.", "Second problem."], ["A note."], "Bank X")
    assert text.splitlines()[:2] == ["To: SHO, Cyber Police Station, Coimbatore",
                                     "Subject: Your notice CBE/CPS/2026/0412: details needed before a hold can be "
                                     "placed"]
    assert "1. First problem.\n2. Second problem.\n\nA note." in text and text.endswith("Nodal Officer, Bank X")


class Model:
    def __init__(self, text):
        self.text = text

    def write(self, system, facts):
        return self.text


def test_model_wording_keeps_every_number_and_utr_or_is_dropped():
    base = "1. UTR IMPS202610181401000039 is not found. The nearest is IMPS202610181401000030: Rs 2,46,800."
    good = "UTR IMPS202610181401000039 was not found; the closest is IMPS202610181401000030 (Rs 2,46,800)."
    assert nt.word(base, Model(good)) == (good, "model")
    assert nt.word(base, Model("UTR IMPS202610181401000031 is the right one, Rs 2,46,800."))[1] == "template"
    assert nt.word(base, None) == (base, "template")


def test_the_loader_row_has_one_value_per_column():
    schema = (ROOT / "sql" / "010_schema.sql").read_text()
    block = schema[schema.index("CREATE OR REPLACE TABLE PH.NOTICE ("):]
    block = block[:block.index(") COMMENT IS")]
    assert sum(1 for line in block.splitlines()[1:] if line.strip()) == NOTICE_COLUMNS


@pytest.mark.parametrize("nid", sorted(TEXTS))
def test_every_sample_notice_has_text_and_a_time(nid):
    assert TEXTS[nid]["text"].strip() and str(TEXTS[nid]["received_at"]).startswith("2026-10-18")


def test_a_letter_without_a_reference_names_the_notice_by_its_time_once():
    n = {"sent_from": "cybercell.madurai@police.example", "received_at": "2026-10-18 18:45:00", "channel": "e-mail",
         "notice_ref": None}
    text = nt.letter(n, ["The notice states no amount to hold."], [], "Bank X")
    assert "Subject: Your notice of 18:45 on 2026-10-18: details needed before a hold can be placed" in text
    assert "We received your notice by e-mail at 18:45 on 2026-10-18. We cannot act on it as it stands:" in text


def test_the_models_letter_is_tidied():
    from pipeline.notices import tidy

    template = "a whole-account freeze is the officer's decision, with written reasons."
    written = "a whole-account freeze is the officer'sdecision, with written reasons.  \n\n\n\nNodal Officer"
    assert tidy(written, template) == ("a whole-account freeze is the officer's decision, with written reasons."
                                       "\n\nNodal Officer")


def test_curly_quotes_and_invisible_characters_are_cleaned():
    from pipeline.notices import tidy

    template = "a whole-account freeze is the officer's decision, with written reasons."
    for glue in ("’s​", "’s", "'s­"):
        written = f"a whole-account freeze is the officer{glue}decision, with written reasons."
        assert tidy(written, template) == template