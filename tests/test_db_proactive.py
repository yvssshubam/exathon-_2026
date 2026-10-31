"""Proactive holds and pre-alerts (DECISIONS D-60 to D-65) against the real database: the build brief's done-whens on
the demo night's scenarios A to G (data/proactive_case.yaml). Resets and runs the night once in draft mode, then the
officer's 09:00 check, then a jump to day 61; a second module-level run replays the night in current SOP mode.

    python -m pytest -m db tests/test_db_proactive.py
"""
from decimal import Decimal

import pytest

from tests.dbtools import connect_or_skip, d, one, q, ts

pytestmark = pytest.mark.db
OFFICER = "Priya (nodal officer)"


@pytest.fixture(scope="module")
def night():
    from pipeline.night import run_night

    connect_or_skip().close()
    return run_night(until="2026-10-18 09:00:00", quiet=True, amlworld=False, hold_mode="rbi_draft")


@pytest.fixture(scope="module")
def db(night):
    c = connect_or_skip()
    yield c
    c.close()


def hold(db, txn: str) -> dict:
    cols = ("hold_id", "account_id", "amount_inr", "legal_basis", "proactive", "payer_type", "review_state",
            "hold_status", "check_due_at", "explanation_due_on", "decide_by", "expires_on", "holder_told_at",
            "snapshot_id", "linked_case_id", "hold_mode")
    rows = q(db, f"SELECT {', '.join(cols)} FROM PH_X.HOLD_LEDGER WHERE source_txn_id = {{t}} "
                 "AND action_code = 'PROACTIVE_HOLD'", t=txn)
    assert len(rows) == 1, (txn, rows)
    return dict(zip(cols, rows[0], strict=True))


def alert(db, txn: str):
    return q(db, "SELECT alert_status, route, alert_text, sent_by, minutes_early FROM PH_X.PRE_ALERT "
                 "WHERE txn_id = {t}", t=txn)


# ------------------------------------------------------------------ Module 1: eligibility

def test_a_layer1_pipe_for_fraud_account_is_eligible(db):
    rows = q(db, "SELECT eligibility_reason, account_pattern, proactive_allowed FROM PH_X.WATCH_LIST "
                 "WHERE account_id = 'ACC_P10' AND left_at IS NULL AND first_pass <> 'CHECKED'")
    assert rows and all(r == ("layer1_ticket", "PIPE_FOR_FRAUD", True) for r in rows), rows


def test_the_same_account_in_the_grey_zone_was_not_eligible_and_its_payment_passed(db):
    """At 00:50, after one complaint, P10 was in the grey zone: victim P2's payment went through untouched."""
    assert one(db, "SELECT COUNT(*) FROM PH_X.HOLD_LEDGER WHERE source_txn_id = 'T_PRO_04' "
                   "AND action_code = 'PROACTIVE_HOLD'") == 0
    s = q(db, "SELECT sentence FROM PH_X.WATCH_EVENT WHERE txn_ref = 'T_PRO_04' AND event_kind = 'NEW_MONEY_HONEST'")
    assert s and "Grey zone" in s[0][0] and "never start one" in s[0][0]


def test_a_layer2_account_is_never_eligible(db):
    for acc in ("ACC_C", "ACC_D"):          # CASE_DEMO's second and third layers
        assert one(db, "SELECT COUNT(*) FROM PH_X.WATCH_LIST WHERE account_id = {a} AND proactive_allowed",
                   a=acc) == 0, acc


def test_a_suspect_registry_account_is_watched_and_every_eligibility_change_is_audited(db):
    assert one(db, "SELECT COUNT(*) FROM PH_X.WATCH_LIST WHERE account_id = 'ACC_P07' "
                   "AND case_id = 'REGISTRY:ACC_P07'") == 1
    events = q(db, "SELECT detail FROM PH_X.AUDIT_LOG WHERE event_name = 'eligibility' AND account_id = 'ACC_P10'")
    assert events and all('"snapshot_id"' in e for (e,) in events)


# ------------------------------------------------------------------ Modules 2, 3, 5: the scenarios

