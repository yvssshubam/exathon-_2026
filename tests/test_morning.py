"""The morning log's fixed template (no database): short headed sections of points, complaints named by their
acknowledgement number, and exactly the decisions waiting."""
from pipeline.morning import template

FACTS = {
    "names": {"ACC_A": "A", "ACC_D": "Murugan", "ACC_K": "K"},
    "labels": {"CASE_DEMO": "complaint 31025101800001", "CASE_BADUTR": "complaint 31025101800014"},
    "tickets": [("CASE_DEMO", "2026-10-18 02:13:00", "DONE"), ("CASE_BADUTR", "2026-10-18 03:20:00", "SENT_BACK")],
    "holds": [("CASE_DEMO", "ACC_A", "LIEN", "50000.00", "V_LAKSHMI", "2026-10-18 02:16:00", "MANDATORY_HOLD", 19)],
    "filings": [("KYC", 1, None, 1), ("SEND_BACK", 1, None, 2)],
    "watch": [("2026-10-18 04:30:00", "04:30: C tried to withdraw Rs 29,500; the hold refused it.", 5)],
    "splits": [], "refused": [], "failed": [],
    "waiting": [(1, "CASE_DEMO", "ACC_D", "PROPOSE_NARROW", "500.00"),
                (2, "CASE_BADUTR", "ACC_K", "UNMATCHED_NOTICE", "25000.00")],
}


def test_the_log_is_headed_sections_of_points():
    lines = template(FACTS, "Bank X", "2026-10-18 09:00:00").splitlines()
    assert lines[0] == "Overnight at Bank X, up to 09:00 on 2026-10-18."
    assert all(ln.startswith(("## ", "- ")) for ln in lines[1:])
    assert "- 02:16 A: Rs 50,000 held, amount only (complaint 31025101800001)" in lines
    assert "- 04:30 C tried to withdraw Rs 29,500; the hold refused it." in lines
    assert "- 1 ticket sent back to the police" in lines
    assert lines[-3:] == ["## Waiting for you (2)", "- Narrow to Rs 500: Murugan (complaint 31025101800001)",
                          "- Unmatched notice: K (complaint 31025101800014)"]


def test_no_internal_case_ids_reach_the_officer():
    assert "CASE_" not in template(FACTS, "Bank X", "2026-10-18 09:00:00")