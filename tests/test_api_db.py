"""The whole officer journey through the API against Exasol: run, trail, account panel, approve, notice, victim."""
import pytest
from fastapi.testclient import TestClient

from app.api.main import app

pytestmark = pytest.mark.db
client = TestClient(app)


def test_health():
    assert client.get("/api/health").json()["ok"] is True


def test_officer_journey_at_murugans_bank():
    run = client.post("/api/cases/CASE_DEMO/run", params={"bank": "BANK_Z"}).json()
    assert run["as_of"] == "2026-10-18 11:13:00" and run["total_seconds"] < 30

    trail = client.get("/api/cases/CASE_DEMO/trail", params={"bank": "BANK_Z"}).json()
    kinds = {n["id"]: n["kind"] for n in trail["nodes"]}
    assert kinds["ACC_D"] == "own" and kinds["ACC_A"] == "other" and kinds["ACC_LAKSHMI"] == "victim"
    assert kinds["EXIT:ATM:BY-ATM-0042"] == "exit"
    b_to_d = next(e for e in trail["edges"] if e["to_account"] == "ACC_D")
    assert b_to_d["victim_inr"] == 500.0

    panel = client.get("/api/cases/CASE_DEMO/accounts/ACC_D", params={"bank": "BANK_Z"}).json()
    assert panel["score"]["band"] == "HONEST" and len(panel["reasons"]) == 3
    assert panel["recommendation"]["headline"] == "Hold Rs 1,000 only"
    libr = next(a for a in panel["attribution"] if a["trace_rule"] == "LIBR")
    assert libr["victim_amount_inr"] == 500.0

    refused = client.post("/api/cases/CASE_DEMO/accounts/ACC_D/reject", params={"bank": "BANK_Z"},
                          json={"officer": "kavitha", "reason": "  "})
    assert refused.status_code == 400 and "reason" in refused.json()["detail"]

    ok = client.post("/api/cases/CASE_DEMO/accounts/ACC_D/approve", params={"bank": "BANK_Z"},
                     json={"officer": "kavitha"}).json()
    notice = client.get(f"/api/decisions/{ok['decision_id']}/notice", params={"lang": "ta"}).json()
    assert "₹1,000" in notice["body"] and "Lakshmi" not in notice["body"]

    victim = client.get("/api/cases/CASE_DEMO/victim").json()
    assert victim["reported_inr"] == 200000.0 and victim["lost_to_exits_inr"] == 50000.0
    assert victim["secured_inr"] >= 500.0

    assert any(d["decision_id"] == ok["decision_id"] for d in client.get("/api/deadlines").json())
    client.post("/api/cases/CASE_DEMO/run", params={"bank": "BANK_X"})


def test_unknown_case_and_bank():
    assert client.get("/api/cases/NOPE/trail").status_code == 404
    assert client.get("/api/cases", params={"bank": "BANK_Q"}).status_code == 400