def test_a_stranger_s_money_into_a_pipe_for_fraud_is_held_at_once_and_the_pre_alert_sent(db):
    h = hold(db, "T_PRO_07")
    assert d(h["amount_inr"]) == Decimal("48000") and h["legal_basis"] == "rbi_draft" and h["proactive"]
    assert h["payer_type"] == "other_bank" and h["holder_told_at"] is not None
    assert ts(h["explanation_due_on"]) == "2026-11-07 02:41:00" and ts(h["expires_on"]) == "2026-12-17 02:41:00"
    a = alert(db, "T_PRO_07")
    assert a and a[0][0] in ("answered", "confirmed") and a[0][1] == "cfcfrms" and a[0][3] == "agent"
    entry = q(db, "SELECT filed_at, utr FROM PH_X.PORTAL_ENTRY WHERE entry_kind = 'PRE_ALERT' AND txn_id = 'T_PRO_07'")
    assert entry and ts(entry[0][0]) == "2026-10-18 02:41:00"            # the same simulated minute
    text = a[0][2]
    assert "ending 4417" in text and "Nothing here says the payer has done anything wrong." in text


def test_the_payer_s_complaint_links_to_money_already_held(db):
    h = hold(db, "T_PRO_07")
    assert h["linked_case_id"] == "CASE_P03" and h["review_state"] == "LINKED" and h["hold_status"] == "REPLACED"
    a = alert(db, "T_PRO_07")
    assert a[0][0] == "answered" and int(a[0][4]) == 169                 # 02:41 to 05:30
    total = d(one(db, "SELECT SUM(amount_inr) FROM PH_X.HOLD_LEDGER WHERE account_id = 'ACC_P10' "
                      "AND hold_status = 'ACTIVE' AND hold_kind = 'LIEN'"))
    assert total == Decimal("133000")                                    # nothing held twice


def test_a_fast_mover_hold_s_pre_alert_waits_for_the_officer(db):
    h = hold(db, "T_PRO_11")
    assert d(h["amount_inr"]) == Decimal("120000") and h["review_state"] == "NEXT_DAY_CHECK"
    a = alert(db, "T_PRO_11")
    assert a and a[0][0] == "waiting_for_officer"
    assert one(db, "SELECT COUNT(*) FROM PH_X.PORTAL_ENTRY WHERE entry_kind = 'PRE_ALERT' AND txn_id = 'T_PRO_11'") == 0


def test_our_own_customer_gets_a_call_task_not_a_referral(db):
    a = alert(db, "T_PRO_09")
    assert a and a[0][1] == "fraud_team"
    assert one(db, "SELECT COUNT(*) FROM PH_X.FRAUD_TEAM_TASK t JOIN PH_X.PRE_ALERT p ON p.pre_alert_id = "
                   "t.pre_alert_id WHERE p.txn_id = 'T_PRO_09'") == 1
    assert one(db, "SELECT COUNT(*) FROM PH_X.PORTAL_ENTRY WHERE txn_id = 'T_PRO_09'") == 0
    assert a[0][0] == "genuine"                                          # she answered at 08:20
    assert hold(db, "T_PRO_09")["review_state"] == "GENUINE"


def test_a_flagged_payer_creates_a_layering_record_and_no_pre_alert(db):
    h = hold(db, "T_PRO_10")
    assert h["payer_type"] == "flagged" and d(h["amount_inr"]) == Decimal("20000")
    assert one(db, "SELECT COUNT(*) FROM PH_X.LAYERING_RECORD WHERE txn_id = 'T_PRO_10'") == 1
    assert not alert(db, "T_PRO_10")


def test_a_regular_payer_s_small_amount_passes(db):
    assert one(db, "SELECT COUNT(*) FROM PH_X.HOLD_LEDGER WHERE source_txn_id = 'T_PRO_12'") == 0
    r = q(db, "SELECT check_result, checks_json FROM PH_X.PAYMENT_CHECK WHERE txn_id = 'T_PRO_12'")
    assert r and r[0][0] == "pass" and '"new_payer"' in r[0][1]


def test_murugan_s_new_customer_goes_through_untouched(db):
    assert one(db, "SELECT COUNT(*) FROM PH_X.HOLD_LEDGER WHERE source_txn_id = 'T_DEMO_11'") == 0


