"""The scale benchmark runs end to end on a few tickets (needs `make data`; skipped without AMLworld). Builds its own
schemas (PH_BENCH_1, PH_BENCH_2); never touches the demo banks.

    python -m pytest -m db tests/test_db_bench.py
"""
import pytest

from tests.dbtools import connect_or_skip

pytestmark = pytest.mark.db


def test_a_small_burst_is_worked_and_held():
    from pipeline import bench
    from pipeline.load_cases import amlworld_loaded

    c = connect_or_skip()
    loaded = amlworld_loaded(c)
    c.close()
    if not loaded:
        pytest.skip("AMLworld is not loaded (run `make data`)")
    r = bench.run(4, 2, quiet=True)
    s = bench.summary(r)
    assert s["tickets"] == 4 and s["failed"] == 0, s["errors"]
    assert s["worked"] >= 1 and s["with_holds"] >= 1
    assert s["steps"]["trace"]["median"] > 0