"""The scale benchmark: is a ticket still held inside the golden hour when a whole night of tickets lands at once?

Fires a burst of tickets at the full AMLworld table (about 5 million transactions, `make data`) and works each one
through the same steps the agent runs at a bank: intake (030), trace (040), attribution (050, the Python UDF),
account evidence and the learned signal (055, 060), the rules (070), the checker's checks (075), then the maker's
proposals and the checker's holds (081). Every ticket counts as arriving at the same moment, so a ticket's time to
hold is its wait in the queue plus its own work.

Where: one schema per worker (PH_BENCH_1, PH_BENCH_2, ...), each built like a bank's (the lab's way: the same
sql/bank files, with transactions and accounts pointed at PH_AML), the way each bank works its own tickets in its own
schema. The demo banks, their data and the night are never touched. The tickets are the first payment of each
labelled laundering attempt (the lab's patterns), then other AMLworld payments picked by a fixed hash, each reported
as one victim's money.

Writes docs/BENCHMARK.md: tickets, rows, time per step (median, 95th percentile, slowest), tickets per minute and how
many were held within the golden hour, with the machine it ran on. Numbers from a laptop, not a bank's servers.

    python -m pipeline.bench                         # 100 tickets, 3 workers
    python -m pipeline.bench --tickets 1000 --workers 3
"""
from __future__ import annotations

import argparse
import math
import os
import platform
import queue
import sys
import threading
import time
from datetime import datetime, timedelta
from decimal import Decimal

from pipeline.db import ROOT

REPORT = ROOT / "docs" / "BENCHMARK.md"
SCHEMA = "PH_BENCH_{n}"
BANK = "LAB"
FMT = "%Y-%m-%d %H:%M:%S"
GOLDEN_HOUR = 3600
STEPS = ["intake", "trace", "attribute", "score", "rules", "checks", "holds"]


# ---------------------------------------------------------------- the tickets

REPORT_AFTER = 30          # minutes from the payment to the complaint reaching the bank: inside the golden hour


def tickets(conn, n: int, report_after: int = REPORT_AFTER) -> list[dict]:
    """n reported payments: the first payment of each labelled laundering attempt, then other payments by a hash.
    Each reaches the bank report_after minutes after the payment, so the trace sees what had moved by then (later
    movement is the watch's job), as at a bank."""
    out: list[dict] = []
    has_patterns = conn.execute("SELECT COUNT(*) FROM EXA_ALL_TABLES WHERE TABLE_SCHEMA = 'PH_AML' "
                                "AND TABLE_NAME = 'PATTERN_TXN'").fetchval()
    if has_patterns:
        for _pid, txn, utr, amount, ts, to_acc in conn.execute("""
                SELECT x.pattern_id, x.txn_id, x.utr, x.amount_inr, x.ts, x.to_account
                FROM (SELECT p.pattern_id, t.txn_id, t.utr, t.amount_inr, t.ts, t.to_account,
                             ROW_NUMBER() OVER (PARTITION BY p.pattern_id ORDER BY t.ts, t.txn_id) AS rn
                      FROM PH_AML.PATTERN_TXN p JOIN PH_AML.TXN t ON t.txn_id = p.txn_id) x
                WHERE x.rn = 1 ORDER BY x.pattern_id LIMIT {n!d}""", {"n": str(n)}).fetchall():
            out.append(_ticket(len(out), "pattern", txn, utr, amount, ts, to_acc, _plus(ts, report_after)))
    if len(out) < n:
        for txn, utr, amount, ts, to_acc in conn.execute(
                "SELECT txn_id, utr, amount_inr, ts, to_account FROM PH_AML.TXN WHERE amount_inr > 0 "
                "ORDER BY HASH_MD5(txn_id) LIMIT {n!d}", {"n": str(n - len(out))}).fetchall():
            out.append(_ticket(len(out), "payment", txn, utr, amount, ts, to_acc, _plus(ts, report_after)))
    return out


def _plus(ts, minutes: int) -> str:
    return (datetime.strptime(str(ts)[:19], FMT) + timedelta(minutes=minutes)).strftime(FMT)


def _ticket(i: int, kind: str, txn, utr, amount, ts, to_acc, reported: str) -> dict:
    return {"case_id": f"BENCH_{i + 1:05d}", "kind": kind, "txn_id": txn, "utr": utr, "amount": Decimal(str(amount)),
            "txn_at": str(ts)[:19], "account": to_acc, "reported_at": reported, "victim": f"VB{i + 1:05d}"}


