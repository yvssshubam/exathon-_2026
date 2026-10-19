"""Database setup tests. Need a running Exasol Personal: `pytest -m db` (make check-db runs them)."""
import pytest

pytestmark = pytest.mark.db


@pytest.fixture(scope="module")
def conn():
    from pipeline.db import connect

    c = connect()
    yield c
    c.close()


def test_python3_udf_runs(conn):
    assert conn.execute("SELECT PH.SMOKE_PYTHON()").fetchval().startswith("python 3")


def test_every_ph_table_and_column_has_a_comment(conn):
    missing_tables = conn.execute(
        "SELECT TABLE_NAME FROM EXA_ALL_TABLES WHERE TABLE_SCHEMA = 'PH' AND TABLE_COMMENT IS NULL"
    ).fetchall()
    missing_cols = conn.execute(
        "SELECT COLUMN_TABLE || '.' || COLUMN_NAME FROM EXA_ALL_COLUMNS "
        "WHERE COLUMN_SCHEMA = 'PH' AND COLUMN_COMMENT IS NULL"
    ).fetchall()
    assert missing_tables == [] and missing_cols == []


def test_demo_case_loaded_exactly(conn):
    rows = conn.execute(
        "SELECT txn_id, amount_inr, is_exit FROM PH.TXN WHERE txn_id LIKE 'T_DEMO_%' ORDER BY ts, txn_id"
    ).fetchall()
    assert [(r[0], float(r[1]), r[2]) for r in rows] == [
        ("T_DEMO_01", 200000.0, False), ("T_DEMO_02", 120000.0, False), ("T_DEMO_03", 80000.0, False),
        ("T_DEMO_04", 1000.0, False), ("T_DEMO_05", 50000.0, True), ("T_DEMO_06", 80000.0, True),
    ]
    assert conn.execute("SELECT COUNT(*) FROM PH.BANK WHERE is_own_bank").fetchval() == 1
    assert float(conn.execute(
        "SELECT balance_inr FROM PH.BALANCE_SNAPSHOT WHERE account_id = 'ACC_D'").fetchval()) == 819000.0


def test_audit_log_has_statement_ids(conn):
    n = conn.execute("SELECT COUNT(*) FROM PH.AUDIT_LOG WHERE stmt_id > 0 AND session_id IS NOT NULL").fetchval()
    assert n >= 1