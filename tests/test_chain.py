"""The referral hash chain (DECISIONS D-17), no database: the canonical text, and that verification catches a changed
share, a broken link and a changed share-unknown flag."""
import hashlib

from pipeline import chain


def ticket(case, to, origin, parent=None, prev=None, amount="50000.00", known=True, utr="UTR1"):
    t = {"case_id": case, "to_bank_id": to, "origin": origin, "parent_case_id": parent, "ack_no": "21234567890123",
         "victim_tag": "V_LAKSHMI", "beneficiary_account": f"ACC_{to[-1]}", "utr": utr, "amount_inr": amount,
         "share_known": known, "txn_at": "2026-10-18 02:05:00.000000", "prev_hash": prev}
    t["chain_hash"] = chain.digest(prev, t["ack_no"], t["victim_tag"], origin, to, t["beneficiary_account"], utr,
                                   amount, known, t["txn_at"])
    return t


def trail():
    root = ticket("CASE_DEMO", "BANK_X", "POLICE", amount="200000.00")
    y = ticket("CASE_DEMO-Y1", "BANK_Y", "BANK_X", "CASE_DEMO", root["chain_hash"], "70000.00", utr="UTR2")
    z = ticket("CASE_DEMO-Y1-Z1", "BANK_Z", "BANK_Y", "CASE_DEMO-Y1", y["chain_hash"], "30000.00", utr="UTR3")
    return [root, y, z]


def test_the_canonical_text_is_fixed():
    text = chain.canonical(None, "21234567890123", "V_LAKSHMI", None, "BANK_X", "ACC_A", "UTR1", "200000.00", True,
                           "2026-10-18 02:05:00.000000")
    assert text == "PHv1|-|21234567890123|V_LAKSHMI|POLICE|BANK_X|ACC_A|UTR1|20000000|Y|2026-10-18 02:05:00"
    assert chain.digest(None, "21234567890123", "V_LAKSHMI", None, "BANK_X", "ACC_A", "UTR1", "200000.00", True,
                        "2026-10-18 02:05:00") == hashlib.sha256(text.encode()).hexdigest()
    assert chain.paise("29500.5") == "2950050" and chain.paise(None) == "-"


def test_an_untouched_trail_is_intact():
    hops = chain.verify(trail())
    assert [h["intact"] for h in hops] == [True, True, True]


def test_a_changed_share_is_caught_at_that_hop():
    t = trail()
    t[1]["amount_inr"] = "90000.00"                      # someone raises Bank Y's share on the way
    hops = chain.verify(t)
    assert [h["fields_match"] for h in hops] == [True, False, True]
    assert not hops[1]["intact"]


def test_a_re_signed_change_breaks_the_next_link():
    t = trail()
    t[1]["amount_inr"] = "90000.00"                      # changed and re-hashed, but Bank Z's ticket names the old hash
    t[1]["chain_hash"] = chain.digest(t[1]["prev_hash"], t[1]["ack_no"], t[1]["victim_tag"], "BANK_X", "BANK_Y",
                                      t[1]["beneficiary_account"], t[1]["utr"], "90000.00", True, t[1]["txn_at"])
    hops = chain.verify(t)
    assert hops[1]["fields_match"] and not hops[2]["linked"]


def test_share_unknown_is_part_of_what_is_signed():
    t = trail()
    t[2] = ticket("CASE_DEMO-Y1-Z1", "BANK_Z", "BANK_Y", "CASE_DEMO-Y1", t[1]["chain_hash"], "40000.00", known=False)
    assert chain.verify(t)[2]["intact"] and not chain.verify(t)[2]["share_known"]
    t[2]["share_known"] = True                           # flipping the flag is a change too
    assert not chain.verify(t)[2]["fields_match"]