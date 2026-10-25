"""With the AMLworld background loaded (`make data`), every bank gets only the rows touching its own branches, and the
demo night gives the same 9 a.m. as without it (DECISIONS D-37). Skipped when PH_AML is not loaded. Slow: it reloads
the background into every bank.

    python -m pytest -m db tests/test_db_amlworld.py
"""
import pytest

from tests.dbtools import connect_or_skip, one, q

pytestmark = pytest.mark.db


@pytest.fixture(scope="module")
def db():
    from pipeline.load_cases import amlworld_loaded
    from pipeline.night import run_night

    c = connect_or_skip()
    if not amlworld_loaded(c):
        c.close()
        pytest.skip("AMLworld is not loaded (run `make data`)")
    run_night(until="2026-10-18 09:00:00", quiet=True, amlworld=True)
    yield c
    c.close()


@pytest.mark.parametrize("schema,bank", [("PH_X", "BANK_X"), ("PH_Y", "BANK_Y"), ("PH_Z", "BANK_Z")])
def test_each_bank_has_its_share_of_the_background_and_nothing_else(db, schema, bank):
    assert one(db, f"SELECT COUNT(*) FROM {schema}.TXN WHERE data_source = 'amlworld'") > 0
    # two equality joins, not a lookup per row: each bank has millions of AMLworld rows
    stray = one(db, f"""SELECT COUNT(*) FROM {schema}.TXN t
                        LEFT JOIN (SELECT account_id FROM {schema}.ACCOUNT WHERE bank_id = {{b}}) f
                               ON f.account_id = t.from_account
                        LEFT JOIN (SELECT account_id FROM {schema}.ACCOUNT WHERE bank_id = {{b}}) g
                               ON g.account_id = t.to_account
                        WHERE t.data_source = 'amlworld' AND f.account_id IS NULL AND g.account_id IS NULL""",
                b=bank)
    assert stray == 0
    # another bank's accounts arrive as bare rows: no KYC, no history
    assert one(db, f"SELECT COUNT(*) FROM {schema}.ACCOUNT WHERE bank_id <> {{b}} AND opened_on IS NOT NULL",
               b=bank) == 0


def test_the_demo_morning_is_the_same_with_the_background(db):
    got = set(q(db, "SELECT case_id, account_id, action_code FROM PH_X.PROPOSAL WHERE proposal_status = 'WAITING'"))
    assert got == {("CASE_DEMO", "ACC_D", "PROPOSE_NARROW"), ("CASE_DEMO", "ACC_C", "PROPOSE_FULL_FREEZE"),
                   ("CASE_BADUTR", "ACC_K", "UNMATCHED_NOTICE")}