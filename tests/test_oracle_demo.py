"""AT-04 (oracle side): the section 9 LIBR table, to the paisa, on the section 3 demo case."""
import pytest

from pipeline.cases import load_case, paise
from tests.helpers import run

case = load_case()
AS_OF = case.tool_as_of
VICTIM = "V_LAKSHMI"


def test_case_file_matches_section_3():
    assert case.tool_as_of == "2026-10-18 11:13:00"
    assert case.complaints[0]["utr"] == "IMPS2026101810020001"
    assert case.opening_paise() == {"ACC_A": 50000, "ACC_B": 0, "ACC_C": 0, "ACC_D": 81900000}
    assert [t.txn_id for t in case.txns][:3] == ["T_DEMO_01", "T_DEMO_02", "T_DEMO_03"]


@pytest.mark.parametrize("account", ["ACC_A", "ACC_B", "ACC_C", "ACC_D", "EXIT:ATM:BY-ATM-0042"])
def test_libr_equals_section_9_table(account):
    r = run(case, "LIBR", AS_OF)
    assert r.tainted(account, VICTIM) == paise(case.expected_libr[account])


def test_libr_conserves_the_reported_amount():
    r = run(case, "LIBR", AS_OF)
    assert r.victim_total(VICTIM) == paise(case.expected_libr["total"]) == r.reported[VICTIM]


def test_d_keeps_its_own_money_untouched():
    r = run(case, "LIBR", AS_OF)
    # D's Rs 8,19,000 plus the Rs 500 of A's float that rode along in the Rs 1,000 diesel payment
    assert r.clean("ACC_D") == paise("819500.00")


def test_with_tool_held_amount_is_section_3_number():
    r = run(case, "LIBR", AS_OF)
    held = sum(r.tainted(a, VICTIM) for a in ("ACC_A", "ACC_B", "ACC_C"))
    assert held == paise(case.expected_compare["victim_money_held"]["tool"])


def test_cash_out_after_holds_is_excluded_and_after_baseline_it_is_gone():
    at_hold = run(case, "LIBR", AS_OF)
    at_baseline_c = run(case, "LIBR", "2026-10-18 13:05:00")
    assert at_hold.tainted("ACC_C") == paise(80000)
    assert at_baseline_c.tainted("ACC_C") == 0
    assert at_baseline_c.tainted("EXIT:ATM:BX-ATM-0117") == paise(80000)


def test_fifo_equals_libr_on_demo_case():
    # DECISIONS D-04: the mixed credit into B keeps A's order (A's own Rs 500 first).
    libr, fifo = run(case, "LIBR", AS_OF), run(case, "FIFO", AS_OF)
    for acc in ("ACC_A", "ACC_B", "ACC_C", "ACC_D", "EXIT:ATM:BY-ATM-0042"):
        assert fifo.tainted(acc) == libr.tainted(acc), acc


def test_pro_rata_hand_values_for_a():
    # A->B 1,20,000 of 2,00,500 takes 299.25 clean; A->C takes 199.50 clean; A ends with 498.75 victim, 1.25 clean.
    r = run(case, "PRO_RATA", AS_OF)
    assert r.tainted("ACC_A") == paise("498.75")
    assert r.clean("ACC_A") == paise("1.25")
    assert r.victim_total(VICTIM) == paise(200000)