def test_every_payment_check_is_logged_with_each_value(db):
    """One audit entry per payment check of this run. The audit log is append-only and survives a reset, so only
    the entries written since the last reset (its 'seed' entry) are counted."""
    n = one(db, "SELECT COUNT(*) FROM PH_X.PAYMENT_CHECK")
    logged = one(db, "SELECT COUNT(*) FROM PH_X.AUDIT_LOG WHERE event_name = 'payment_check' AND event_id > "
                     "(SELECT MAX(event_id) FROM PH_X.AUDIT_LOG WHERE event_name = 'seed')")
    missing = one(db, "SELECT COUNT(*) FROM PH_X.PAYMENT_CHECK p WHERE NOT EXISTS (SELECT 1 FROM PH_X.AUDIT_LOG a "
                      "WHERE a.event_name = 'payment_check' AND a.sim_at = p.checked_at "
                      "AND a.detail LIKE '%' || p.txn_id || '%')")
    assert n >= 5 and logged == n and missing == 0


def test_no_pre_alert_ever_says_victim_or_mule(db):
    for (text,) in q(db, "SELECT alert_text FROM PH_X.PRE_ALERT"):
        assert "victim" not in text.lower() and "mule" not in text.lower(), text
    for (text,) in q(db, "SELECT call_script FROM PH_X.FRAUD_TEAM_TASK"):
        assert "victim" not in text.lower() and "mule" not in text.lower(), text


def test_the_checker_refuses_a_proactive_hold_where_it_is_not_allowed(db):
    from pipeline.checker import Checker
    from pipeline.db import connect
    from pipeline.recalc import propose

    m, c = connect("maker", "BANK_X"), connect("checker", "BANK_X")
    try:
        pid = propose(m, "CASE_DEMO", "ACC_D", "PROACTIVE_HOLD", "AUTOMATIC", "2400", "watch", "2026-10-18 07:31:00",
                      {"trigger": "proactive", "source_txn_id": "T_DEMO_11", "mode": "rbi_draft"})
        r = Checker(c, "BANK_X").process(None, "2026-10-18 07:31:00", [pid])
        assert r["refused"] == 1 and r["done"] == 0
    finally:
        m.close()
        c.close()


# ------------------------------------------------------------------ Module 6: the 09:00 check (changes the state)

def test_bulk_confirm_takes_only_clear_cases_and_records_each(db):
    from pipeline import proactive
    from pipeline.checker import Checker, DecisionError
    from pipeline.db import connect

    c = connect("checker", "BANK_X")
    try:
        ck = Checker(c, "BANK_X")
        stranger_c = hold(db, "T_PRO_09")["hold_id"]          # genuine answer: not clear
        fast = hold(db, "T_PRO_11")["hold_id"]                # Fast mover: one by one
        layering = hold(db, "T_PRO_10")["hold_id"]            # Pipe for fraud, no answer: clear
        for h in (stranger_c, fast):
            with pytest.raises(DecisionError):
                proactive.confirm(ck, int(h), OFFICER, "clear case", "2026-10-18 09:00:00", bulk=True)
        proactive.confirm(ck, int(layering), OFFICER, "clear case", "2026-10-18 09:00:00", bulk=True)
        with pytest.raises(DecisionError):
            proactive.confirm(ck, int(fast), "", "no name", "2026-10-18 09:00:00")
    finally:
        c.close()
    assert one(db, "SELECT COUNT(*) FROM PH_X.DECISION WHERE action_code = 'PROACTIVE_CONFIRM'") == 1


def test_a_fast_mover_s_pre_alert_is_sent_from_its_card(db):
    from pipeline import proactive
    from pipeline.checker import Checker
    from pipeline.db import connect

    c = connect("checker", "BANK_X")
    try:
        ck = Checker(c, "BANK_X")
        pa = one(db, "SELECT pre_alert_id FROM PH_X.PRE_ALERT WHERE txn_id = 'T_PRO_11'")
        proactive.confirm(ck, int(hold(db, "T_PRO_11")["hold_id"]), OFFICER, "first-time payer, large",
                          "2026-10-18 09:02:00")
        proactive.send_by_officer(ck, int(pa), OFFICER, "the payer should be asked", "2026-10-18 09:02:00")
    finally:
        c.close()
    assert alert(db, "T_PRO_11")[0][0] == "sent"
    assert one(db, "SELECT COUNT(*) FROM PH_X.PORTAL_ENTRY WHERE entry_kind = 'PRE_ALERT' AND txn_id = 'T_PRO_11'") == 1


