"""The officer's morning through the API against Exasol: the queue, the night's work, the inbox, a decision,
Compare. Run with `pytest -m db` after `make db seed`."""
import pytest
from fastapi.testclient import TestClient

from app.api.main import app

pytestmark = pytest.mark.db
client = TestClient(app)


def test_health():
    assert client.get("/api/health").json()["ok"] is True


def test_the_morning_after_the_2_13_ticket():
    assert client.post("/api/demo/reset").json()["reset"] is True
    q = {t["case_id"]: t for t in client.get("/api/queue").json()}
    assert q["CASE_DEMO"]["ticket_status"] == "QUEUED" and q["CASE_DEMO"]["after_hours"] is True

    run = client.post("/api/tickets/CASE_DEMO/work").json()
    assert run["status"] == "DONE"

    box = client.get("/api/inbox").json()
    demo = next(d for d in box["done"] if d["case_id"] == "CASE_DEMO")
    assert demo["holds"] == 3 and demo["held_inr"] == 80500.0 and demo["waiting"] == 3
    narrow = next(w for w in box["waiting"] if w["account_id"] == "ACC_D")
    assert narrow["headline"] == "Narrow the hold to Rs 500?"

    trail = client.get("/api/cases/CASE_DEMO/trail").json()
    kinds = {n["id"]: n["kind"] for n in trail["nodes"]}
    assert kinds == {"ACC_LAKSHMI": "victim", "ACC_A": "own", "ACC_B": "other", "ACC_C": "own", "ACC_D": "own",
                     "EXIT:ATM:BX-ATM-0042": "exit"}
    c_to_d = next(e for e in trail["edges"] if e["to_account"] == "ACC_D")
    assert c_to_d["victim_inr"] == 500.0

    panel = client.get("/api/cases/CASE_DEMO/accounts/ACC_D").json()
    assert panel["split"]["victim_amount_inr"] == 500.0 and len(panel["reasons"]) == 3
    assert [h["amount_inr"] for h in panel["holds"]] == [1000.0]

    refused = client.post("/api/cases/CASE_DEMO/accounts/ACC_C/approve",
                          json={"action_code": "PROPOSE_FULL_FREEZE", "officer": "priya"})
    assert refused.status_code == 400 and "reasons" in refused.json()["detail"]
    ok = client.post("/api/cases/CASE_DEMO/accounts/ACC_D/approve",
                     json={"action_code": "PROPOSE_NARROW", "officer": "priya"})
    assert ok.status_code == 200

    cmp = client.get("/api/cases/CASE_DEMO/compare", params={"refresh": True}).json()
    assert cmp["today"]["honest_locked_inr"] == 819500.0 and cmp["tool"]["honest_locked_now_inr"] == 0.0

    log = client.get("/api/cases/CASE_DEMO/log").json()
    assert log[0]["steps"][0]["tool_name"] == "read_ticket"
    papers = client.get("/api/cases/CASE_DEMO/paperwork").json()
    kyc = next(p for p in papers if p["entry_kind"] == "KYC")
    assert client.post(f"/api/cases/CASE_DEMO/paperwork/{kyc['entry_id']}/send",
                       json={"officer": "priya"}).status_code == 200


def test_unknown_case_and_bank():
    assert client.get("/api/cases/NOPE/trail").status_code == 404
    assert client.get("/api/inbox", params={"bank": "BANK_Q"}).status_code == 400