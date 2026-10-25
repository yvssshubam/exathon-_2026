"""AT-05 (oracle side): every victim's money is accounted for, to the paisa, under every rule.

held in accounts + gone through exits == amount the victim reported, for all 23 victims,
plus property tests on random small graphs (Hypothesis).
"""
import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from pipeline.cases import DEMO_CASE, EXTRA_CASES, LOOP_CASE, load_case, paise
from tests.helpers import oracle_txns, run
from tests.oracle import CLEAN, RULES, Txn, replay

demo, extra, loop = load_case(DEMO_CASE), load_case(EXTRA_CASES), load_case(LOOP_CASE)


def test_there_are_23_victims():
    # the demo night's three (Lakshmi, Meena, Ravi; the investigating officer's notice repeats Lakshmi's) and 20 more
    victims = {c["victim_tag"] for c in demo.complaints + extra.complaints}
    assert len(victims) == 23


@pytest.mark.parametrize("rule", RULES)
@pytest.mark.parametrize("which", ["demo", "extra", "loop"])
def test_every_victim_is_conserved(rule, which):
    case = {"demo": demo, "extra": extra, "loop": loop}[which]
    r = run(case, rule)
    for c in case.complaints:
        assert r.victim_total(c["victim_tag"]) == paise(c["amount_inr"]), (rule, c["victim_tag"])


@pytest.mark.parametrize("rule", RULES)
def test_all_cases_together_are_conserved(rule):
    # Replaying everything at once must give the same per-victim totals as replaying each case alone.
    txns = oracle_txns(demo) + oracle_txns(extra) + oracle_txns(loop)
    opening = demo.opening_paise() | extra.opening_paise() | loop.opening_paise()
    r = replay(txns, rule, opening)
    for c in demo.complaints + extra.complaints + loop.complaints:
        assert r.victim_total(c["victim_tag"]) == paise(c["amount_inr"])


@pytest.mark.parametrize("rule", RULES)
def test_generated_cases_need_no_shortfall(rule):
    assert run(extra, rule).shortfalls == {}


def test_loop_case_libr_by_hand():
    r = run(loop, "LIBR")
    for acc, rupees in loop.expected_libr.items():
        if acc != "total":
            assert r.tainted(acc) == paise(rupees), acc


# ---- property tests -------------------------------------------------------------------------------

ACCOUNTS = ["A", "B", "C", "D", "E"]
VICTIMS = ["V1", "V2", "V3"]

step = st.one_of(
    st.tuples(st.just("root"), st.sampled_from(VICTIMS), st.sampled_from(ACCOUNTS),
              st.integers(1, 5_000_000)),
    st.tuples(st.just("move"), st.sampled_from(ACCOUNTS),
              st.sampled_from(ACCOUNTS + ["EXIT:ATM:1", "EXIT:CRYPTO:1"]), st.integers(1, 5_000_000)),
)


def build(steps):
    txns = []
    for i, (kind, a, b, amount) in enumerate(steps):
        ts = f"2026-10-18 {10 + i // 60:02d}:{i % 60:02d}:00"
        if kind == "root":
            txns.append(Txn(f"T{i:03d}", ts, f"VIC_{a}", b, amount, a))
        elif a != b:
            txns.append(Txn(f"T{i:03d}", ts, a, b, amount))
    return txns


@settings(max_examples=300, deadline=None)
@given(
    steps=st.lists(step, min_size=1, max_size=40),
    opening=st.dictionaries(st.sampled_from(ACCOUNTS), st.integers(0, 10_000_000)),
    rule=st.sampled_from(RULES),
)
def test_random_graphs_conserve_money(steps, opening, rule):
    txns = build(steps)
    r = replay(txns, rule, opening)

    for victim, reported in r.reported.items():
        assert r.victim_total(victim) == reported

    for comp in list(r.holdings.values()) + list(r.exits.values()):
        assert all(p > 0 for p in comp.values())

    moved: dict[str, int] = {}
    for txn_id, _, p in r.moves:
        moved[txn_id] = moved.get(txn_id, 0) + p
    assert moved == {t.txn_id: t.amount_paise for t in txns}

    # Money in each account = opening + shortfall top-ups + inflows - outflows.
    touched = {t.from_account for t in txns} | {t.to_account for t in txns}
    for acc in touched & set(ACCOUNTS):
        inflow = sum(t.amount_paise for t in txns if t.to_account == acc)
        outflow = sum(t.amount_paise for t in txns if t.from_account == acc)
        expected = opening.get(acc, 0) + r.shortfalls.get(acc, 0) + inflow - outflow
        assert sum(r.holdings.get(acc, {}).values()) == expected


@settings(max_examples=200, deadline=None)
@given(steps=st.lists(step, min_size=1, max_size=30),
       opening=st.dictionaries(st.sampled_from(ACCOUNTS), st.integers(0, 10_000_000)))
def test_libr_never_holds_more_victim_money_than_pro_rata_takes_in_total(steps, opening):
    # Across the whole graph, the rules only move victim money around; totals per victim are identical.
    txns = build(steps)
    libr, pro = replay(txns, "LIBR", opening), replay(txns, "PRO_RATA", opening)
    for v in libr.reported:
        assert libr.victim_total(v) == pro.victim_total(v)
    assert CLEAN not in libr.reported