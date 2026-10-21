"""The Exasol attribution UDF body, run without Exasol through a fake ctx, must agree with the oracle exactly.

This is the CI half of AT-04/05/06: if this passes, any difference seen in Exasol comes from the SQL that feeds
the UDF, not from the attribution logic.
"""
import importlib.util
from pathlib import Path

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from pipeline.cases import DEMO_CASE, EXTRA_CASES, LOOP_CASE, load_case, paise
from tests.helpers import oracle_txns
from tests.oracle import RULES, Txn, replay

UDF_PATH = Path(__file__).resolve().parents[1] / "sql" / "udf" / "attribute_case.py"
spec = importlib.util.spec_from_file_location("attribute_case", UDF_PATH)
udf = importlib.util.module_from_spec(spec)
spec.loader.exec_module(udf)


class FakeCtx:
    """Mimics Exasol's SET-script context: one row visible at a time, next(), emit()."""

    COLUMNS = ("trace_rule", "row_kind", "ts", "txn_id", "from_account", "to_account", "amount_paise", "victim_tag")

    def __init__(self, rows):
        self._rows, self._i, self.emitted = rows, 0, []

    def __getattr__(self, name):
        if name in self.COLUMNS:
            return self._rows[self._i][self.COLUMNS.index(name)]
        raise AttributeError(name)

    def next(self):
        self._i += 1
        return self._i < len(self._rows)

    def emit(self, *values):
        self.emitted.append(values)


def udf_rows(rule, txns, opening):
    rows = [(rule, "OPEN", None, None, acc, None, p, None) for acc, p in opening.items()]
    rows += [(rule, "TXN", t.ts, t.txn_id, t.from_account, t.to_account, t.amount_paise, t.victim_tag)
             for t in txns]
    return rows


def run_udf(rule, txns, opening):
    rows = udf_rows(rule, txns, opening)
    if not rows:                      # Exasol never calls a SET script with an empty group
        return {}, {}, {}
    ctx = FakeCtx(rows)
    udf.run(ctx)
    holds, exits, moves = {}, {}, {}
    for r, kind, ref, comp, p in ctx.emitted:
        assert r == rule
        if kind == "HOLD" and p:
            holds.setdefault(ref, {})[comp] = p
        elif kind == "EXIT":
            exits.setdefault(ref, {})[comp] = p
        elif kind == "MOVE":
            moves[(ref, comp)] = moves.get((ref, comp), 0) + p
    return holds, exits, moves


def assert_same(rule, txns, opening):
    o = replay(txns, rule, opening)
    holds, exits, moves = run_udf(rule, txns, opening)
    assert holds == {a: c for a, c in o.holdings.items() if c}, rule
    assert exits == {e: c for e, c in o.exits.items() if c}, rule
    o_moves = {}
    for txn_id, comp, p in o.moves:
        if comp != "clean":
            o_moves[(txn_id, comp)] = o_moves.get((txn_id, comp), 0) + p
    assert moves == o_moves, rule


@pytest.mark.parametrize("rule", RULES)
@pytest.mark.parametrize("path", [DEMO_CASE, EXTRA_CASES, LOOP_CASE], ids=["demo", "extra", "loop"])
def test_udf_matches_oracle_on_case_files(rule, path):
    case = load_case(path)
    assert_same(rule, oracle_txns(case), case.opening_paise())


def test_udf_gives_the_hand_checked_libr_numbers():
    case = load_case(DEMO_CASE)
    txns = [t for t in oracle_txns(case) if t.ts <= case.tool_as_of]
    holds, exits, _ = run_udf("LIBR", txns, case.opening_paise())
    for acc in ("ACC_A", "ACC_B", "ACC_C", "ACC_D"):
        assert holds[acc].get("V_LAKSHMI", 0) == paise(case.expected_libr[acc]), acc
    assert exits["EXIT:ATM:BX-ATM-0042"]["V_LAKSHMI"] == paise(50000)


def test_udf_rejects_unknown_rule():
    with pytest.raises(ValueError):
        run_udf("LIFO", [Txn("T1", "2026-10-18 10:00:00", "X", "Y", 100)], {"X": 100})


ACCOUNTS = ["A", "B", "C", "D"]
step = st.one_of(
    st.tuples(st.just("root"), st.sampled_from(["V1", "V2"]), st.sampled_from(ACCOUNTS), st.integers(1, 10**6)),
    st.tuples(st.just("move"), st.sampled_from(ACCOUNTS), st.sampled_from(ACCOUNTS + ["EXIT:ATM:1"]),
              st.integers(1, 10**6)),
)


@settings(max_examples=300, deadline=None)
@given(steps=st.lists(step, min_size=1, max_size=30),
       opening=st.dictionaries(st.sampled_from(ACCOUNTS), st.integers(0, 2 * 10**6)),
       rule=st.sampled_from(RULES))
def test_udf_matches_oracle_on_random_graphs(steps, opening, rule):
    txns = []
    for i, (kind, a, b, amount) in enumerate(steps):
        ts = f"2026-10-18 10:{i:02d}:00"
        if kind == "root":
            txns.append(Txn(f"T{i:03d}", ts, f"VIC_{a}", b, amount, a))
        elif a != b:
            txns.append(Txn(f"T{i:03d}", ts, a, b, amount))
    assert_same(rule, txns, opening)