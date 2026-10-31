"""The morning log's fixed template (no database): short headed sections of points, complaints named by their
acknowledgement number, and exactly the decisions waiting."""
from pipeline.morning import template

FACTS = {
    "names": {"ACC_A": "A", "ACC_C": "C", "ACC_D": "Murugan", "ACC_K": "K", "ACC_E": "E"},
    "labels": {"CASE_DEMO": "02:13 complaint", "CASE_BADUTR": "03:20 complaint"},
    "tickets": [("CASE_DEMO", "2026-10-18 02:13:00", "DONE"), ("CASE_BADUTR", "2026-10-18 03:20:00", "SENT_BACK")],
    "holds": [("CASE_DEMO", "ACC_A", "LIEN", "50000.00", "V_LAKSHMI", "2026-10-18 02:16:00", "MANDATORY_HOLD", 19)],
    "filings": [("KYC", 1, None, 1), ("SEND_BACK", 1, None, 2)],
    "watch": [], "splits": [], "refused": [], "failed": [],
    "stories": [
        {"case_id": "CASE_DEMO", "received_at": "2026-10-18 02:13:00", "status": "DONE", "amount": "200000.00",
         "victim_tag": "V_LAKSHMI", "holds_at": "2026-10-18 02:16:00",
         "held": [("ACC_A", "50000.00", "MANDATORY_HOLD"), ("ACC_C", "29500.00", "MANDATORY_HOLD")],
         "gone": {"ONWARD": ("70000.00", "BANK_Y", "2026-10-18 01:55:00", "2026-10-18 02:16:00"),
                  "EXIT": ("50000.00", None, "2026-10-18 02:07:00", "2026-10-18 02:16:00")}, "caught": None},
        {"case_id": "CASE_BADUTR", "received_at": "2026-10-18 03:20:00", "status": "SENT_BACK", "amount": "25000.00",
         "victim_tag": "V_MEENA", "holds_at": None, "held": [],
         "gone": {"SEND_BACK": (None, None, None, "2026-10-18 03:21:00")}, "caught": None}],
    "attempts": [("2026-10-18 04:30:00", "ACC_C", "CASHOUT_BLOCKED", "29500.00", None),
                 ("2026-10-18 04:31:00", "ACC_E", "RECIPIENT_WATCHED", "29500.00", "C")],
    "waiting": [(1, "CASE_DEMO", "ACC_D", "PROPOSE_NARROW", "500.00"),
                (2, "CASE_BADUTR", "ACC_K", "UNMATCHED_NOTICE", "25000.00")],
}


def test_the_log_is_the_handover_note_in_names():
    lines = template(FACTS, "Bank X", "2026-10-18 09:00:00").splitlines()
    assert lines[0] == "2 complaints overnight at Bank X, Rs 2,25,000 reported. Each was worked within 3 minutes."
    assert all(ln.startswith(("## ", "- ")) for ln in lines[1:])
    assert ("- Lakshmi, 02:13, Rs 2,00,000. Rs 79,500 held here in A and C. Rs 70,000 had gone to Bank Y; the onward "
            "entry was filed at 02:16. Rs 50,000 left as cash at 02:07, before the complaint.") in lines
    assert "## The holds stopped Rs 29,500 leaving" in lines
    assert "- C tried to take Rs 29,500 from an ATM at 04:30. Refused." in lines
    assert "- E is now watched, after C tried to pay it at 04:31." in lines
    assert lines[-3:] == ["## Waiting for the morning desk", "- Smaller hold: reduce Murugan's hold to Rs 500.",
                          "- Near match: Meena's notice. Hold Rs 25,000 in K, or wait for the police?"]


def test_no_internal_case_ids_reach_the_officer():
    assert "CASE_" not in template(FACTS, "Bank X", "2026-10-18 09:00:00")


def test_refused_duplicates_stay_out_of_the_note():
    f = dict(FACTS, refused=[("CASE_DEMO", "PROPOSE_FULL_FREEZE", "ACC_C",
                              "a full-freeze proposal on this account is already waiting for the officer")] * 3)
    assert "already waiting" not in template(f, "Bank X", "2026-10-18 09:00:00")


def test_a_freeze_says_why_even_when_the_tries_were_in_an_earlier_period():
    from pipeline.morning import freeze_why

    f = {"attempts": [], "tries": {"ACC_B": 1}, "links": {"ACC_M13": 3.0}}
    assert freeze_why(f, "ACC_B") == ", after 1 try to move held money."
    assert freeze_why(f, "ACC_M13") == ": fraud money leaves it faster than its other money, in 3 fraud cases."
    assert freeze_why(f, "ACC_Q") == "."