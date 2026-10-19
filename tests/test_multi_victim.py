"""AT-06 (oracle side): an account holding several victims' money splits pro rata (FR-07, SOP para 5(xiii))."""
import pytest

from pipeline.cases import EXTRA_CASES, load_case
from tests.helpers import run
from tests.oracle import Txn, replay, to_paise


def T(i, ts, frm, to, rupees, victim=None):
    return Txn(f"T{i:02d}", f"2026-10-18 {ts}:00", frm, to, to_paise(rupees), victim)


TWO_VICTIMS = [
    T(1, "10:00", "VIC1", "M", 30000, "V1"),
    T(2, "10:05", "VIC2", "M", 10000, "V2"),
    T(3, "10:10", "M", "EXIT:ATM:9", 20000),
]


@pytest.mark.parametrize("rule", ["LIBR", "PRO_RATA"])
def test_two_victims_split_three_to_one(rule):
    r = replay(TWO_VICTIMS, rule)
    assert r.tainted("EXIT:ATM:9", "V1") == to_paise(15000)
    assert r.tainted("EXIT:ATM:9", "V2") == to_paise(5000)
    assert r.tainted("M", "V1") == to_paise(15000)
    assert r.tainted("M", "V2") == to_paise(5000)


def test_fifo_is_chronological_not_pro_rata():
    # FIFO is a tracing rule in its own right: V1's money arrived first, so it leaves first.
    r = replay(TWO_VICTIMS, "FIFO")
    assert r.tainted("M", "V1") == to_paise(10000)
    assert r.tainted("M", "V2") == to_paise(10000)


def test_generated_mule_m01_holds_three_victims():
    extra = load_case(EXTRA_CASES)
    r = run(extra, "LIBR")
    victims_in_m01 = {v for v in r.holdings.get("ACC_M01", {}) if v != "clean"}
    credited = {t.victim_tag for t in extra.txns if t.to_account == "ACC_M01" and t.victim_tag}
    assert credited == {"V_X01", "V_X02", "V_X03"}
    # Whatever is left in M01 belongs only to the victims who paid into it.
    assert victims_in_m01 <= credited