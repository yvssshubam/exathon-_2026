"""The console's flow through the API, against the real database (Modules 7-8): run the night, read the morning,
open a hold record, decide. Resets and runs the night first.

    python -m pytest -m db tests/test_db_api.py
"""
import pytest

from tests.dbtools import connect_or_skip

pytestmark = pytest.mark.db


@pytest.fixture(scope="module")
def client():
    from fastapi.testclient import TestClient

    from app.api.main import app

    connect_or_skip().close()
    c = TestClient(app)
    r = c.post("/api/night/run", json={"until": "2026-10-18 09:00:00", "reset": True})
    assert r.status_code == 200, r.text
    return c


def waiting(client, bank="BANK_X"):
    return {(w["case_id"], w["account_id"], w["action_code"]): w
            for w in client.get("/api/inbox", params={"bank": bank}).json()["waiting"]}


def test_the_clock_says_nine(client):
    assert str(client.get("/api/clock").json()["clock"]).startswith("2026-10-18 09:00")


def test_the_morning_has_two_lists(client):
    body = client.get("/api/inbox", params={"bank": "BANK_X"}).json()
    assert {d["case_id"] for d in body["done"]} >= {"CASE_DEMO", "CASE_BADUTR", "CASE_DEMO_V2"}
    assert len(body["waiting"]) == 3
    for w in body["waiting"]:                  # every decision comes with a fixed citation
        assert w["citation"]
        if w["action_code"] in ("PROPOSE_NARROW", "PROPOSE_FULL_FREEZE"):   # ... and the case against it
            assert w["reasons_for"] and w["case_against"]
    assert body["morning_log"]["text"]


def test_the_hold_record_traces_every_number(client):
    r = client.get("/api/accounts/ACC_A/record", params={"bank": "BANK_X"})
    assert r.status_code == 200
    text = client.get("/api/accounts/ACC_A/record.txt", params={"bank": "BANK_X"}).text
    assert "HOLD RECORD" in text and "Lowest-balance calculation" in text and "[stmt " in text
    assert "V_RAVI" in text                     # the second victim's linked hold and the split


def test_the_narrator_sees_what_no_single_bank_sees(client):
    body = client.get("/api/narrator").json()
    assert "no single bank sees this" in body["caption"].lower()


def test_another_bank_s_account_has_no_record_here(client):
    assert client.get("/api/accounts/ACC_B/record", params={"bank": "BANK_X"}).status_code == 404


def test_rejecting_needs_a_reason(client):
    w = waiting(client)[("CASE_DEMO", "ACC_C", "PROPOSE_FULL_FREEZE")]
    r = client.post(f"/api/proposals/{w['proposal_id']}/reject", json={"officer": "officer.test"})
    assert r.status_code == 400


def test_the_officer_rejects_the_freeze_and_c_keeps_its_lien(client):
    w = waiting(client)[("CASE_DEMO", "ACC_C", "PROPOSE_FULL_FREEZE")]
    r = client.post(f"/api/proposals/{w['proposal_id']}/reject",
                    json={"officer": "officer.test", "comment": "the lien covers the victim's money"})
    assert r.status_code == 200
    rows = client.get("/api/holds", params={"bank": "BANK_X"}).json()
    c = [h for h in rows if h["account_id"] == "ACC_C" and h["hold_status"] == "ACTIVE"]
    assert c and all(h["hold_kind"] == "LIEN" for h in c)


def test_accepting_the_near_match_works_the_ticket_and_holds_k(client):
    w = waiting(client)[("CASE_BADUTR", "ACC_K", "UNMATCHED_NOTICE")]
    r = client.post(f"/api/proposals/{w['proposal_id']}/approve",
                    json={"officer": "officer.test", "comment": "UTR one digit off; amount and time match"})
    assert r.status_code == 200, r.text
    assert r.json()["rerun"]
    db = connect_or_skip()
    try:
        status = db.execute("SELECT match_status FROM PH_X.INTAKE_CHECK WHERE case_id = 'CASE_BADUTR'").fetchval()
        held = db.execute("SELECT SUM(amount_inr) FROM PH_X.HOLD_LEDGER WHERE case_id = 'CASE_BADUTR' "
                          "AND hold_kind = 'LIEN' AND hold_status = 'ACTIVE'").fetchval()
    finally:
        db.close()
    assert status == "ACCEPTED" and float(held) == 25000.0
    assert ("CASE_BADUTR", "ACC_K", "UNMATCHED_NOTICE") not in waiting(client)


def test_a_decided_proposal_cannot_be_decided_again(client):
    db = connect_or_skip()
    try:
        pid = db.execute("SELECT proposal_id FROM PH_X.PROPOSAL WHERE action_code = 'UNMATCHED_NOTICE'").fetchval()
    finally:
        db.close()
    r = client.post(f"/api/proposals/{int(pid)}/approve", json={"officer": "officer.test"})
    assert r.status_code == 400