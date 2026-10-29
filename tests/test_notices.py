"""Police notices by e-mail or letter (DECISIONS D-52), no database: what is read from each demo notice, that a model
can only confirm what the text says, the ticket a notice becomes, and the letter sent back to the officer."""
from decimal import Decimal

import yaml

from pipeline import notices
from pipeline.agent import send_back_letter
from pipeline.db import ROOT


def demo(nid: str) -> str:
    n = next(x for x in yaml.safe_load(notices.NOTICES.read_text())["notices"] if x["notice_id"] == nid)
    return n.get("text") or notices.read_file(ROOT / n["file"])


def test_the_e_mail_is_read_with_a_date_and_no_time():
    f = notices.parse(demo("NTC_01"))
    assert f["utr"] == "IMPS202610181122000038" and f["amount_inr"] == Decimal("209400")
    assert str(f["txn_date"]) == "2026-10-18" and f["txn_time"] is None
    assert f["beneficiary_account"] == "ACC_M03" and "94 BNSS" in f["legal_basis"]
    assert f["authority"] == "Cyber Crime Police Station, Madurai" and f["fir_reference"] == "FIR 312/2026"
    assert f["ack_no"] == "24666011332132" and f["notice_ref"] == "MDU/CCPS/2026/1018-31"


def test_the_pdf_letter_is_read_from_its_text():
    f = notices.parse(demo("NTC_02"))
    assert f["utr"] == "IMPS202610181450000009" and f["amount_inr"] == Decimal("125800")
    assert (str(f["txn_date"]), f["txn_time"]) == ("2026-10-18", "14:50")
    assert f["beneficiary_account"] == "ACC_M01" and f["legal_basis"] == "u/s 94 BNSS"
    assert f["authority"] == "Cyber Crime Police Station, Coimbatore"


def test_a_notice_without_a_provision_and_a_whole_account_demand():
    text = demo("NTC_04")
    f = notices.parse(text)
    assert f["legal_basis"] is None and f["amount_inr"] == Decimal("143000")
    assert notices.asks_full_freeze(text) and not notices.asks_full_freeze(demo("NTC_03"))


def test_the_model_can_only_confirm_what_the_text_says():
    text = demo("NTC_02")
    parsed = notices.parse(text)
    model = {**{k: (str(v) if v is not None else None) for k, v in parsed.items()},
             "amount_inr": "152800", "fir_reference": "FIR 1/2026"}     # values that are not in the notice
    fields, source = notices.ground(model, parsed, text)
    assert fields["amount_inr"] == Decimal("125800") and source["amount_inr"] != "model"
    assert fields["fir_reference"] is None
    assert source["utr"] in ("model", "parser") and fields["utr"] == parsed["utr"]


def test_a_date_only_notice_becomes_a_ticket_that_compares_the_day():
    f = notices.parse(demo("NTC_01"))
    n = {"case_id": "NOTICE_NTC_01", "received_at": "2026-10-18 20:05:00"}
    t = notices.ticket_row(n, f, "V_X09", demo("NTC_01"))
    assert t["txn_at"] == "2026-10-18 00:00:00" and t["txn_time_known"] == "FALSE"
    assert t["complaint_source"] == "IO" and t["ticket_kind"] == "IO_NOTICE" and t["lane"] == "A"
    assert t["amount_inr"] == "209400" and t["ordering_authority"].endswith("Madurai")
    f2 = notices.parse(demo("NTC_02"))
    assert notices.ticket_row(n, f2, "V_X", "")["txn_at"] == "2026-10-18 14:50:00"


def test_the_letter_back_names_the_wrong_field_in_rupees():
    c = {"case_id": "NOTICE_NTC_02", "ticket_kind": "IO_NOTICE", "ordering_authority": "Cyber Crime Police Station, "
         "Coimbatore", "ordering_reference": "CBE/CCPS/2026/0877", "beneficiary_account": "ACC_M01", "remarks": ""}
    k = {"mismatched": "Amount: the ticket says Rs 125800.00; the payment with that UTR is Rs 152800.00"}
    letter = send_back_letter(c, k)
    assert letter.startswith("To: Cyber Crime Police Station, Coimbatore")
    assert "Your notice CBE/CCPS/2026/0877" in letter
    assert "the notice says Rs 1,25,800" in letter and "is Rs 1,52,800" in letter
    assert "Please send a corrected notice" in letter


def test_the_letter_back_for_a_missing_provision_and_a_whole_account_demand():
    c = {"case_id": "NOTICE_NTC_04", "ticket_kind": "IO_NOTICE", "ordering_authority": "Cyber Cell, Tiruchirappalli",
         "ordering_reference": "TRY/CC/2026/5502", "beneficiary_account": "ACC_M07", "remarks": demo("NTC_04")}
    letter = send_back_letter(c, {"problems": "no legal basis"})
    assert "- Missing: legal basis." in letter and "whole account" in letter


def test_a_portal_ticket_still_gets_the_portal_letter():
    letter = send_back_letter({"case_id": "CASE_BADUTR", "ack_no": "31234567890123"},
                              {"mismatched": "UTR: X is not a payment into account ACC_K"})
    assert letter.startswith("Ticket CASE_BADUTR") and "Please correct the ticket on the portal" in letter


def test_the_inbox_rows_for_the_demo():
    rows = notices.inbox_rows()["BANK_X"]
    assert [r[0] for r in rows] == ["NTC_01", "NTC_02", "NTC_03", "NTC_04"]
    assert rows[0][4] == "data/notices/NTC_01.eml" and rows[0][5] is None        # a file, read when it is worked
    assert rows[2][4] is None and "IMPS202610181631000065" in rows[2][5]            # text pasted from the e-mail
    assert all(r[6] == "NEW" for r in rows)