def test_genuine_puts_a_release_in_the_queue_and_the_officer_releases_it(db):
    from pipeline import proactive
    from pipeline.checker import Checker
    from pipeline.db import connect

    c = connect("checker", "BANK_X")
    try:
        ck = Checker(c, "BANK_X")
        items = proactive.queue_items(proactive.hold_cards(c, "2026-10-18 09:05:00"))
        assert any(i["item"] == "genuine" and i["source_txn_id"] == "T_PRO_09" for i in items)
        proactive.release(ck, int(hold(db, "T_PRO_09")["hold_id"]), OFFICER, "sale of a phone: genuine",
                          "2026-10-18 09:05:00")
    finally:
        c.close()
    h = hold(db, "T_PRO_09")
    assert h["hold_status"] == "RELEASED"


# ------------------------------------------------------------------ Module 4 and 7: the clocks, then the numbers

def test_jump_ahead_the_drafted_referral_the_explanation_and_the_limit(db):
    """G: nobody answers D's hold (only its next-day check was done): day 30 drafts the referral, day 50 warns,
    day 60 releases it at the limit as a compliance miss. B's holder explained on day 12: decide by day 22."""
    from pipeline.night import run_night

    run_night(until="2026-12-18 00:00:00", reset=False, quiet=True, amlworld=False)
    b = hold(db, "T_PRO_11")
    assert ts(b["decide_by"]) == "2026-11-09 11:00:00"                   # explanation on 30 Oct + 10 days
    layer = hold(db, "T_PRO_10")
    assert layer["hold_status"] == "RELEASED"
    assert one(db, "SELECT closed_why FROM PH_X.HOLD_LEDGER WHERE hold_id = {h!d}", h=str(layer["hold_id"])) == \
        "released at the limit, no decision recorded"
    assert one(db, "SELECT compliance_miss FROM PH_X.HOLD_LEDGER WHERE hold_id = {h!d}", h=str(layer["hold_id"]))
    kinds = [k for (k,) in q(db, "SELECT event_kind FROM PH_X.WATCH_EVENT WHERE hold_id = {h!d} ORDER BY event_id",
                            h=str(layer["hold_id"]))]
    assert kinds.index("REFER_DRAFTED") < kinds.index("MUST_DECIDE") < kinds.index("HOLD_RELEASED_LIMIT")


def test_every_closed_proactive_hold_has_one_outcome(db):
    rows = q(db, "SELECT hold_id, outcome FROM PH_X.PROACTIVE_HOLD_OUTCOME")
    assert len({h for h, _ in rows}) == len(rows)
    got = {o for _, o in rows}
    assert {"confirmed_by_victim", "released_after_explanation", "layering"} <= got
    labels = q(db, "SELECT label FROM PH_X.PROACTIVE_HOLD_LABEL")
    assert {int(lab) for (lab,) in labels} <= {0, 1} and len(labels) == sum(
        1 for _, o in rows if o in ("confirmed_by_victim", "released_after_explanation"))


# ------------------------------------------------------------------ the mode switch (a second night)

def test_current_sop_mode_holds_only_the_room_under_the_existing_notice():
    from pipeline.night import run_night

    connect_or_skip().close()
    run_night(until="2026-10-18 09:00:00", quiet=True, amlworld=False, hold_mode="current_sop")
    c = connect_or_skip()
    try:
        h = hold(c, "T_PRO_07")
        assert h["legal_basis"] == "police_notice" and h["hold_mode"] == "current_sop"
        assert d(h["amount_inr"]) == Decimal("35000")                   # CASE_P01: Rs 60,000 reported, 25,000 held
        assert h["explanation_due_on"] is None and h["expires_on"] is None
    finally:
        c.close()
    run_night(until="2026-10-18 09:00:00", quiet=True, amlworld=False, hold_mode="rbi_draft")    # leave it as found