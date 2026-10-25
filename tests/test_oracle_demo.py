"""AT-04 (oracle side): the demo case's LIBR answers, to the paisa, at the moment the agent's holds land."""
import pytest

from pipeline.cases import load_case, paise
from tests.helpers import run

case = load_case()
AS_OF = case.tool_as_of
VICTIM = "V_LAKSHMI"


def test_case_file_is_the_2_13_am_story():
    assert case.complaints[0]["reported_at"] == "2026-10-18 02:13:00"
    assert case.tool_as_of == "2026-10-18 02:16:00"
    assert case.complaints[0]["utr"] == "IMPS2026101801520001"
    assert case.opening_paise() == {"ACC_A": 0, "ACC_B": 0, "ACC_C": 50000, "ACC_D": 81900000, "ACC_E": 0,
                                    "ACC_K": 0, "ACC_G": 0, "ACC_F": 0}
    assert case.account("ACC_D")["bank_id"] == "BANK_X"          # Murugan banks where the tool runs
    assert case.account("ACC_B")["bank_id"] == "BANK_Y"          # the onward entry goes to Bank Y
    assert case.account("ACC_G")["bank_id"] == "BANK_Z"          # and Bank Y's goes to Bank Z
    assert [c["case_id"] for c in case.police_tickets()] == ["CASE_DEMO", "CASE_BADUTR", "CASE_DEMO_V2", "CASE_DEMO_IO"]


@pytest.mark.parametrize("account", ["ACC_A", "ACC_B", "ACC_C", "ACC_D", "EXIT:ATM:BX-ATM-0042"])
def test_libr_equals_the_hand_checked_table(account):
    r = run(case, "LIBR", AS_OF)
    assert r.tainted(account, VICTIM) == paise(case.expected_libr[account])


def test_libr_conserves_the_reported_amount():
    r = run(case, "LIBR", AS_OF)
    assert r.victim_total(VICTIM) == paise(case.expected_libr["total"]) == r.reported[VICTIM]


def test_murugans_own_money_never_mixes():
    r = run(case, "LIBR", AS_OF)
    # His Rs 8,19,000 plus the Rs 500 of C's own float that rode along in the Rs 1,000 diesel payment
    assert r.clean("ACC_D") == paise("819500.00")


def test_tool_holds_80000_of_the_victims_money_at_bank_x():
    r = run(case, "LIBR", AS_OF)
    held = sum(r.tainted(a, VICTIM) for a in ("ACC_A", "ACC_C", "ACC_D"))
    assert held == paise(case.expected_compare["victim_money_held"]["tool"])


def test_by_the_time_the_desks_open_the_victims_money_is_gone():
    """With nobody acting (today): what is left of Lakshmi's money when the desks open at 10."""
    at_desk = run(case, "LIBR", "2026-10-18 10:20:00")
    assert at_desk.tainted("ACC_A", VICTIM) == at_desk.tainted("ACC_C", VICTIM) == 0
    assert at_desk.tainted("ACC_A", "V_RAVI") == paise(40000)                 # only victim 2's money is in A
    assert at_desk.tainted("EXIT:ATM:BX-ATM-0117") == paise(29500)          # C at 04:30
    assert at_desk.tainted("ACC_F", VICTIM) == paise(50000)                  # A sent it to Bank Z at 05:10
    assert at_desk.tainted("EXIT:ATM:BY-ATM-0221", VICTIM) == paise(40000)   # B cashed out at Bank Y at 07:00
    assert at_desk.tainted("EXIT:ATM:BZ-ATM-0310", VICTIM) == paise(30000)   # G cashed out at Bank Z at 03:40
    assert at_desk.tainted("ACC_D", VICTIM) == paise(500)                     # the only victim money left in a bank


def test_bank_x_sees_only_its_own_accounts():
    from pipeline.cases import bank_view

    x = run(bank_view(case, "BANK_X"), "LIBR", AS_OF)
    for account, rupees in case.expected_libr_bank_x.items():
        if account != "total":
            assert x.tainted(account, VICTIM) == paise(rupees), account
    assert x.tainted("ACC_G", VICTIM) == 0                                   # Bank X never sees B's hop to G


def test_fifo_equals_libr_on_demo_case():
    # DECISIONS D-04: C's own Rs 500 is its oldest money, so FIFO also spends it first.
    libr, fifo = run(case, "LIBR", AS_OF), run(case, "FIFO", AS_OF)
    for acc in ("ACC_A", "ACC_B", "ACC_C", "ACC_D", "EXIT:ATM:BX-ATM-0042"):
        assert fifo.tainted(acc) == libr.tainted(acc), acc


def test_pro_rata_spreads_cs_float():
    # C held 500 own + 80,000 victim; the Rs 1,000 to D takes 500/80,500 of it as C's own money: Rs 6.21.
    r = run(case, "PRO_RATA", AS_OF)
    assert r.tainted("ACC_D") == paise("993.79")
    assert r.victim_total(VICTIM) == paise(200000)