# ---------------------------------------------------------------- one ticket, step by step

def work(conn, t: dict) -> dict:
    """Run one ticket through the bank's steps. Returns the seconds per step and what was held."""
    from pipeline import steps
    from pipeline.recalc import propose

    case, at = t["case_id"], t["reported_at"]
    conn.execute("INSERT INTO COMPLAINT (case_id, ack_no, complaint_source, ticket_kind, victim_tag, utr, amount_inr, "
                 "txn_at, reported_at, beneficiary_account, lane, notice_legal_basis, notice_has_amount) VALUES "
                 "({c}, '29999999999999', 'NCRP', 'POLICE', {v}, {u}, {a!d}, "
                 "TO_TIMESTAMP({ta}, 'YYYY-MM-DD HH24:MI:SS'), TO_TIMESTAMP({r}, 'YYYY-MM-DD HH24:MI:SS'), {acc}, 'A', "
                 "'BNSS S.106 (benchmark)', TRUE)",
                 {"c": case, "v": t["victim"], "u": t["utr"], "a": str(t["amount"]), "ta": t["txn_at"], "r": at,
                  "acc": t["account"]})
    seconds: dict[str, float] = {}

    def timed(name, fn, *args, **kwargs):
        started = time.perf_counter()
        result = fn(*args, **kwargs)
        seconds[name] = seconds.get(name, 0.0) + time.perf_counter() - started
        return result

    out = {"case_id": case, "kind": t["kind"], "seconds": seconds, "held_inr": 0.0, "holds": 0, "error": None}
    try:
        match = timed("intake", steps.intake, conn, case, BANK, at).get("match_status")
        out["match"] = match
        if match != "EXACT":
            return out
        timed("trace", steps.trace, conn, case, BANK, as_of=at)
        timed("attribute", steps.attribute, conn, case, BANK, as_of=at)
        timed("score", steps.score, conn, case, BANK, as_of=at)
        timed("rules", steps.actions, conn, case, BANK)
        checks = timed("checks", steps.check, conn, case, BANK)
        out["checks_passed"] = all(c["check_passed"] for c in checks)
        started = time.perf_counter()
        for a in steps.case_actions(conn, case, BANK):
            if a["act_mode"] != "AUTOMATIC" or a["action_code"] not in ("MANDATORY_HOLD", "HOLD_MARKER"):
                continue
            pid = propose(conn, case, a["account_id"], a["action_code"], "AUTOMATIC", a["amount_inr"], "pipeline",
                          at, {"benchmark": True})
            steps.execute(conn, "081_hold.sql", case, BANK, at, a["account_id"], pid)
        seconds["holds"] = time.perf_counter() - started
        held = conn.execute("SELECT COUNT(*), COALESCE(SUM(amount_inr), 0) FROM HOLD_LEDGER WHERE case_id = {c} "
                            "AND hold_kind = 'LIEN'", {"c": case}).fetchone()
        out["holds"], out["held_inr"] = int(held[0]), float(held[1])
    except Exception as exc:                      # noqa: BLE001 - one ticket failing must not stop the run
        out["error"] = str(exc).splitlines()[0][:300]
    return out


# ---------------------------------------------------------------- the burst

