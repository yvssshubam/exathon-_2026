"""AT-10 (no database): every notice carries the required fields in en, hi and ta and never the victim's details."""
import pytest
import yaml

from pipeline.notices import LANGS, TEMPLATES, render, rupees

MURUGAN = {
    "holder_name": "Murugan (Sri Murugan Fuels)", "bank_name": "Bank Z", "notice_date": "18-10-2026",
    "hold_ref": "PH-7", "account_id": "ACC_D", "txn_time": "18-10-2026 10:21", "txn_amount": rupees(1000),
    "txn_utr": "UPI629181021000001", "hold_amount": rupees(1000), "scope": "amount", "lane": "A",
    "authority": "Cyber Crime Police Station, Vellore (simulated)", "reference": "VLR/CCPS/2026/1018-01",
    "legal_basis": "S.168 read with S.94 BNSS", "respond_by": "",
    "grievance_officer": "Ms. Kavitha Subramanian, Nodal Grievance Officer, Bank Z",
    "grievance_contact": "grievance@bankz.example.com, 1800-000-0303", "signals": "",
}
REQUIRED = ["UPI629181021000001", "18-10-2026 10:21", "₹1,000", "S.168 read with S.94 BNSS",
            "Cyber Crime Police Station, Vellore (simulated)", "VLR/CCPS/2026/1018-01", "Kavitha Subramanian",
            "grievance@bankz.example.com", "PH-7"]
VICTIM = ["Lakshmi", "ACC_LAKSHMI", "V_LAKSHMI", "IMPS2026101810020001", "2,00,000"]


@pytest.mark.parametrize("lang", LANGS)
def test_required_fields_present(lang):
    text = render(lang, MURUGAN)
    for value in REQUIRED:
        assert value in text, (lang, value)
    assert "$" not in text                          # every placeholder filled


@pytest.mark.parametrize("lang", LANGS)
def test_victim_details_never_appear(lang):
    text = render(lang, MURUGAN)
    for value in VICTIM:
        assert value not in text, (lang, value)


@pytest.mark.parametrize("lang", LANGS)
def test_lane_b_states_deadline_and_draft_status(lang):
    text = render(lang, {**MURUGAN, "lane": "B", "respond_by": "07-11-2026", "scope": "account",
                         "signals": "Money it receives usually leaves within 12 minutes."})
    assert "07-11-2026" in text and "RBI" in text


def test_all_languages_have_the_same_keys():
    keys = [set(yaml.safe_load((TEMPLATES / f"{lang}.yaml").read_text(encoding="utf-8"))) for lang in LANGS]
    assert keys[0] == keys[1] == keys[2]


def test_missing_field_is_an_error():
    bad = dict(MURUGAN)
    del bad["txn_utr"]
    with pytest.raises(ValueError):
        render("en", bad)