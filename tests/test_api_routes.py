"""The API exposes every console screen's data, only four decisions for the officer, and no way to hold, freeze or
file anything without one (no database needed)."""
from fastapi.testclient import TestClient

from app.api.main import after_hours, app, get_checker, rows


def paths():
    return {(m, r.path) for r in app.routes for m in getattr(r, "methods", set())}


def test_every_screen_has_its_route():
    expected = {
        ("GET", "/api/health"), ("GET", "/api/config"), ("GET", "/api/clock"), ("POST", "/api/night/run"),
        ("POST", "/api/demo/reset"), ("GET", "/api/narrator"), ("GET", "/api/queue"),
        ("POST", "/api/tickets/{case_id}/work"), ("GET", "/api/inbox"), ("GET", "/api/watch"),
        ("POST", "/api/proposals/{proposal_id}/approve"), ("POST", "/api/proposals/{proposal_id}/reject"),
        ("GET", "/api/accounts/{account_id}/record"), ("GET", "/api/accounts/{account_id}/record.txt"),
        ("GET", "/api/cases/{case_id}"), ("GET", "/api/cases/{case_id}/trail"), ("GET", "/api/cases/{case_id}/actions"),
        ("GET", "/api/cases/{case_id}/accounts/{account_id}"), ("GET", "/api/cases/{case_id}/log"),
        ("GET", "/api/cases/{case_id}/paperwork"), ("GET", "/api/cases/{case_id}/compare"), ("GET", "/api/day"),
        ("GET", "/api/holds"), ("GET", "/api/decisions"), ("GET", "/api/audit"),
    }
    assert expected <= paths()


def test_dropped_features_have_no_route():
    gone = ("notices", "networks", "reasons-draft", "paperwork/{entry_id}/send", "query", "ask", "chat")
    assert not any(g in p for _, p in paths() for g in gone)


def test_no_route_places_a_hold_or_freeze_by_itself():
    writes = {p for m, p in paths() if m == "POST"}
    assert not any(word in p.lower() for p in writes for word in ("freeze", "hold", "auto", "file"))
    assert writes == {"/api/night/run", "/api/demo/reset", "/api/tickets/{case_id}/work",
                      "/api/proposals/{proposal_id}/approve", "/api/proposals/{proposal_id}/reject"}


def test_config_without_database():
    body = TestClient(app).get("/api/config").json()
    assert {b["bank_id"] for b in body["banks"]} == {"BANK_X", "BANK_Y", "BANK_Z"}
    assert set(body["officer"]) == {"PROPOSE_NARROW", "PROPOSE_FULL_FREEZE", "UNMATCHED_NOTICE", "PROPOSE_RELEASE"}
    assert "MANDATORY_HOLD" in body["automatic"] and "KYC_PACKET" in body["automatic"]
    assert {b["bank_id"]: b["works_tickets"] for b in body["banks"]} == {
        "BANK_X": "agent", "BANK_Y": "agent", "BANK_Z": "desk"}


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


def test_a_decision_needs_an_officer():
    app.dependency_overrides[get_checker] = lambda: None     # no database: the request must fail validation first
    try:
        r = TestClient(app).post("/api/proposals/7/approve", json={"comment": "ok"})
    finally:
        app.dependency_overrides.clear()
    assert r.status_code == 422


def test_unknown_bank_is_refused():
    r = TestClient(app).get("/api/queue", params={"bank": "BANK_Q"})
    assert r.status_code == 400


def test_desk_hours():
    assert after_hours("2026-10-18 02:13:00") and after_hours("2026-10-18 21:00:00")
    assert not after_hours("2026-10-18 11:00:00")


def test_routes_called_from_python_pass_every_query_setting():
    """A route function called directly gets FastAPI's Query objects as defaults, not values: pass them all."""
    import ast
    import inspect

    from app.api import main

    src = inspect.getsource(main)
    routes = {n.name: n for n in ast.walk(ast.parse(src)) if isinstance(n, ast.FunctionDef)
              and any(isinstance(d, ast.Call) and getattr(d.func, "attr", "") in ("get", "post")
                      for d in n.decorator_list)}
    for node in ast.walk(ast.parse(src)):
        if isinstance(node, ast.Call) and getattr(node.func, "id", None) in routes:
            wanted = len(routes[node.func.id].args.args)
            assert len(node.args) + len(node.keywords) == wanted, (node.func.id, node.lineno)