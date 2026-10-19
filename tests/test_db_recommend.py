"""AT-08 against Exasol, from each bank's point of view (DECISIONS D-01)."""
import pytest

from pipeline.steps import attribute, recommend, recommendations, score, trace

pytestmark = pytest.mark.db


@pytest.fixture(scope="module")
def conn():
    from pipeline.db import connect

    c = connect()
    yield c
    c.close()


def run_as(conn, bank):
    trace(conn, "CASE_DEMO", bank)
    attribute(conn, "CASE_DEMO", bank)
    score(conn, "CASE_DEMO")
    recommend(conn, "CASE_DEMO", bank)
    return {r["account_id"]: r for r in recommendations(conn, "CASE_DEMO", bank)}


def test_viewed_from_bank_x(conn):
    recs = run_as(conn, "BANK_X")
    a, b, c, d = recs["ACC_A"], recs["ACC_B"], recs["ACC_C"], recs["ACC_D"]
    atm = recs["EXIT:ATM:BY-ATM-0042"]
    assert (c["action_code"], float(c["amount_inr"]), c["draft_seizure"]) == ("AMOUNT_HOLD", 80000.0, True)
    assert (a["action_code"], float(a["amount_inr"])) == ("AMOUNT_HOLD", 500.0)
    assert (b["action_code"], float(b["amount_inr"])) == ("REFER_OUT", 119500.0)
    assert d["action_code"] == "NONE" and d["reason_code"] == "OTHER_BANK_DOWNSTREAM"
    assert atm["action_code"] == "NONE" and atm["reason_code"] == "EXIT"
    held = sum(float(r["amount_inr"]) for r in recs.values() if r["action_code"] == "AMOUNT_HOLD")
    assert held == 80500.0                         # Bank X's own share of the Rs 1,49,500 held in total


def test_viewed_from_murugans_bank(conn):
    recs = run_as(conn, "BANK_Z")
    assert recs["ACC_A"]["action_code"] == "NONE"   # Lakshmi's own payment is the complaint, not a referral
    d = recs["ACC_D"]
    assert (d["action_code"], float(d["amount_inr"])) == ("AMOUNT_HOLD", 1000.0)
    assert d["draft_narrowing"] and float(d["victim_amount_inr"]) == 500.0
    assert not d["draft_seizure"] and d["band"] == "HONEST"


def test_viewed_from_bank_y(conn):
    recs = run_as(conn, "BANK_Y")
    assert (recs["ACC_B"]["action_code"], float(recs["ACC_B"]["amount_inr"])) == ("AMOUNT_HOLD", 69000.0)
    assert (recs["ACC_D"]["action_code"], float(recs["ACC_D"]["amount_inr"])) == ("REFER_OUT", 500.0)
    run_as(conn, "BANK_X")                         # leave the demo in its usual state