def run(n: int, workers: int, quiet: bool = False, report_after: int = REPORT_AFTER) -> dict:
    """Build the schemas, fire n tickets at once, work them with `workers` sessions in parallel."""
    from pipeline import lab
    from pipeline.db import connect

    say(quiet, "connecting to Exasol")
    admin = connect()
    try:
        if not admin.execute("SELECT COUNT(*) FROM EXA_ALL_TABLES WHERE TABLE_SCHEMA = 'PH_AML' "
                             "AND TABLE_NAME = 'TXN'").fetchval():
            raise SystemExit("AMLworld is not loaded: run `make data` first.")
        rows = int(admin.execute("SELECT COUNT(*) FROM PH_AML.TXN").fetchval())
        version = admin.execute("SELECT PARAM_VALUE FROM EXA_METADATA "
                                "WHERE PARAM_NAME = 'databaseProductVersion'").fetchval()
        schemas = [SCHEMA.format(n=k + 1) for k in range(workers)]
        for s in schemas:
            began = time.perf_counter()
            say(quiet, f"building {s} (a bank schema over AMLworld)")
            lab.build(admin, s, learned=True)
            say(quiet, f"  built in {time.perf_counter() - began:.0f}s")
        began = time.perf_counter()
        say(quiet, f"picking {n} tickets from {rows:,} AMLworld transactions")
        work_list = tickets(admin, n, report_after)
        kinds = sum(1 for t in work_list if t["kind"] == "pattern")
        say(quiet, f"  {len(work_list)} tickets ({kinds} laundering attempts, {len(work_list) - kinds} other "
                   f"payments) in {time.perf_counter() - began:.0f}s; all arrive now, {workers} sessions work them")
    finally:
        admin.close()
    todo: queue.Queue = queue.Queue()
    for t in work_list:
        todo.put(t)
    results: list[dict] = []
    lock = threading.Lock()
    started = time.perf_counter()

    conns: list = []
    stop = threading.Event()

    def worker(schema: str) -> None:
        conn = connect()
        with lock:
            conns.append(conn)
        conn.execute(f"OPEN SCHEMA {schema}")
        try:
            while not stop.is_set():
                try:
                    t = todo.get_nowait()
                except queue.Empty:
                    return
                r = work(conn, t)
                r["done_after"] = time.perf_counter() - started
                with lock:
                    results.append(r)
                    if len(work_list) <= 50 or len(results) % 10 == 0:
                        say(quiet, f"  {len(results)} of {len(work_list)} done, {r['done_after']:.0f}s since the "
                                   f"burst. {r['case_id']} in {schema}: {outcome(r)}")
        finally:
            conn.close()

    threads = [threading.Thread(target=worker, args=(s,), daemon=True) for s in schemas]
    for th in threads:
        th.start()
    try:
        while any(th.is_alive() for th in threads):
            for th in threads:
                th.join(0.5)
    except KeyboardInterrupt:
        # Ctrl+C: stop taking tickets and abort what Exasol is running, so no query is left behind on the server
        stop.set()
        for c in conns:
            try:
                c.abort_query()
            except Exception:                     # noqa: BLE001 - the session may already be gone
                pass
        print("stopped: the running queries were aborted", flush=True)
        raise SystemExit(130) from None
    wall = time.perf_counter() - started
    return {"tickets": len(work_list), "workers": workers, "rows": rows, "wall_seconds": wall, "results": results,
            "report_after": report_after,
            "exasol": version, "machine": machine(), "ran_at": datetime.now().strftime(FMT)}


def say(quiet: bool, text: str) -> None:
    if not quiet:
        print(text, flush=True)


def outcome(r: dict) -> str:
    """One ticket, in a few words: what happened and its slowest step."""
    took = sum(r["seconds"].values())
    slowest = max(r["seconds"].items(), key=lambda kv: kv[1], default=("-", 0.0))
    if r["error"]:
        what = f"stopped: {r['error'][:120]}"
    elif r.get("match") != "EXACT":
        what = f"no exact match ({(r.get('match') or 'none').lower()}), nothing held"
    elif r["holds"]:
        what = f"held Rs {r['held_inr']:,.0f} on {r['holds']} account{'s' if r['holds'] != 1 else ''}"
    else:
        what = "worked, nothing to hold" + ("" if r.get("checks_passed", True) else " (a check failed)")
    return f"{what}; {took:.1f}s of work, slowest step {slowest[0]} {slowest[1]:.1f}s"


def machine() -> str:
    return (f"{platform.system()} {platform.release()}, {platform.machine()}, {os.cpu_count()} logical CPUs, "
            f"Python {platform.python_version()}")


# ---------------------------------------------------------------- the numbers

def pct(values: list[float], p: float) -> float:
    """The p-th percentile (nearest rank). Empty: 0."""
    if not values:
        return 0.0
    v = sorted(values)
    k = max(0, min(len(v) - 1, math.ceil(p / 100 * len(v)) - 1))
    return v[k]


