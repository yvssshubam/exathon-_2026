"""The API exposes every console screen's data and no way to hold money without an officer (no database needed)."""
from fastapi.testclient import TestClient

from app.api.main import app, get_conn, rows


def paths():
    return {(m, r.path) for r in app.routes for m in getattr(r, "methods", set())}


def test_every_screen_has_its_route():
    expected = {
        ("GET", "/api/health"), ("GET", "/api/config"), ("GET", "/api/cases"), ("GET", "/api/tickets"),
        ("POST", "/api/cases/{case_id}/run"), ("GET", "/api/cases/{case_id}"),
        ("GET", "/api/cases/{case_id}/trail"), ("GET", "/api/cases/{case_id}/recommendations"),
        ("GET", "/api/cases/{case_id}/accounts/{account_id}"),
        ("POST", "/api/cases/{case_id}/accounts/{account_id}/approve"),
        ("POST", "/api/cases/{case_id}/accounts/{account_id}/reject"),
        ("POST", "/api/decisions/{decision_id}/grievance"), ("GET", "/api/decisions"),
        ("GET", "/api/decisions/{decision_id}/notice"), ("GET", "/api/deadlines"),
        ("GET", "/api/cases/{case_id}/victim"), ("GET", "/api/audit"),
    }
    assert expected <= paths()


def test_no_auto_approve_anywhere():
    assert not any("auto" in p.lower() for _, p in paths())


def test_config_without_database():
    body = TestClient(app).get("/api/config").json()
    assert {b["bank_id"] for b in body["banks"]} == {"BANK_X", "BANK_Y", "BANK_Z"}
    assert body["lanes"]["B"]["in_force"] is False


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


def test_approve_requires_an_officer_field():
    app.dependency_overrides[get_conn] = lambda: None     # no database: the request must fail validation first
    try:
        r = TestClient(app).post("/api/cases/CASE_DEMO/accounts/ACC_C/approve", json={})
    finally:
        app.dependency_overrides.clear()
    assert r.status_code == 422