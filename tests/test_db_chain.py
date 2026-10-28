"""The referral hash chain in Exasol (DECISIONS D-17): Exasol's HASH_SHA256 and Python's hashlib agree on every hop,
each bank's checker verifies the ticket it received, a share changed on the way fails that check, and an onward entry
without a share is routed as "share unknown" with the whole transfer as the most to hold. Resets and runs the night.

    python -m pytest -m db tests/test_db_chain.py
"""
import pytest

from pipeline import chain
from tests.dbtools import connect_or_skip, one, q

pytestmark = pytest.mark.db


@pytest.fixture(scope="module")
def db():
    from pipeline.night import run_night

    connect_or_skip().close()
    run_night(until="2026-10-18 09:00:00", quiet=True, amlworld=False)
    c = connect_or_skip()
    yield c
    c.close()


def test_every_hop_of_the_trail_is_intact(db):
    hops = chain.verify(chain.tickets(db, "V_LAKSHMI"))
    banks = {h["to_bank_id"] for h in hops}
    assert {"BANK_X", "BANK_Y", "BANK_Z"} <= banks                     # police -> X -> Y -> Z
    assert all(h["intact"] for h in hops), [h for h in hops if not h["intact"]]


def test_every_ticket_on_the_portal_is_intact(db):
    hops = chain.verify(chain.tickets(db))
    assert hops and all(h["intact"] for h in hops), [h for h in hops if not h["intact"]]


def test_each_bank_checked_the_ticket_it_received(db):
    for schema, case in (("PH_X", "CASE_DEMO"), ("PH_Y", "CASE_DEMO-Y1")):
        passed, detail = q(db, f"SELECT check_passed, detail FROM {schema}.CHECK_RESULT WHERE case_id = {{c}} "
                               "AND check_name = 'referral_intact'", c=case)[0]
        assert passed and "matches the hash" in detail, (schema, detail)
    sent = one(db, "SELECT chain_hash FROM PH_X.PORTAL_ENTRY WHERE case_id = 'CASE_DEMO' AND entry_kind = 'ONWARD' "
                   "AND bank_id = 'BANK_Y'")
    assert sent == one(db, "SELECT chain_hash FROM PH_Y.COMPLAINT WHERE case_id = 'CASE_DEMO-Y1'")


def test_a_share_changed_on_the_way_fails_the_check(db):
    from pipeline import steps

    amount = one(db, "SELECT amount_inr FROM PH_Y.COMPLAINT WHERE case_id = 'CASE_DEMO-Y1'")
    chk = connect_or_skip("checker", "BANK_Y")
    try:
        db.execute("UPDATE PH_Y.COMPLAINT SET amount_inr = amount_inr + 1 WHERE case_id = 'CASE_DEMO-Y1'")
        steps.check(chk, "CASE_DEMO-Y1", "BANK_Y")
        passed, detail = q(db, "SELECT check_passed, detail FROM PH_Y.CHECK_RESULT WHERE case_id = 'CASE_DEMO-Y1' "
                               "AND check_name = 'referral_intact'")[0]
        assert not passed and "changed on the way" in detail
    finally:
        db.execute("UPDATE PH_Y.COMPLAINT SET amount_inr = {a!d} WHERE case_id = 'CASE_DEMO-Y1'", {"a": str(amount)})
        steps.check(chk, "CASE_DEMO-Y1", "BANK_Y")
        chk.close()
    assert one(db, "SELECT check_passed FROM PH_Y.CHECK_RESULT WHERE case_id = 'CASE_DEMO-Y1' "
                   "AND check_name = 'referral_intact'")


def test_an_entry_without_a_share_is_routed_as_share_unknown(db):
    from pipeline import portal

    db.execute("INSERT INTO PH_X.PORTAL_ENTRY (case_id, viewing_bank_id, entry_kind, from_account, account_id, "
               "bank_id, txn_id, utr, channel, amount_inr, victim_amount_inr, moved_at, detail, entry_status, "
               "filed_at, filed_by) VALUES ('CASE_DEMO', 'BANK_X', 'ONWARD', 'ACC_A', 'ACC_B', 'BANK_Y', 'T_TEST_SU', "
               "'UTRTESTSU', 'IMPS', 12345.00, NULL, TO_TIMESTAMP('2026-10-18 08:00:00', 'YYYY-MM-DD HH24:MI:SS'), "
               "'test: no share given', 'FILED', TO_TIMESTAMP('2026-10-18 08:01:00', 'YYYY-MM-DD HH24:MI:SS'), "
               "'test')")
    p = connect_or_skip("portal")
    try:
        portal.collect(p, "BANK_X")
    finally:
        p.close()
    try:
        amount, known = q(db, "SELECT amount_inr, share_known FROM PH_PORTAL.TICKET WHERE utr = 'UTRTESTSU'")[0]
        assert float(amount) == 12345.0 and known is False
        hop = next(h for h in chain.verify(chain.tickets(db)) if h["amount_inr"] == 12345.0)
        assert hop["intact"] and not hop["share_known"]                  # signed by the portal, linked to CASE_DEMO
    finally:
        db.execute("DELETE FROM PH_PORTAL.TICKET WHERE utr = 'UTRTESTSU'")
        db.execute("DELETE FROM PH_PORTAL.ENTRY WHERE utr = 'UTRTESTSU'")
        db.execute("DELETE FROM PH_X.PORTAL_ENTRY WHERE utr = 'UTRTESTSU'")