def summary(run_: dict) -> dict:
    res = run_["results"]
    worked = [r for r in res if not r["error"] and r.get("match") == "EXACT"]
    held = [r for r in worked if r["holds"]]
    done = [r["done_after"] for r in res]
    total = [sum(r["seconds"].values()) for r in worked]
    return {
        "tickets": run_["tickets"], "worked": len(worked), "with_holds": len(held),
        "not_exact": sum(1 for r in res if not r["error"] and r.get("match") != "EXACT"),
        "failed": sum(1 for r in res if r["error"]),
        "checks_failed": sum(1 for r in worked if r.get("checks_passed") is False),
        "held_inr": sum(r["held_inr"] for r in worked),
        "per_minute": 60 * len(res) / run_["wall_seconds"] if run_["wall_seconds"] else 0.0,
        "within_hour": sum(1 for d in done if d <= GOLDEN_HOUR),
        "time_to_hold": {"median": pct(done, 50), "p95": pct(done, 95), "max": max(done, default=0.0)},
        "per_ticket": {"median": pct(total, 50), "p95": pct(total, 95), "max": max(total, default=0.0)},
        "steps": {s: {"median": pct(v, 50), "p95": pct(v, 95), "max": max(v, default=0.0)}
                  for s in STEPS for v in [[r["seconds"][s] for r in worked if s in r["seconds"]]]},
        "errors": sorted({r["error"] for r in res if r["error"]})[:5],
    }


def report(run_: dict, s: dict) -> str:
    def secs(x: float) -> str:
        return f"{x:.2f} s" if x < 60 else f"{x / 60:.1f} min"

    lines = ["# Scale benchmark (generated by `python -m pipeline.bench`)", "",
             f"{s['tickets']} tickets arriving at once, worked by {run_['workers']} sessions in parallel (one schema "
             f"each, like separate banks), against {run_['rows']:,} AMLworld transactions. Ran {run_['ran_at']} on "
             f"{run_['machine']}; Exasol {run_['exasol']}.", "",
             "## The golden hour", "",
             f"**{s['within_hour']} of {s['tickets']} tickets were worked within an hour of all arriving together.** "
             f"Median time to hold {secs(s['time_to_hold']['median'])}, 95th percentile "
             f"{secs(s['time_to_hold']['p95'])}, last ticket {secs(s['time_to_hold']['max'])}. "
             f"{s['per_minute']:.1f} tickets a minute.", "",
             f"One ticket's own work: median {secs(s['per_ticket']['median'])}, 95th percentile "
             f"{secs(s['per_ticket']['p95'])}, slowest {secs(s['per_ticket']['max'])}.", "",
             "## Time per step", "", "| Step | Median | 95th percentile | Slowest |", "| --- | ---: | ---: | ---: |"]
    for step, v in s["steps"].items():
        lines.append(f"| {step} | {secs(v['median'])} | {secs(v['p95'])} | {secs(v['max'])} |")
    lines += ["", "## What happened to the tickets", "",
              f"- Worked to the end: {s['worked']}; with at least one hold: {s['with_holds']} "
              f"(Rs {s['held_inr']:,.0f} held in all).",
              f"- Intake found no exact match (nothing held, as at a bank): {s['not_exact']}.",
              f"- Checks failed (nothing held automatically): {s['checks_failed']}.",
              f"- Stopped by an error: {s['failed']}."]
    if s["errors"]:
        lines += ["", "Errors seen:", ""] + [f"- `{e}`" for e in s["errors"]]
    lines += ["", f"Each complaint reaches the bank {run_.get('report_after', REPORT_AFTER)} minutes after the "
              "payment, so the trace follows what had moved by then; money that moves later is the watch's job, as "
              "at a bank. Every ticket counts as arriving at the same moment, so the time to hold includes the wait "
              "in the queue: the worst case of a night when many complaints land together. In the lab schemas one "
              "bank owns every AMLworld account, so each trace follows every hop: more work than a bank that sees "
              "only its own. A laptop, not a bank's servers: the numbers are a floor to improve on, not a promise.",
              ""]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--tickets", type=int, default=100)
    ap.add_argument("--workers", type=int, default=3, help="parallel sessions, one schema each")
    ap.add_argument("--reported-after", type=int, default=REPORT_AFTER,
                    help="minutes from the payment to the complaint (default 30)")
    args = ap.parse_args(argv)
    r = run(args.tickets, max(1, args.workers), report_after=args.reported_after)
    s = summary(r)
    REPORT.write_text(report(r, s), encoding="utf-8")
    print(f"{s['within_hour']} of {s['tickets']} worked within the hour; median time to hold "
          f"{s['time_to_hold']['median']:.1f}s, {s['per_minute']:.1f} tickets a minute; "
          f"{s['failed']} stopped by an error. Wrote {REPORT.relative_to(ROOT)}")
    return 0 if not s["failed"] else 1


if __name__ == "__main__":
    sys.exit(main())