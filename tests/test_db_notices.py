"""Police notices against Exasol: two match their tickets, three go back with the reasons (DECISIONS D-41).
Needs `pytest -m db`."""
import pytest

pytestmark = pytest.mark.db
BANK = "BANK_X"


@pytest.fixture(scope="module")
def conn():
    from pipeline.db import connect
    from pipeline.notices import run_notices

    c = connect()
    c.execute("UPDATE PH.NOTICE SET notice_status = 'NEW', sent_at = NULL, sent_by = NULL WHERE viewing_bank_id = {b}",
              {"b": BANK})
    run_notices(c, BANK)
    yield c
    c.close()


def test_each_notice_gets_the_right_outcome(conn):
    got = conn.execute("SELECT notice_id, notice_status, case_id, suggestion_utr, asks_full_freeze FROM PH.NOTICE "
                       "WHERE viewing_bank_id = {b} ORDER BY notice_id", {"b": BANK}).fetchall()
    assert [tuple(r) for r in got] == [
        ("NTC_01", "LINKED", "CASE_DEMO", None, False),
        ("NTC_02", "LINKED", "CASE_X12", None, False),
        ("NTC_03", "SENT_BACK", None, "IMPS202610181401000030", True),
        ("NTC_04", "SENT_BACK", None, None, False),
        ("NTC_05", "SENT_BACK", "CASE_X05", None, True),
    ]


def test_the_letter_names_the_nearest_match_and_the_amount(conn):
    text = conn.execute("SELECT reply_text FROM PH.NOTICE WHERE notice_id = 'NTC_03'").fetchval()
    assert "The nearest match received that day is IMPS202610181401000030: Rs 2,46,800 at 14:01" in text
    assert "that nearest match is Rs 2,46,800" in text and "whole account to be frozen" in text


def test_nothing_is_held_on_a_notice(conn):
    assert conn.execute("SELECT COUNT(*) FROM PH.HOLD_LEDGER WHERE case_id LIKE 'NTC%'").fetchval() == 0


def test_the_officer_sends_a_letter_once(conn):
    from fastapi.testclient import TestClient

    from app.api.main import app

    client = TestClient(app)
    assert client.post("/api/notices/NTC_04/send", json={"officer": ""}).status_code == 400
    assert client.post("/api/notices/NTC_04/send", json={"officer": "priya"}).status_code == 200
    assert client.post("/api/notices/NTC_04/send", json={"officer": "priya"}).status_code == 400
    assert client.post("/api/notices/NTC_01/send", json={"officer": "priya"}).status_code == 400
    notices = {n["notice_id"]: n for n in client.get("/api/notices").json()}
    assert notices["NTC_04"]["sent_by"] == "priya" and notices["NTC_01"]["problems"] == []