"""Mule networks (pipeline/networks.py, DECISIONS D-40). No database."""
from decimal import Decimal as D

from pipeline import networks as nw


def link(a, b, kind="MONEY", amount="1000", detail=""):
    return nw.Link(a, b, kind, 1, D(amount), "2026-10-18 10:00:00", "2026-10-18 10:00:00", detail)


def test_components_largest_first_and_singletons_dropped():
    cands = ["M01", "M02", "M03", "M04", "M05", "M06", "A", "C", "LONE"]
    links = [link("M02", "M09"), link("M01", "M03"), link("M03", "M02"), link("M05", "M06"), link("A", "C"),
             link("X", "M01")]                                      # X is not a candidate: ignored
    nets = nw.build(cands, links)
    assert [(n.network_id, n.members) for n in nets] == [
        ("N1", ["M01", "M02", "M03"]), ("N2", ["A", "C"]), ("N3", ["M05", "M06"])]
    assert [(lk.a, lk.b) for lk in nets[0].links] == [("M01", "M03"), ("M03", "M02")]


def test_a_shared_cash_out_point_joins_two_accounts():
    nets = nw.build(["M07", "M08"], [link("M07", "M08", "SHARED_EXIT", detail="ATM BX-ATM-0385")])
    assert nets[0].members == ["M07", "M08"] and nets[0].links[0].kind == "SHARED_EXIT"


def test_no_links_no_networks():
    assert nw.build(["A", "B"], []) == []


NET = {
    "network_id": "N2", "bank_name": "Bank X",
    "members": [{"account_id": "ACC_A", "name": "A"}, {"account_id": "ACC_C", "name": "C"}],
    "links": [{"kind": "MONEY", "from_name": "A", "to_name": "C", "count": 1, "amount_inr": 80000.0},
              {"kind": "TRIED", "from_name": "C", "to_name": "A", "count": 1, "amount_inr": 500.0},
              {"kind": "SHARED_EXIT", "from_name": "A", "to_name": "C", "count": 2, "amount_inr": 79500.0,
               "detail": "ATM BX-ATM-0117"}],
    "tickets": [{"case_id": "CASE_DEMO", "received_at": "2026-10-18 02:13:00", "reported_inr": 200000.0,
                 "victim_inr": 79500.0}],
    "reported_inr": 200000.0, "victim_inr": 79500.0, "held_inr": 79500.0, "refused": 2,
}


def test_the_network_note_states_the_links_tickets_and_money():
    text = nw.template(NET)
    assert text.startswith("2 accounts at Bank X act as one network: A, C.")
    assert "A paid C Rs 80,000 in the 30 days before the latest ticket." in text
    assert "A whole-account freeze of both accounts is proposed" in text
    assert "A and C both cashed out at ATM BX-ATM-0117." in text
    assert "Payments between them refused because the money was held: C to A Rs 500." in text
    assert "Rs 80,500" not in text                             # the refused Rs 500 is not counted as paid
    assert ("Together they are named in 1 fraud ticket reporting Rs 2,00,000; Rs 79,500 of the victims' money was "
            "traced into them and Rs 79,500 is held amount-only.") in text
    assert "Since the holds, 2 attempts to move held money were refused." in text
    assert text.endswith("CASE_DEMO (02:13, Rs 2,00,000).")


def test_the_draft_falls_back_when_the_model_adds_a_number():
    class Model:
        def write(self, system, facts):
            return "These 3 accounts moved Rs 9,99,999."

    assert nw.draft(NET, Model())["written_by"] == "template"


def test_a_refused_payment_still_links_two_accounts():
    nets = nw.build(["M01", "M10"], [link("M01", "M10", "TRIED", "68760")])
    assert nets[0].members == ["M01", "M10"]