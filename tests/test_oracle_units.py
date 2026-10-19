"""The oracle checked against tiny cases worked out by hand. If these fail, nothing else can be trusted."""
import pytest

from tests.oracle import CLEAN, RULES, Txn, allocate, replay, to_paise


def T(i, ts, frm, to, rupees, victim=None):
    return Txn(f"T{i:02d}", f"2026-10-18 {ts}:00", frm, to, to_paise(rupees), victim)


class TestAllocate:
    def test_sums_exactly(self):
        out = allocate(100, {"a": 1, "b": 1, "c": 1})
        assert sum(out.values()) == 100
        assert sorted(out.values()) == [33, 33, 34]

    def test_tie_goes_to_clean_first(self):
        assert allocate(1, {"V1": 1, CLEAN: 1}) == {CLEAN: 1}

    def test_largest_remainder_wins(self):
        # 100000 paise over 50000:11950000 -> clean 416.67, V 99583.33 -> clean gets the spare paisa
        assert allocate(100000, {CLEAN: 50000, "V": 11950000}) == {CLEAN: 417, "V": 99583}

    def test_empty(self):
        assert allocate(0, {"a": 5}) == {}
        assert allocate(5, {"a": 0}) == {}


class TestRulesByHand:
    """Account X opens with Rs 100 of its own, gets Rs 300 of V1's money, then pays out Rs 200."""

    txns = [T(1, "10:00", "VIC", "X", 300, "V1"), T(2, "10:05", "X", "Y", 200)]
    opening = {"X": to_paise(100)}

    def test_libr_spends_own_money_first(self):
        r = replay(self.txns, "LIBR", self.opening)
        assert r.tainted("X") == to_paise(200)     # 100 own + 100 victim left; victim keeps 200
        assert r.tainted("Y") == to_paise(100)
        assert r.clean("X") == 0

    def test_fifo_spends_oldest_first(self):
        r = replay(self.txns, "FIFO", self.opening)
        assert r.tainted("X") == to_paise(200)     # own 100 is oldest, then 100 of V1
        assert r.tainted("Y") == to_paise(100)

    def test_pro_rata_spends_in_proportion(self):
        r = replay(self.txns, "PRO_RATA", self.opening)
        assert r.tainted("Y") == to_paise(150)     # 200 * 300/400
        assert r.tainted("X") == to_paise(150)
        assert r.clean("X") == to_paise(50)

    def test_fifo_differs_from_libr_when_clean_money_arrives_later(self):
        txns = [T(1, "10:00", "VIC", "X", 300, "V1"), T(2, "10:01", "S", "X", 100), T(3, "10:05", "X", "Y", 200)]
        libr = replay(txns, "LIBR", {"S": to_paise(100)})
        fifo = replay(txns, "FIFO", {"S": to_paise(100)})
        assert libr.tainted("Y") == to_paise(100)  # the later clean 100 still goes out first
        assert fifo.tainted("Y") == to_paise(200)  # the victim's money is older

    def test_libr_never_refills_taint_from_clean_deposits(self):
        # Lowest intermediate balance: X falls to Rs 50 of victim money; a later clean deposit doesn't raise it.
        txns = [T(1, "10:00", "VIC", "X", 300, "V1"), T(2, "10:05", "X", "Y", 250),
                T(3, "10:10", "S", "X", 1000)]
        r = replay(txns, "LIBR", {"S": to_paise(1000)})
        assert r.tainted("X") == to_paise(50)
        assert r.clean("X") == to_paise(1000)

    def test_exit_takes_victim_money_out(self):
        txns = [T(1, "10:00", "VIC", "X", 300, "V1"), T(2, "10:05", "X", "EXIT:ATM:1", 120)]
        r = replay(txns, "LIBR")
        assert r.tainted("EXIT:ATM:1") == to_paise(120)
        assert r.tainted("X") == to_paise(180)

    def test_shortfall_is_treated_as_old_clean_money(self):
        # X pays out more than the replay knows about: the gap is pre-window clean money (DECISIONS D-05).
        txns = [T(1, "10:00", "VIC", "X", 100, "V1"), T(2, "10:05", "X", "Y", 150)]
        for rule in RULES:
            r = replay(txns, rule)
            assert r.shortfalls == {"X": to_paise(50)}
            assert r.victim_total("V1") == to_paise(100)

    def test_as_of_cuts_later_transactions(self):
        txns = [T(1, "10:00", "VIC", "X", 300, "V1"), T(2, "12:30", "X", "EXIT:ATM:1", 300)]
        assert replay(txns, "LIBR", as_of="2026-10-18 11:13:00").tainted("X") == to_paise(300)
        assert replay(txns, "LIBR").tainted("X") == 0

    def test_rejects_unknown_rule(self):
        with pytest.raises(ValueError):
            replay([], "LIFO")

    def test_to_paise_refuses_fractions_of_a_paisa(self):
        assert to_paise("1.25") == 125
        with pytest.raises(ValueError):
            to_paise("1.255")