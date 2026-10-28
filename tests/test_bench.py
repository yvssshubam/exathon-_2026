"""The scale benchmark's arithmetic and report, no database (pipeline/bench.py)."""
from pipeline import bench


def fake_run():
    results = []
    for i in range(10):
        seconds = {s: 0.1 * (i + 1) for s in bench.STEPS}
        results.append({"case_id": f"BENCH_{i:05d}", "kind": "pattern", "seconds": seconds, "held_inr": 1000.0,
                        "holds": 1 if i % 2 else 0, "error": None, "match": "EXACT", "checks_passed": True,
                        "done_after": 400.0 * (i + 1)})
    results.append({"case_id": "BENCH_X", "kind": "payment", "seconds": {"intake": 0.2}, "held_inr": 0.0, "holds": 0,
                    "error": None, "match": "NO_MATCH", "done_after": 10.0})
    results.append({"case_id": "BENCH_Y", "kind": "payment", "seconds": {}, "held_inr": 0.0, "holds": 0,
                    "error": "FAILED 040_trace.sql statement 1", "done_after": 20.0})
    return {"tickets": 12, "workers": 3, "rows": 5_000_000, "wall_seconds": 4000.0, "results": results,
            "exasol": "8.x", "machine": "test machine", "ran_at": "2026-10-28 19:00:00"}


def test_percentiles():
    assert bench.pct([], 50) == 0.0
    assert bench.pct([5.0], 95) == 5.0
    v = [float(x) for x in range(1, 101)]
    assert bench.pct(v, 50) == 50.0 and bench.pct(v, 95) == 95.0 and bench.pct(v, 100) == 100.0


def test_summary_counts_the_golden_hour_and_each_outcome():
    s = bench.summary(fake_run())
    assert s["tickets"] == 12 and s["worked"] == 10 and s["with_holds"] == 5
    assert s["not_exact"] == 1 and s["failed"] == 1 and s["checks_failed"] == 0
    assert s["within_hour"] == 2 + 9                      # done at 400 s .. 3600 s, plus the two quick ones
    assert s["time_to_hold"]["max"] == 4000.0
    assert s["steps"]["trace"]["max"] == 1.0
    assert s["per_minute"] == 60 * 12 / 4000.0


def test_report_says_what_it_measured():
    r = fake_run()
    text = bench.report(r, bench.summary(r))
    assert "11 of 12 tickets were worked within an hour" in text
    assert "| trace |" in text and "5,000,000 AMLworld transactions" in text and "test machine" in text
    assert "FAILED 040_trace.sql" in text