"""Load IBM AMLworld HI-Small into Exasol (`make data`, FR-01, AT-01).

The CSVs are downloaded by hand from Kaggle into data/raw/ (README, "Data is synthetic"); they are never committed.
Steps, all in Exasol except the patterns file, which is small and needs parsing:

  1. sql/amlworld/010_stage.sql   create schema PH_AML and the raw tables
  2. native IMPORT                HI-Small_Trans.csv and HI-Small_accounts.csv straight into the raw tables
  3. patterns                     HI-Small_Patterns.txt parsed here and imported (optional file)
  4. sql/amlworld/020_rates.sql   clean and number the rows; read each currency's dollar rate out of the file
  5. sql/amlworld/030_map.sql     build PH_AML.BANK, ACCOUNT and TXN in the shape of the PH tables
  6. verify                       row counts against the files; stop loudly if the file has changed shape

PH_AML survives `make db` and `make seed`. `make seed` (pipeline/load_cases.py) makes each AMLworld bank a branch of
Bank X, Y or Z and copies into each bank's schema only the rows touching its own branches, next to the cases, so each
bank's agent works its tickets against a full-size transaction table.

  python -m pipeline.load_amlworld             # load, then `python -m pipeline.load_cases --verify`
  python -m pipeline.load_amlworld --check     # only check the files are there and look right (no database)
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

import yaml

from pipeline.run_sql import split_statements

ROOT = Path(__file__).resolve().parents[1]
CONFIG = ROOT / "config" / "amlworld.yaml"
SQL = ROOT / "sql" / "amlworld"

TRANS_HEADER = ["Timestamp", "From Bank", "Account", "To Bank", "Account", "Amount Received", "Receiving Currency",
                "Amount Paid", "Payment Currency", "Payment Format", "Is Laundering"]
ACCOUNTS_HEADER = ["Bank Name", "Bank ID", "Account Number", "Entity ID", "Entity Name"]


class ShapeError(SystemExit):
    """The file is not shaped the way this loader expects (requirements s8: fail loudly)."""


def config() -> dict:
    cfg = yaml.safe_load(CONFIG.read_text(encoding="utf-8"))
    for key in ("transactions", "accounts", "patterns"):
        cfg["files"][key] = ROOT / cfg["files"][key]
    return cfg


def header(path: Path) -> list[str]:
    with path.open(encoding="utf-8-sig", newline="") as f:
        return [h.strip() for h in f.readline().strip("\r\n").split(",")]


def count_rows(path: Path) -> int:
    """Data rows in a CSV (lines after the header), counted without parsing so it is quick on 500 MB."""
    lines, last = 0, b"\n"
    with path.open("rb") as f:
        while chunk := f.read(1 << 24):
            lines += chunk.count(b"\n")
            last = chunk[-1:]
    if last != b"\n":
        lines += 1
    return lines - 1


def parse_patterns(lines) -> list[tuple]:
    """HI-Small_Patterns.txt -> (pattern_id, type, detail, ts, from_bank, from_acc, to_bank, to_acc, amount, format).

    The file is blocks of
        BEGIN LAUNDERING ATTEMPT - FAN-OUT:  Max 12-degree Fan-Out
        2022/09/01 05:44,0116,80F1AF380,013145,80D210C20,17193.07,US Dollar,17193.07,US Dollar,ACH,1
        END LAUNDERING ATTEMPT - FAN-OUT
    """
    rows, pid, kind, detail = [], 0, None, None
    for n, raw in enumerate(lines, 1):
        line = raw.strip()
        if not line:
            continue
        if line.startswith("BEGIN LAUNDERING ATTEMPT"):
            rest = line.split("-", 1)[1].strip() if "-" in line else ""
            kind, _, detail = (part.strip() for part in rest.partition(":"))
            pid += 1
            continue
        if line.startswith("END LAUNDERING ATTEMPT"):
            kind = detail = None
            continue
        if kind is None:
            raise ShapeError(f"patterns file line {n}: a transaction outside a BEGIN/END block: {line[:80]}")
        f = line.split(",")
        if len(f) != 11:
            raise ShapeError(f"patterns file line {n}: expected 11 fields, found {len(f)}: {line[:80]}")
        rows.append((pid, kind, detail or None, f[0], f[1], f[2], f[3], f[4], f[7], f[9]))
    return rows


def check_files(cfg: dict) -> list[str]:
    """Problems with the downloaded files, as plain sentences (empty list = fine)."""
    files, problems = cfg["files"], []
    for key in ("transactions", "accounts"):
        if not files[key].exists():
            problems.append(f"missing {files[key].relative_to(ROOT)}: download it from Kaggle (README, Data)")
    if problems:
        return problems
    if header(files["transactions"]) != TRANS_HEADER:
        problems.append(f"{files['transactions'].name} header changed: {header(files['transactions'])}")
    if header(files["accounts"]) != ACCOUNTS_HEADER:
        problems.append(f"{files['accounts'].name} header changed: {header(files['accounts'])}")
    return problems


def run_file(conn, name: str, params: dict | None = None) -> None:
    started = time.perf_counter()
    statements = split_statements((SQL / name).read_text(encoding="utf-8"), ROOT)
    for stmt in statements:
        try:
            conn.execute(stmt, params)
        except Exception as exc:
            raise SystemExit(f"FAILED {name}: {stmt.splitlines()[0][:90]}\n{exc}") from exc
    print(f"ok  {name:16} {len(statements):2} statements  {time.perf_counter() - started:7.1f}s")


def load(conn, cfg: dict) -> dict:
    files = cfg["files"]
    run_file(conn, "010_stage.sql")

    for key, table in (("transactions", "TXN_RAW"), ("accounts", "ACCOUNT_RAW")):
        started = time.perf_counter()
        conn.import_from_file(str(files[key]), ("PH_AML", table), import_params={"skip": 1})
        print(f"ok  IMPORT {files[key].name:24} {time.perf_counter() - started:7.1f}s")

    channels = cfg["channels"]
    conn.import_from_iterable([(k, v) for k, v in channels.items()], ("PH_AML", "CHANNEL_MAP"))
    unknown = [f for (f,) in conn.execute(
        "SELECT DISTINCT TRIM(payment_format) FROM PH_AML.TXN_RAW WHERE TRIM(payment_format) NOT IN "
        "(SELECT payment_format FROM PH_AML.CHANNEL_MAP)").fetchall()]
    if unknown:
        raise ShapeError(f"payment formats not in config/amlworld.yaml channels: {unknown}")

    patterns = []
    if files["patterns"].exists():
        with files["patterns"].open(encoding="utf-8") as f:
            patterns = parse_patterns(f)
        conn.import_from_iterable(patterns, ("PH_AML", "PATTERN_RAW"))
        print(f"ok  patterns: {len(patterns)} transactions in {len({p[0] for p in patterns})} laundering attempts")
    else:
        print(f"--  {files['patterns'].name} not found: skipping the laundering-attempt labels")

    run_file(conn, "020_rates.sql")
    missing_sql = ("SELECT DISTINCT payment_currency FROM PH_AML.TXN_KEYED WHERE payment_currency NOT IN "
                   "(SELECT currency_name FROM PH_AML.FX_RATE WHERE usd_per_unit > 0)")
    missing = [c for (c,) in conn.execute(missing_sql).fetchall()]
    fallback = cfg.get("fallback_usd_per_unit") or {}
    use = [(c, float(fallback[c]), 0) for c in missing if c in fallback]
    if use:
        conn.execute("DELETE FROM PH_AML.FX_RATE WHERE currency_name IN ({cs})", {"cs": [c for c, _, _ in use]})
        conn.import_from_iterable(use, ("PH_AML", "FX_RATE"))
        print(f"--  no dollar conversions in the file for {[c for c, _, _ in use]}: using the fallback rates")
    missing = [c for (c,) in conn.execute(missing_sql).fetchall()]
    if missing:
        raise ShapeError(f"no dollar rate for {missing}: add them to fallback_usd_per_unit in config/amlworld.yaml")

    run_file(conn, "030_map.sql", {"last_day": str(cfg["last_day"]), "usd_to_inr": cfg["usd_to_inr"]})
    return {"patterns": len(patterns)}


def verify(conn, cfg: dict) -> bool:
    files, ok = cfg["files"], True

    def line(good: bool, text: str) -> None:
        nonlocal ok
        ok &= good
        print(f"{'ok ' if good else 'BAD'} {text}")

    q = lambda sql: conn.execute(sql).fetchval()  # noqa: E731
    for key, table in (("transactions", "TXN_RAW"), ("accounts", "ACCOUNT_RAW")):
        n_file, n_db = count_rows(files[key]), q(f"SELECT COUNT(*) FROM PH_AML.{table}")
        line(n_file == n_db, f"{files[key].name:24} file={n_file:9}  PH_AML.{table}={n_db:9}")

    raw = q("SELECT COUNT(*) FROM PH_AML.TXN_KEYED")
    loaded = q("SELECT COUNT(*) FROM PH_AML.TXN")
    skipped = q("SELECT COUNT(*) FROM PH_AML.TXN_KEYED k JOIN PH_AML.CHANNEL_MAP m ON m.payment_format = "
                "k.payment_format WHERE m.channel IS NULL OR (k.from_bank = k.to_bank AND k.from_acc = k.to_acc)")
    line(loaded + skipped == raw, f"transactions loaded={loaded}  not loaded (self-transfers)={skipped}  raw={raw}")

    first, last = conn.execute("SELECT MIN(ts), MAX(ts) FROM PH_AML.TXN").fetchone()
    line(str(last)[:10] <= str(cfg["last_day"]), f"dates moved to {str(first)[:16]} .. {str(last)[:16]} "
                                                  f"(cases start 2026-10-18)")
    accounts = q("SELECT COUNT(*) FROM PH_AML.ACCOUNT")
    orphans = q("SELECT COUNT(*) FROM (SELECT from_account AS acc FROM PH_AML.TXN UNION "
                "SELECT to_account FROM PH_AML.TXN) t WHERE t.acc NOT IN (SELECT account_id FROM PH_AML.ACCOUNT)")
    line(orphans == 0, f"accounts={accounts}  banks={q('SELECT COUNT(*) FROM PH_AML.BANK')}  "
                       f"transactions naming an unknown account={orphans}")
    laundering = q("SELECT COUNT(*) FROM PH_AML.TXN WHERE is_laundering")
    print(f"    laundering-labelled transactions: {laundering}")
    if files["patterns"].exists():
        n_raw, n_match = q("SELECT COUNT(*) FROM PH_AML.PATTERN_RAW"), q("SELECT COUNT(*) FROM PH_AML.PATTERN_TXN")
        line(n_match >= n_raw * 0.99, f"pattern transactions matched to loaded rows: {n_match} of {n_raw}")
    print("    dollar rates read from the file (US dollars per unit):")
    for cur, rate, n in conn.execute("SELECT currency_name, usd_per_unit, sample_rows FROM PH_AML.FX_RATE "
                                     "ORDER BY currency_name").fetchall():
        print(f"      {cur:20} {rate:14.6f}  " + (f"from {n} rows" if n else "fallback (config)"))
    total = q("SELECT SUM(amount_inr) FROM PH_AML.TXN")
    print(f"    total moved: Rs {float(total):,.0f} at Rs {cfg['usd_to_inr']} per US dollar")
    return ok


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true", help="check the files only; no database")
    args = ap.parse_args(argv)
    cfg = config()
    problems = check_files(cfg)
    for p in problems:
        print(f"BAD {p}")
    if problems:
        return 1
    if args.check:
        for key in ("transactions", "accounts"):
            print(f"ok  {cfg['files'][key].name}: {count_rows(cfg['files'][key])} rows, header as expected")
        return 0

    from pipeline.db import audit, connect

    started = time.perf_counter()
    conn = connect()
    load(conn, cfg)
    good = verify(conn, cfg)
    try:
        n = conn.execute("SELECT COUNT(*) FROM PH_AML.TXN").fetchval()
        audit(conn, "load_amlworld", detail=f"{n} transactions")
    except Exception as exc:  # no bank schema yet: `make db` has not run
        print(f"(audit skipped: {exc})", file=sys.stderr)
    conn.close()
    print(f"{'done' if good else 'FINISHED WITH PROBLEMS'} in {time.perf_counter() - started:.0f}s. "
          "Next: python -m pipeline.load_cases --verify  (gives each bank its share of AMLworld)")
    return 0 if good else 1


if __name__ == "__main__":
    sys.exit(main())