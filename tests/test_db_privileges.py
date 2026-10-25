"""Done-whens for Module 1 and the ground rules, against the real database: each bank sees only its own data, the
maker can only propose, only the checker acts, and nobody but the admin can change the audit log.

    python -m pytest -m db tests/test_db_privileges.py
"""
import pytest

from tests.dbtools import connect_or_skip, one

pytestmark = pytest.mark.db


def denied(conn, sql: str) -> str | None:
    """Exasol's refusal, or None when the statement ran. A statement that selects no rows still needs the privilege,
    so these never change anything."""
    import pyexasol

    try:
        conn.execute(sql)
    except pyexasol.ExaQueryError as exc:
        return str(exc).lower()
    return None


def no_privilege(msg: str | None) -> bool:
    return msg is not None and ("privilege" in msg or "not found" in msg)


@pytest.fixture(scope="module")
def maker():
    c = connect_or_skip("maker", "BANK_X")
    yield c
    c.close()


@pytest.fixture(scope="module")
def checker():
    c = connect_or_skip("checker", "BANK_X")
    yield c
    c.close()


@pytest.fixture(scope="module")
def portal():
    c = connect_or_skip("portal")
    yield c
    c.close()


def test_bank_x_reads_its_own_schema(maker):
    assert one(maker, "SELECT COUNT(*) FROM TXN") > 0
    assert one(maker, "SELECT bank_id FROM BANK WHERE is_own_bank") == "BANK_X"


@pytest.mark.parametrize("schema", ["PH_Y", "PH_Z", "PH_PORTAL"])
def test_bank_x_cannot_read_another_bank_or_the_portal(maker, checker, schema):
    table = "TICKET" if schema == "PH_PORTAL" else "TXN"
    assert no_privilege(denied(maker, f"SELECT COUNT(*) FROM {schema}.{table}"))
    assert no_privilege(denied(checker, f"SELECT COUNT(*) FROM {schema}.{table}"))


@pytest.mark.parametrize("table", ["HOLD_LEDGER", "PORTAL_ENTRY", "HOLDER_MESSAGE", "DECISION", "VICTIM_SPLIT",
                                   "WATCH_LIST", "CASE_LINK", "CHECK_RESULT"])
def test_the_maker_cannot_write_what_only_the_checker_writes(maker, table):
    assert no_privilege(denied(maker, f"INSERT INTO {table} SELECT * FROM {table} WHERE FALSE"))
    assert no_privilege(denied(maker, f"DELETE FROM {table} WHERE FALSE"))


def test_the_maker_can_propose(maker):
    assert denied(maker, "INSERT INTO PROPOSAL SELECT * FROM PROPOSAL WHERE FALSE") is None


@pytest.mark.parametrize("role", ["maker", "checker"])
def test_nobody_but_the_admin_changes_the_audit_log(maker, checker, role):
    conn = maker if role == "maker" else checker
    assert denied(conn, "INSERT INTO AUDIT_LOG SELECT * FROM AUDIT_LOG WHERE FALSE") is None
    assert no_privilege(denied(conn, "UPDATE AUDIT_LOG SET detail = detail WHERE FALSE"))
    assert no_privilege(denied(conn, "DELETE FROM AUDIT_LOG WHERE FALSE"))


def test_the_checker_cannot_change_the_banks_transactions(checker):
    assert no_privilege(denied(checker, "INSERT INTO TXN SELECT * FROM TXN WHERE FALSE"))
    assert no_privilege(denied(checker, "UPDATE ACCOUNT SET holder_name = holder_name WHERE FALSE"))


def test_the_portal_reads_outboxes_and_writes_inboxes_only(portal):
    assert denied(portal, "SELECT COUNT(*) FROM PH_X.PORTAL_ENTRY") is None
    assert denied(portal, "INSERT INTO PH_X.TICKET SELECT * FROM PH_X.TICKET WHERE FALSE") is None
    for sql in ("SELECT COUNT(*) FROM PH_X.TXN", "SELECT COUNT(*) FROM PH_X.ACCOUNT",
                "SELECT COUNT(*) FROM PH_X.HOLD_LEDGER", "INSERT INTO PH_X.HOLD_LEDGER SELECT * FROM "
                "PH_X.HOLD_LEDGER WHERE FALSE"):
        assert no_privilege(denied(portal, sql)), sql