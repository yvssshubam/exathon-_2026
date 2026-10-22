"""Freeze reasons drafted from Exasol for mule A on the demo ticket (DECISIONS D-39). Needs `pytest -m db`."""
import pytest

pytestmark = pytest.mark.db
BANK = "BANK_X"


@pytest.fixture(scope="module")
def conn():
    from pipeline.agent import work_ticket
    from pipeline.db import connect
    from pipeline.watch import run_watch

    c = connect()
    for table in ("HOLD_LEDGER", "PORTAL_ENTRY", "DECISION"):
        c.execute(f"DELETE FROM PH.{table} WHERE case_id = 'CASE_DEMO'")
    work_ticket(c, "CASE_DEMO", BANK)
    run_watch(c, BANK)
    yield c
    c.close()


def test_the_draft_for_a_uses_the_ticket_the_history_and_the_night(conn):
    from pipeline.reasons import draft, facts

    f = facts(conn, BANK, "ACC_A")
    assert [t["case_id"] for t in f["tickets"]] == ["CASE_DEMO"]
    text = draft(f)["text"]
    assert text.startswith("A holds a savings account at Bank X, opened 20 days before the first ticket.")
    assert "the 02:13 ticket, Rs 2,00,000 reported, Rs 50,000 of it traced into this account" in text
    assert "05:10: A tried to move Rs 50,000 to a crypto wallet; refused" in text
    assert "Its balance is Rs 50,000, of which Rs 50,000 is already held amount-only." in text


def test_the_route_serves_it(conn):
    from fastapi.testclient import TestClient

    from app.api.main import app

    r = TestClient(app).get("/api/accounts/ACC_A/reasons-draft")
    assert r.status_code == 200 and "02:13 ticket" in r.json()["text"]
    assert TestClient(app).get("/api/accounts/NOPE/reasons-draft").status_code == 404