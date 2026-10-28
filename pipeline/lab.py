"""AMLworld tests (Module 9, idea 15): does the money engine hold up on IBM's labelled laundering patterns?

Two tests, and one number, measured on what matters here, never a mule "hit rate" (that invites the MuleHunter
comparison and measures the wrong thing):

  1. Trace tests (always). Every labelled laundering attempt goes through the same trace and attribution SQL the agent
     runs (sql/steps/040, 050), its first payment tagged as one victim's money. Pass when the trace finishes (cycles
     included) and the victim's money is conserved to the paisa under LIBR, FIFO and pro rata, through fan-out,
     fan-in, scatter-gather and the rest.
  2. The data check, then the scoring test (only if the data allows). Does AMLworld have clean accounts that received
     laundered money? If not, say so plainly: the weights are hand-set. If it does, run the evidence step and the rules
     engine on the accounts the traces actually reach, and report the two mistakes that hurt someone:
        wrongly widened   a full-freeze proposal on a clean account     (hurts the honest holder)
        wrongly narrowed  a narrow proposal on a laundering account     (hurts the victim)
     on held-out patterns (odd pattern IDs; nothing is tuned: the weights in config/scoring.yaml are hand-set), with
     the caveat: synthetic benchmark; laundering labels, not cyber-fraud mule labels.
  3. The share of touched accounts that need one of the officer's four decisions.

Writes PH_LAB.LAB_RESULT and docs/AMLWORLD_TESTS.md. Needs `make data` first.

    python -m pipeline.lab                  # up to 25 patterns of each type
    python -m pipeline.lab --all            # every pattern (a few minutes)
"""
from __future__ import annotations

import argparse
import sys
import time
from collections import defaultdict
from datetime import datetime, timedelta
from decimal import Decimal
from pathlib import Path

from pipeline.db import ROOT

LAB = "PH_LAB"
REPORT = ROOT / "docs" / "AMLWORLD_TESTS.md"
CAVEAT = "Synthetic benchmark; laundering labels, not cyber-fraud mule labels."
FMT = "%Y-%m-%d %H:%M:%S"
CLEAN_RECEIVERS_NEEDED = 30


def build(conn, schema: str = LAB, learned: bool = False) -> None:
    """A bank schema (PH_LAB by default) whose transactions and accounts are AMLworld's. learned=True also loads the
    learned signal (the benchmark measures the scoring as the banks run it)."""
    from pipeline.run_sql import BANK_SQL, split_statements, sql_files

    if not schema.startswith(("PH_LAB", "PH_BENCH_")):
        raise ValueError(f"{schema} is not a lab schema: build only ever replaces PH_LAB or PH_BENCH_*")
    # A rebuild starts clean: the views below replace tables of the same names, which the bank SQL cannot replace.
    conn.execute(f"DROP SCHEMA IF EXISTS {schema} CASCADE")
    conn.execute(f"CREATE SCHEMA {schema}")
    conn.execute(f"OPEN SCHEMA {schema}")
    for path in sql_files(BANK_SQL) + [ROOT / "sql" / "lab" / "010_lab.sql"]:
        for stmt in split_statements(path.read_text(encoding="utf-8")):
            conn.execute(stmt)
    from pipeline.load_cases import world_from_files

    world = world_from_files()
    tables = ["SCORING_CONFIG", "PROFILE_BAND"] + (["LEARNED_COEF"] if learned else [])
    if not learned:
        # The hand-set signals only: the learned one is trained on AMLworld itself, so it stays out of this test (D-51).
        world["SCORING_CONFIG"] = [r for r in world["SCORING_CONFIG"] if r[0] != "learned_pattern"]
    for table in tables:
        conn.execute(f"TRUNCATE TABLE {schema}.{table}")
        conn.import_from_iterable(world[table], (schema, table))


def patterns(conn, per_type: int | None) -> list[tuple]:
    """(pattern_id, type, root txn, its UTR, amount, first ts, last ts) for each labelled attempt."""
    rows = conn.execute("SELECT p.pattern_id, MIN(p.pattern_type), MIN(t.ts), MAX(t.ts) FROM PH_AML.PATTERN_TXN p "
                        "JOIN PH_AML.TXN t ON t.txn_id = p.txn_id GROUP BY p.pattern_id ORDER BY 1").fetchall()
    out, seen = [], defaultdict(int)
    for pid, kind, first, last in rows:
        if per_type and seen[kind] >= per_type:
            continue
        root = conn.execute("SELECT t.txn_id, t.utr, t.amount_inr, t.ts FROM PH_AML.PATTERN_TXN p "
                            "JOIN PH_AML.TXN t ON t.txn_id = p.txn_id WHERE p.pattern_id = {p} "
                            "ORDER BY t.ts, t.txn_id LIMIT 1", {"p": int(pid)}).fetchone()
        seen[kind] += 1
        out.append((int(pid), kind, root[0], root[1], Decimal(str(root[2])), str(first)[:19], str(last)[:19]))
    return out


