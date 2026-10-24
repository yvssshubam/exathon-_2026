"""The API exposes every console screen's data and no way to freeze an account without an officer
(no database needed)."""
from fastapi.testclient import TestClient

from app.api.main import after_hours, app, get_conn, rows


def paths():
    return {(m, r.path) for r in app.routes for m in getattr(r, "methods", set())}


def test_every_screen_has_its_route():
    expected = {
        ("GET", "/api/health"), ("GET", "/api/config"), ("GET", "/api/queue"), ("POST", "/api/queue/work"),
        ("POST", "/api/tickets/{case_id}/work"), ("GET", "/api/inbox"), ("GET", "/api/cases/{case_id}"),
        ("GET", "/api/cases/{case_id}/trail"), ("GET", "/api/cases/{case_id}/actions"),
        ("GET", "/api/cases/{case_id}/accounts/{account_id}"), ("GET", "/api/cases/{case_id}/log"),
        ("GET", "/api/cases/{case_id}/paperwork"), ("GET", "/api/cases/{case_id}/compare"),
        ("POST", "/api/cases/{case_id}/accounts/{account_id}/approve"),
        ("POST", "/api/cases/{case_id}/accounts/{account_id}/reject"),
        ("POST", "/api/cases/{case_id}/paperwork/{entry_id}/send"), ("GET", "/api/holds"),
        ("GET", "/api/decisions"), ("GET", "/api/audit"), ("POST", "/api/demo/reset"), ("GET", "/api/day"),
        ("GET", "/api/watch"), ("POST", "/api/watch/run"),
        ("GET", "/api/accounts/{account_id}/reasons-draft"),
        ("GET", "/api/networks"), ("GET", "/api/networks/{network_id}/reasons-draft"),
        ("GET", "/api/notices"), ("POST", "/api/notices/run"), ("POST", "/api/notices/{notice_id}/send"),
    }
    assert expected <= paths()


def test_no_freeze_route_and_no_auto_approve():
    assert not any("freeze" in p.lower() or "auto" in p.lower() for _, p in paths())


def test_config_without_database():
    body = TestClient(app).get("/api/config").json()
    assert {b["bank_id"] for b in body["banks"]} == {"BANK_X", "BANK_Y", "BANK_Z"}
    assert "PROPOSE_FULL_FREEZE" in body["officer"] and "MANDATORY_HOLD" in body["automatic"]


def test_bare_address_opens_docs_without_a_cacheable_redirect():
    r = TestClient(app).get("/", follow_redirects=False)
    assert r.status_code == 307 and r.headers["location"] == "/docs"


def test_decimal_columns_come_back_as_numbers():
    class Stmt:                      # what pyexasol hands back: DECIMAL values as strings
        def columns(self):
            return {"A": {"type": "DECIMAL", "scale": 0}, "B": {"type": "DECIMAL", "scale": 2},
                    "C": {"type": "VARCHAR"}, "D": {"type": "DECIMAL", "scale": 0}}

        def column_names(self):
            return ["A", "B", "C", "D"]

        def fetchall(self):
            return [("500", "80000.00", "500", None)]

    class Conn:
        def execute(self, sql, params):
            return Stmt()

    assert rows(Conn(), "SELECT 1") == [{"a": 500, "b": 80000.0, "c": "500", "d": None}]


def test_approve_requires_an_officer_and_an_action():
    app.dependency_overrides[get_conn] = lambda: None     # no database: the request must fail validation first
    try:
        r = TestClient(app).post("/api/cases/CASE_DEMO/accounts/ACC_D/approve", json={"officer": "priya"})
    finally:
        app.dependency_overrides.clear()
    assert r.status_code == 422


def test_desk_hours():
    assert after_hours("2026-10-18 02:13:00") and after_hours("2026-10-18 21:00:00")
    assert not after_hours("2026-10-18 11:47:00")