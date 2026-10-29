"""Police notices by e-mail or letter (DECISIONS D-52), against the real database: the night reads Bank X's four
demo notices into tickets and works them like any other. Resets and runs the night to 21:40 once.

    python -m pytest -m db tests/test_db_notices.py
"""
import pytest

from tests.dbtools import connect_or_skip, one, q

pytestmark = pytest.mark.db


@pytest.fixture(scope="module")
def db():
    from pipeline.night import run_night

    connect_or_skip().close()
    run_night(until="2026-10-18 21:40:00", quiet=True, amlworld=False)
    c = connect_or_skip()
    yield c
    c.close()


def intake(db, nid: str) -> tuple:
    return q(db, "SELECT match_status, related, related_case_id, mismatched, problems, near_utr "
                 "FROM PH_X.INTAKE_CHECK WHERE case_id = {c}", c=f"NOTICE_{nid}")[0]


def letter(db, nid: str) -> str | None:
    return one(db, "SELECT MAX(detail) FROM PH_X.PORTAL_ENTRY WHERE case_id = {c} AND entry_kind = 'SEND_BACK'",
               c=f"NOTICE_{nid}")


def test_every_notice_is_read_into_a_ticket_and_worked(db):
    rows = q(db, "SELECT n.notice_id, n.notice_status, n.read_by, n.case_id, t.ticket_status, n.stmt_id "
                 "FROM PH_X.NOTICE_INBOX n JOIN PH_X.TICKET t ON t.case_id = n.case_id ORDER BY n.notice_id")
    assert [r[0] for r in rows] == ["NTC_01", "NTC_02", "NTC_03", "NTC_04"]
    for nid, status, by, case, ticket, stmt in rows:
        assert status == "READ" and by == "parser" and case == f"NOTICE_{nid}" and stmt is not None, nid
        assert ticket not in ("QUEUED", "WORKING"), nid


def test_the_date_only_e_mail_matches_and_is_linked_not_held_twice(db):
    status, related, case, *_ = intake(db, "NTC_01")
    assert (status, related, case) == ("EXACT", "DUPLICATE", "CASE_X09")
    assert one(db, "SELECT COUNT(*) FROM PH_X.HOLD_LEDGER WHERE case_id = 'NOTICE_NTC_01'") == 0
    assert one(db, "SELECT txn_time_known FROM PH_X.COMPLAINT WHERE case_id = 'NOTICE_NTC_01'") is False


def test_the_wrong_amount_is_sent_back_with_both_figures(db):
    status, _, _, mismatched, _, _ = intake(db, "NTC_02")
    assert status == "NO_MATCH" and mismatched.startswith("Amount:")
    text = letter(db, "NTC_02")
    assert text.startswith("To: Cyber Crime Police Station, Coimbatore")
    assert "the notice says Rs 1,25,800" in text and "Rs 1,52,800" in text
    assert one(db, "SELECT COUNT(*) FROM PH_X.HOLD_LEDGER WHERE case_id = 'NOTICE_NTC_02'") == 0


def test_the_mistyped_utr_is_sent_back_and_the_near_match_waits_for_the_officer(db):
    status, _, _, mismatched, _, near = intake(db, "NTC_03")
    assert status == "NEAR_MATCH" and near == "IMPS202610181631000056" and mismatched.startswith("UTR:")
    assert "IMPS202610181631000056" in letter(db, "NTC_03")
    assert one(db, "SELECT COUNT(*) FROM PH_X.PROPOSAL WHERE case_id = 'NOTICE_NTC_03' "
                   "AND action_code = 'UNMATCHED_NOTICE' AND proposal_status = 'WAITING'") == 1


def test_a_notice_without_a_provision_is_sent_back_and_the_full_freeze_left_to_the_officer(db):
    status, _, _, _, problems, _ = intake(db, "NTC_04")
    assert status == "INCOMPLETE" and "no legal basis" in problems
    text = letter(db, "NTC_04")
    assert "Missing: legal basis" in text and "whole account" in text
    assert "whole account" in one(db, "SELECT read_note FROM PH_X.NOTICE_INBOX WHERE notice_id = 'NTC_04'")
    assert one(db, "SELECT COUNT(*) FROM PH_X.HOLD_LEDGER WHERE case_id = 'NOTICE_NTC_04'") == 0


# ------------------------------------------------------------------ the extra cases' afternoon (D-56)

def test_payments_bank_x_refused_never_arrive_at_bank_y(db):
    got = {r for (r,) in q(db, "SELECT txn_ref FROM PH_Y.UNDELIVERED_CREDIT WHERE to_account = 'ACC_M14'")}
    assert {"T_X05_02", "T_X12_03"} <= got
    assert one(db, "SELECT COUNT(*) FROM PH_Y.TXN_LIVE WHERE txn_id IN ('T_X05_02', 'T_X12_03')") == 0
    assert one(db, "SELECT COUNT(*) FROM PH_Y.WATCH_EVENT WHERE txn_ref IN ('T_X05_02', 'T_X12_03')") == 0


def test_no_pre_alert_for_money_that_never_arrived(db):
    assert one(db, "SELECT COUNT(*) FROM PH_Y.PROPOSAL WHERE action_code = 'PROPOSE_PRE_ALERT' "
                   "AND account_id = 'ACC_M14'") == 0


def test_a_pre_alert_on_a_complaint_s_trail_closes_unsent(db):
    rows = q(db, "SELECT p.proposal_status, p.checker_note FROM PH_X.PROPOSAL p WHERE p.action_code = "
                 "'PROPOSE_PRE_ALERT' AND p.account_id = 'ACC_M10'")
    assert rows and all(s == "CANCELLED" for s, _ in rows), rows
    assert any("trail" in (n or "") for _, n in rows)
    assert one(db, "SELECT COUNT(*) FROM PH_X.WATCH_EVENT WHERE account_id = 'ACC_M10' "
                   "AND event_kind = 'PRE_ALERT_NOT_NEEDED'") >= 1