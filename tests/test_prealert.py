"""A likely unreported victim (DECISIONS D-53), no database: the pre-alert names the payment, where the account is
on hold and the case against, says nothing against the payer, and the handover note reports it."""
from pipeline import prealert
from pipeline.morning import template
from pipeline.reasons import VERDICT

HOLDS = [{"case_id": "CASE_DEMO", "amount_inr": "50000.00", "frozen": False, "since": "2026-10-18 02:16:00",
          "not_linked": False, "source_txn_id": None, "authority": "Cyber Crime PS Chennai", "reference": "CCPS/1",
          "legal_basis": "S.168 r/w S.94 BNSS", "ack_no": "31025101800001", "victim_tag": "V_LAKSHMI"},
         {"case_id": "CASE_DEMO", "amount_inr": "40000.00", "frozen": False, "since": "2026-10-18 05:45:00",
          "not_linked": True, "source_txn_id": "T_DEMO_10", "authority": "Cyber Crime PS Chennai",
          "reference": "CCPS/1", "legal_basis": "S.168 r/w S.94 BNSS", "ack_no": "31025101800001",
          "victim_tag": "V_LAKSHMI"}]
C = {"account": "ACC_A", "name": "A", "bank_name": "Bank X", "payer": "ACC_RAVI", "payer_bank_id": "BANK_Y",
     "payer_bank_name": "Bank Y", "payer_where": "an account at Bank Y", "txn_id": "T_DEMO_10",
     "utr": "IMPS2026101805450021", "channel": "IMPS", "amount": "40000.00", "at": "2026-10-18 05:45:00",
     "holds": HOLDS, "case_id": "CASE_DEMO"}


def test_the_alert_says_where_the_account_is_on_hold():
    reasons, text = prealert.draft(C)
    assert text.startswith("To: CFCFRMS, for the investigating police and Bank Y")
    assert "At 05:45 on 18 Oct 2026, an account at Bank Y paid Rs 40,000 by IMPS (UTR IMPS2026101805450021)" in text
    assert "Where account ACC_A is on hold at Bank X:" in text
    assert ("- Rs 50,000 held since 02:16 on 18 Oct 2026, complaint 31025101800001 (CASE_DEMO), under Cyber Crime PS "
            "Chennai, CCPS/1, S.168 r/w S.94 BNSS.") in text
    assert "- Rs 40,000 since 05:45: this payment, held under the same notice, not linked to a reported victim yet." \
        in text
    assert reasons["held_inr"] == "90000.00" and reasons["source_txn_id"] == "T_DEMO_10"
    assert reasons["holds"] == HOLDS and reasons["citation"] == prealert.CITATION


def test_the_alert_passes_no_verdict_on_the_payer_and_keeps_the_case_against():
    reasons, text = prealert.draft(C)
    assert not VERDICT.search(text)
    assert "does not say the payer has done anything wrong" in text and "Nothing more is held" in text
    assert [a["id"] for a in reasons["against"]] == ["C1", "C2"]
    assert "1 reported complaint when the money arrived; Rs 90,000 is held on it now." in reasons["support"][1]["text"]


def test_a_frozen_account_says_so():
    frozen = [{**HOLDS[0], "frozen": True}]
    lines = prealert.hold_lines(frozen, "T_X", "Bank X", "ACC_A")
    assert lines[1].startswith("- whole account frozen since 02:16 on 18 Oct 2026")


def test_durations():
    assert prealert.minutes("2026-10-18 05:45:00", "2026-10-18 06:00:00") == 15
    assert prealert.duration(15) == "15 minutes" and prealert.duration(1) == "1 minute"
    assert prealert.duration(162) == "2 h 42 min" and prealert.duration(120) == "2 h"


def test_the_handover_note_reports_the_head_start_and_a_waiting_pre_alert():
    facts = {"names": {"ACC_A": "A"}, "labels": {}, "tickets": [("CASE_RAVI", "2026-10-18 06:00:00", "DONE")],
             "holds": [], "filings": [], "watch": [], "splits": [], "refused": [], "failed": [], "attempts": [],
             "stories": [{"case_id": "CASE_RAVI", "received_at": "2026-10-18 06:00:00", "status": "DONE",
                          "amount": "40000.00", "victim_tag": "V_RAVI", "holds_at": "2026-10-18 06:03:00",
                          "held": [("ACC_A", "40000.00", "LINKED_HOLD")], "gone": {},
                          "caught": ("2026-10-18 05:45:00", "ACC_A"), "flagged_at": "2026-10-18 05:45:00"}],
             "waiting": [(9, "CASE_DEMO", "ACC_A", "PROPOSE_PRE_ALERT", "12000.00")]}
    text = template(facts, "Bank X", "2026-10-18 09:00:00")
    assert "The watch had flagged the payer as a likely unreported victim 15 minutes before the complaint." in text
    assert ("- Pre-alert: A received Rs 12,000 from a payer who had not paid it before, while it was held. Send the "
            "pre-alert that they may be an unreported victim?") in text