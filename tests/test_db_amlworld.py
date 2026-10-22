"""AMLworld in Exasol (AT-01, DECISIONS D-37). Needs `make data`; skipped when PH_AML is not loaded."""
import pytest

pytestmark = pytest.mark.db


@pytest.fixture(scope="module")
def conn():
    from pipeline.db import connect

    c = connect()
    if not c.execute("SELECT COUNT(*) FROM EXA_ALL_TABLES WHERE TABLE_SCHEMA = 'PH_AML' "
                     "AND TABLE_NAME = 'TXN'").fetchval():
        c.close()
        pytest.skip("AMLworld not loaded (make data)")
    yield c
    c.close()


def test_background_is_copied_next_to_the_cases(conn):
    n_stage = conn.execute("SELECT COUNT(*) FROM PH_AML.TXN").fetchval()
    n_ph = conn.execute("SELECT COUNT(*) FROM PH.TXN WHERE data_source = 'amlworld'").fetchval()
    assert n_stage > 1_000_000 and n_ph == n_stage


def test_background_ends_before_the_case_day(conn):
    assert str(conn.execute("SELECT MAX(ts) FROM PH_AML.TXN").fetchval()) < "2026-10-18"


def test_every_background_transaction_names_known_accounts(conn):
    assert conn.execute(
        "SELECT COUNT(*) FROM PH_AML.TXN t LEFT JOIN PH_AML.ACCOUNT a ON a.account_id = t.to_account "
        "WHERE a.account_id IS NULL").fetchval() == 0


def test_still_exactly_one_own_bank_and_no_id_clash(conn):
    assert conn.execute("SELECT COUNT(*) FROM PH.BANK WHERE is_own_bank").fetchval() == 1
    # pyexasol returns the difference of two counts as a decimal string
    assert int(conn.execute("SELECT COUNT(*) - COUNT(DISTINCT txn_id) FROM PH.TXN").fetchval()) == 0
    assert int(conn.execute("SELECT COUNT(*) - COUNT(DISTINCT account_id) FROM PH.ACCOUNT").fetchval()) == 0


def test_no_trace_walks_into_the_background(conn):
    assert conn.execute("SELECT COUNT(*) FROM PH.TRACE_EDGE WHERE to_account LIKE 'AML\\_%' ESCAPE '\\'"
                        ).fetchval() == 0