def result(conn, test: str, pid, kind, passed, detail) -> None:
    conn.execute(f"INSERT INTO {LAB}.LAB_RESULT (test_name, pattern_id, pattern_type, passed, detail, stmt_id) "
                 "VALUES ({t}, {p}, {k}, {ok}, {d}, CURRENT_STATEMENT)",
                 {"t": test, "p": pid, "k": kind, "ok": None if passed is None else passed, "d": str(detail)[:2000]})


def run_trace_tests(conn, pats: list[tuple]) -> dict:
    """Test 1: every pattern through trace + attribution; terminates, conserves to the paisa under every rule."""
    from pipeline import steps

    summary: dict[str, list[bool]] = defaultdict(list)
    for pid, kind, txn, utr, amount, first, last in pats:
        case = f"LAB_P{pid}"
        as_of = (datetime.strptime(last, FMT) + timedelta(minutes=1)).strftime(FMT)
        conn.execute("DELETE FROM COMPLAINT WHERE case_id = {c}", {"c": case})
        conn.execute("DELETE FROM INTAKE_CHECK WHERE case_id = {c}", {"c": case})
        conn.execute("INSERT INTO COMPLAINT (case_id, ack_no, complaint_source, ticket_kind, victim_tag, utr, "
                     "amount_inr, "
                     "txn_at, reported_at, lane, notice_legal_basis, notice_has_amount) VALUES ({c}, '29999999999999', "
                     "'NCRP', 'POLICE', {v}, {u}, {a!d}, TO_TIMESTAMP({f}, 'YYYY-MM-DD HH24:MI:SS'), "
                     "TO_TIMESTAMP({f}, 'YYYY-MM-DD HH24:MI:SS'), 'A', 'lab', TRUE)",
                     {"c": case, "v": f"P{pid}", "u": utr, "a": str(amount), "f": first})
        conn.execute("INSERT INTO INTAKE_CHECK (case_id, match_status, matched_txn_id, related) "
                     "VALUES ({c}, 'EXACT', {t}, 'NEW')", {"c": case, "t": txn})
        started = time.perf_counter()
        try:
            steps.trace(conn, case, "LAB", as_of)
            steps.attribute(conn, case, "LAB", as_of)
        except steps.StepFailed as exc:
            result(conn, "trace", pid, kind, False, f"step failed: {exc}")
            summary[kind].append(False)
            continue
        seconds = time.perf_counter() - started
        edges, cycles = conn.execute("SELECT COUNT(*), SUM(CASE WHEN is_cycle THEN 1 ELSE 0 END) FROM TRACE_EDGE "
                                     "WHERE case_id = {c}", {"c": case}).fetchone()
        totals = dict(conn.execute("SELECT trace_rule, SUM(victim_amount_inr) FROM ATTRIBUTION WHERE case_id = {c} "
                                   "GROUP BY trace_rule", {"c": case}).fetchall())
        conserved = {r: Decimal(str(totals.get(r) or 0)) == amount for r in ("LIBR", "FIFO", "PRO_RATA")}
        ok = all(conserved.values()) and int(edges or 0) > 0
        result(conn, "trace", pid, kind, ok, f"{int(edges or 0)} edges, {int(cycles or 0)} loops, {seconds:.2f}s; "
                                             f"conserved {conserved}; tagged {amount}")
        summary[kind].append(ok)
    return {k: (sum(v), len(v)) for k, v in summary.items()}


def data_check(conn) -> tuple[bool, int]:
    """Does AMLworld have clean accounts that received laundered money (they never send a laundering payment)?"""
    n = conn.execute("""
        SELECT COUNT(*) FROM (
            SELECT DISTINCT t.to_account FROM PH_AML.TXN t WHERE t.is_laundering
            MINUS
            SELECT DISTINCT s.from_account FROM PH_AML.TXN s WHERE s.is_laundering)""").fetchval()
    return int(n or 0) >= CLEAN_RECEIVERS_NEEDED, int(n or 0)


