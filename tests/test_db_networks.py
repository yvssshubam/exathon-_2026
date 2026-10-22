"""Mule networks against Exasol: on the demo ticket, A paid C, so A and C form one network (DECISIONS D-40).
Needs `pytest -m db`."""
import pytest

pytestmark = pytest.mark.db
BANK = "BANK_X"


@pytest.fixture(scope="module")
def conn():
    from pipeline.agent import work_ticket
    from pipeline.db import connect
    from pipeline.networks import run_networks
    from pipeline.watch import run_watch

    c = connect()
    for table in ("HOLD_LEDGER", "PORTAL_ENTRY", "DECISION"):
        c.execute(f"DELETE FROM PH.{table} WHERE case_id = 'CASE_DEMO'")
    work_ticket(c, "CASE_DEMO", BANK)
    run_watch(c, BANK)
    run_networks(c, BANK)
    yield c
    c.close()


def test_a_and_c_are_one_network_linked_by_the_money_a_sent_c(conn):
    from pipeline.networks import details

    net = next(n for n in details(conn, BANK) if any(m["account_id"] == "ACC_A" for m in n["members"]))
    assert [m["account_id"] for m in net["members"]] == ["ACC_A", "ACC_C"]
    assert [(lk["from_account"], lk["to_account"], lk["kind"], lk["amount_inr"]) for lk in net["links"]] == [
        ("ACC_A", "ACC_C", "MONEY", 80000.0)]
    assert [t["case_id"] for t in net["tickets"]] == ["CASE_DEMO"]
    assert net["refused"] == 2 and net["held_inr"] == 79500.0


def test_murugan_is_never_in_a_network(conn):
    assert conn.execute("SELECT COUNT(*) FROM PH.NETWORK_MEMBER WHERE account_id = 'ACC_D'").fetchval() == 0


def test_the_routes_serve_the_network_and_its_note(conn):
    from fastapi.testclient import TestClient

    from app.api.main import app

    client = TestClient(app)
    net = next(n for n in client.get("/api/networks").json() if n["members"][0]["account_id"] == "ACC_A")
    note = client.get(f"/api/networks/{net['network_id']}/reasons-draft").json()["text"]
    assert note.startswith("2 accounts at Bank X act as one network: A, C.") and "CASE_DEMO (02:13" in note