def scoring_test(conn, pats: list[tuple]) -> dict:
    """Test 2 on the accounts the traces reached: wrongly widened, wrongly narrowed, held-out patterns only."""
    from pipeline import steps

    laundering = {a for (a,) in conn.execute("SELECT DISTINCT from_account FROM PH_AML.TXN WHERE is_laundering")
                  .fetchall()}
    counts = {"clean": 0, "widened": 0, "laundering": 0, "narrowed": 0, "touched": 0, "officer": 0}
    for pid, _kind, _txn, _utr, _amount, _first, last in pats:
        if pid % 2 == 0:
            continue                        # held-out: odd pattern IDs only
        case = f"LAB_P{pid}"
        as_of = (datetime.strptime(last, FMT) + timedelta(minutes=1)).strftime(FMT)
        try:
            steps.score(conn, case, "LAB", as_of)
            steps.actions(conn, case, "LAB")
        except steps.StepFailed:
            continue
        accounts = {a: set() for (a,) in conn.execute("SELECT account_id FROM SCORE_TARGET WHERE case_id = {c}",
                                                       {"c": case}).fetchall()}
        for acc, code in conn.execute("SELECT account_id, action_code FROM CASE_ACTION WHERE case_id = {c} "
                                      "AND act_mode = 'OFFICER'", {"c": case}).fetchall():
            accounts.setdefault(acc, set()).add(code)
        for acc, codes in accounts.items():
            counts["touched"] += 1
            counts["officer"] += 1 if codes else 0
            if acc in laundering:
                counts["laundering"] += 1
                counts["narrowed"] += 1 if "PROPOSE_NARROW" in codes else 0
            else:
                counts["clean"] += 1
                counts["widened"] += 1 if "PROPOSE_FULL_FREEZE" in codes else 0
    return counts


def report(trace: dict, check: tuple[bool, int], scoring: dict | None, n: int) -> str:
    lines = ["# AMLworld tests (generated by `python -m pipeline.lab`)", "", f"_{CAVEAT}_", "",
             f"Patterns run: {n}. The same trace and attribution SQL the agent runs; the first payment of each "
             "laundering attempt tagged as one victim's money.", "", "## Test 1: trace and attribution", "",
             "| Pattern type | Passed | Run |", "| --- | ---: | ---: |"]
    for kind, (ok, total) in sorted(trace.items()):
        lines.append(f"| {kind} | {ok} | {total} |")
    lines += ["", "Pass = the trace finished (loops included) and the tagged money is conserved to the paisa "
              "under LIBR, "
              "FIFO and pro rata.", "", "## Data check", "",
              f"Clean accounts that received laundered money (never send a laundering payment): {check[1]}. "
              + ("Enough to run the scoring test." if check[0] else
                 "Not enough: the scoring test is skipped. **The scoring weights are hand-set.**")]
    if scoring:
        def share(a, b):
            return f"{a} of {b} ({(100 * a / b):.1f}%)" if b else "n/a"
        lines += ["", "## Test 2: the two mistakes (held-out patterns: odd IDs; nothing tuned)", "",
                  "| Mistake | Who it hurts | Result |", "| --- | --- | --- |",
                  f"| Wrongly widened: full-freeze proposal on a clean account | The honest holder | "
                  f"{share(scoring['widened'], scoring['clean'])} |",
                  f"| Wrongly narrowed: narrow proposal on a laundering account | The victim | "
                  f"{share(scoring['narrowed'], scoring['laundering'])} |", "",
                  "## Officer decisions per touched account", "",
                  f"{share(scoring['officer'], scoring['touched'])} of the accounts the traces touched needed one of "
                  "the officer's four decisions; the rest were handled automatically."]
    lines += ["", f"_{CAVEAT}_", ""]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--all", action="store_true", help="every pattern (default: 25 of each type)")
    ap.add_argument("--per-type", type=int, default=25)
    args = ap.parse_args(argv)
    from pipeline.db import connect

    conn = connect()
    try:
        if not conn.execute("SELECT COUNT(*) FROM EXA_ALL_TABLES WHERE TABLE_SCHEMA = 'PH_AML' "
                            "AND TABLE_NAME = 'PATTERN_TXN'").fetchval():
            print("AMLworld is not loaded with its patterns file: run `make data` first.", file=sys.stderr)
            return 1
        build(conn)
        pats = patterns(conn, None if args.all else args.per_type)
        print(f"running {len(pats)} laundering patterns through trace and attribution in {LAB}")
        trace = run_trace_tests(conn, pats)
        for kind, (ok, total) in sorted(trace.items()):
            print(f"  {kind:16} {ok:4} of {total:4} passed")
        enough, n = data_check(conn)
        result(conn, "data_check", None, None, enough, f"{n} clean receivers of laundered money")
        print(f"data check: {n} clean accounts received laundered money -> "
              f"{'scoring test runs' if enough else 'skipped; the weights are hand-set'}")
        scoring = scoring_test(conn, pats) if enough else None
        if scoring:
            result(conn, "scoring", None, None, None, str(scoring))
            print(f"  scoring (held-out): {scoring}")
        REPORT.write_text(report(trace, (enough, n), scoring, len(pats)), encoding="utf-8")
        print(f"wrote {Path(REPORT).relative_to(ROOT)}")
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())