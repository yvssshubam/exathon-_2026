"""Load the cases, their 90-day history and the scoring rules into Exasol with bulk IMPORT (`make seed`).
FR-01 (case half). History comes from pipeline/gen_history.py (seed 42); scoring rules from config/scoring.yaml.
Every complaint also lands in PH.TICKET as QUEUED: the simulated CFCFRMS queue the agent works (pipeline/agent.py).
KYC details are synthetic and derived from the account ID (never real people, never a real PAN).

Uses pyexasol's import_from_iterable, which runs a native IMPORT over HTTP transport: no row-by-row INSERT
(requirements s8). Reloading is safe: the case tables are truncated first.

  python -m pipeline.load_cases                 # load
  python -m pipeline.load_cases --verify        # load, then compare row counts and sums with the files (AT-01)
  python -m pipeline.load_cases --no-amlworld   # leave the AMLworld background out (quick reset)

When `make data` has loaded AMLworld into schema PH_AML (pipeline/load_amlworld.py), its banks, accounts and
transactions are copied into PH next to the cases with one INSERT ... SELECT each, inside Exasol (DECISIONS D-37).
"""
from __future__ import annotations

import argparse
import sys
import time
import zlib
from decimal import Decimal

import yaml

from pipeline.cases import DEMO_CASE, EXTRA_CASES, LOOP_CASE, ROOT, Case, load_case
from pipeline.gen_history import generate as generate_history

SNAPSHOT_TS = "2026-10-18 00:00:00.000"   # opening balances are as of the start of the case day
CASE_FILES = {"demo_case": DEMO_CASE, "extra_cases": EXTRA_CASES, "loop_case": LOOP_CASE}
TABLES = ["BANK", "ACCOUNT", "TXN", "BALANCE_SNAPSHOT", "COMPLAINT", "TICKET", "SCORING_CONFIG", "PROFILE_BAND"]
# Work tables emptied on every load, so a reload is a clean slate for the agent (make demo-reset).
WORK_TABLES = ["TRACE_LINK", "TRACE_EDGE", "REFERRAL_OUT", "ATTRIBUTION", "ATTRIBUTION_RAW", "ATTRIBUTION_FLOW",
               "ACCOUNT_SIGNALS", "ACCOUNT_SCORE", "CASE_ACCOUNT", "CASE_ACTION", "CHECK_RESULT", "HOLD_LEDGER",
               "PORTAL_ENTRY", "DECISION", "AGENT_RUN", "AGENT_STEP", "TODAY_RAW", "TODAY_OUTCOME",
               "WATCH_EVENT"]
SCORING = ROOT / "config" / "scoring.yaml"
# AMLworld background copied from schema PH_AML (make data) into PH, column for column.
AMLWORLD_COPY = {
    "BANK": "bank_id, bank_name, is_own_bank",
    "ACCOUNT": "account_id, bank_id, holder_name, opened_on, account_type, holder_type, profile_band, "
               "history_profile, customer_id",
    "TXN": "txn_id, utr, ts, from_account, to_account, amount_inr, channel, is_exit, exit_identifier, data_source",
}
AMLWORLD_KEY = {"BANK": "bank_id", "ACCOUNT": "account_id", "TXN": "txn_id"}   # every AMLworld ID starts with AML


def ts(value: str | None) -> str | None:
    return None if value is None else (value if "." in value else value + ".000")


def b(value: bool) -> str:
    return "TRUE" if value else "FALSE"


def rupees(paise: int) -> str:
    return f"{paise // 100}.{paise % 100:02d}"


def synthetic_kyc(account: dict) -> tuple:
    """customer_id, ifsc, mobile, email, address, pan: deterministic, obviously synthetic (a case may give its own)."""
    k = account.get("kyc") or {}
    n = zlib.crc32(account["account_id"].encode())
    bank = account["bank_id"][-1]
    return (k.get("customer_id", f"CUST{n % 10**8:08d}"),
            k.get("ifsc", f"BNK{bank}0{n % 10**6:06d}"),
            k.get("mobile", f"+91 9XXXX X{n % 10**4:04d}"),
            k.get("email", f"{account['account_id'].lower()}@example.com"),
            k.get("address", "Synthetic address, Vellore, Tamil Nadu"),
            k.get("pan", f"SYNTH{n % 10**4:04d}X"))


def rows_for(cases: dict[str, Case], history: bool = True) -> dict[str, list[tuple]]:
    banks: dict[str, tuple] = {}
    accounts: dict[str, tuple] = {}
    out: dict[str, list[tuple]] = {t: [] for t in TABLES}
    for source, case in cases.items():
        for bk in case.banks:
            banks[bk["bank_id"]] = (bk["bank_id"], bk["name"], b(bk["is_own_bank"]))
        for a in case.accounts:
            if a["account_id"] in accounts:
                raise ValueError(f"account {a['account_id']} defined in two case files")
            accounts[a["account_id"]] = (a["account_id"], a["bank_id"], a.get("holder"), a["opened_on"],
                                         a["account_type"], a["holder_type"], a["profile_band"], a.get("history"),
                                         *synthetic_kyc(a))
            if a.get("opening_balance_inr") is not None:
                opening = str(Decimal(str(a["opening_balance_inr"])))
                out["BALANCE_SNAPSHOT"].append((a["account_id"], SNAPSHOT_TS, opening))
        for t in case.txns:
            out["TXN"].append((t.txn_id, t.utr, ts(t.ts), t.from_account, t.to_account, str(t.amount_inr),
                               t.channel, b(t.is_exit), t.exit_identifier, source))
        for c in case.complaints:
            amount = c.get("amount_inr")
            out["COMPLAINT"].append((
                c["case_id"], c["ack_no"], c["source"], c["victim_account"], c["victim_tag"], c["utr"],
                None if amount is None else str(Decimal(str(amount))), ts(c.get("txn_at")), ts(c["reported_at"]),
                c.get("beneficiary_account"), c["lane"], c.get("notice_legal_basis"), b(c["notice_has_amount"]),
                c.get("ordering_authority"), c.get("ordering_reference"),
            ))
            out["TICKET"].append((c["case_id"], ts(c["reported_at"]), "QUEUED", None, None))
    if history:
        all_accounts = [a for case in cases.values() for a in case.accounts]
        for h in generate_history(all_accounts):
            out["TXN"].append((h["txn_id"], h["utr"], ts(h["ts"]), h["from"], h["to"], rupees(h["amount_paise"]),
                               h["channel"], b(h["to"].startswith("EXIT:")), h["exit_identifier"], "history"))
    scoring = yaml.safe_load(SCORING.read_text(encoding="utf-8"))
    for name, sig in scoring["signals"].items():
        out["SCORING_CONFIG"].append((name, sig["weight"], sig["honest_at"], sig["mule_at"],
                                      sig["mule_sentence"], sig["honest_sentence"]))
    for band, expected in scoring["profile_band_expected_monthly_credit_inr"].items():
        out["PROFILE_BAND"].append((band, str(Decimal(str(expected)))))
    out["BANK"] = list(banks.values())
    out["ACCOUNT"] = list(accounts.values())
    own = [r for r in out["BANK"] if r[2] == "TRUE"]
    if len(own) != 1:
        raise ValueError(f"exactly one bank must have is_own_bank = true, found {len(own)}")
    return out


def amlworld_loaded(conn) -> bool:
    return bool(conn.execute("SELECT COUNT(*) FROM EXA_ALL_TABLES WHERE TABLE_SCHEMA = 'PH_AML' "
                             "AND TABLE_NAME = 'TXN'").fetchval())


def load(conn, rows: dict[str, list[tuple]], amlworld: bool = True) -> dict[str, int]:
    """Load the cases; keep or copy in the AMLworld background (make data). Returns the AMLworld rows per table.

    AMLworld rows (IDs starting AML) are left in place when they already match PH_AML, so a reset from the console
    reloads only the cases; they are copied in again after `make db` or a new `make data`.
    """
    for table in WORK_TABLES:
        conn.execute(f"TRUNCATE TABLE PH.{table}")
    keep = amlworld and amlworld_loaded(conn)
    for table in TABLES:
        if keep and table in AMLWORLD_KEY:
            conn.execute(f"DELETE FROM PH.{table} WHERE LEFT({AMLWORLD_KEY[table]}, 3) <> 'AML'")
        else:
            conn.execute(f"TRUNCATE TABLE PH.{table}")
        conn.import_from_iterable(rows[table], ("PH", table))
        print(f"loaded {len(rows[table]):7} rows into PH.{table}")
    copied: dict[str, int] = {}
    if keep:
        for table, cols in AMLWORLD_COPY.items():
            key = AMLWORLD_KEY[table]
            staged = conn.execute(f"SELECT COUNT(*) FROM PH_AML.{table}").fetchval()
            present = conn.execute(f"SELECT COUNT(*) FROM PH.{table} WHERE LEFT({key}, 3) = 'AML'").fetchval()
            if present != staged:
                started = time.perf_counter()
                conn.execute(f"DELETE FROM PH.{table} WHERE LEFT({key}, 3) = 'AML'")
                conn.execute(f"INSERT INTO PH.{table} ({cols}) SELECT {cols} FROM PH_AML.{table}")
                print(f"copied {staged:7} AMLworld rows into PH.{table} ({time.perf_counter() - started:.1f}s)")
            else:
                print(f"kept   {staged:7} AMLworld rows in PH.{table}")
            copied[table] = staged
    elif amlworld:
        print("AMLworld not loaded (make data): the cases run without the background")
    return copied


def verify(conn, rows: dict[str, list[tuple]], copied: dict[str, int] | None = None) -> bool:
    ok = True
    copied = copied or {}
    for table in TABLES:
        n = conn.execute(f"SELECT COUNT(*) FROM PH.{table}").fetchval()
        expected = len(rows[table]) + copied.get(table, 0)
        good = n == expected
        ok &= good
        extra = f" + AMLworld={copied[table]}" if table in copied else ""
        print(f"{'ok ' if good else 'BAD'} PH.{table:17} db={n:7}  file={len(rows[table]):7}{extra}")
    db_sum = Decimal(str(conn.execute(
        "SELECT SUM(amount_inr) FROM PH.TXN WHERE data_source <> 'amlworld'").fetchval()))
    file_sum = sum(Decimal(r[5]) for r in rows["TXN"])
    good = db_sum == file_sum
    ok &= good
    print(f"{'ok ' if good else 'BAD'} SUM(TXN.amount_inr)    db={db_sum}  file={file_sum}")
    demo = conn.execute(
        "SELECT COUNT(*) FROM PH.TXN WHERE txn_id LIKE 'T_DEMO_%' AND ts BETWEEN "
        "TIMESTAMP '2026-10-18 01:52:00' AND TIMESTAMP '2026-10-18 05:10:00'"
    ).fetchval()
    good = demo == 7
    ok &= good
    print(f"{'ok ' if good else 'BAD'} demo case transactions with correct times: {demo} of 7")
    return ok


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--verify", action="store_true")
    ap.add_argument("--no-amlworld", action="store_true", help="leave the AMLworld background out")
    args = ap.parse_args(argv)
    cases = {source: load_case(path) for source, path in CASE_FILES.items()}
    rows = rows_for(cases)

    from pipeline.db import audit, connect

    conn = connect()
    copied = load(conn, rows, amlworld=not args.no_amlworld)
    audit(conn, "seed_cases", detail=f"{sum(len(r) for r in rows.values())} case rows, "
                                     f"{copied.get('TXN', 0)} AMLworld transactions")
    good = verify(conn, rows, copied) if args.verify else True
    conn.close()
    return 0 if good else 1


if __name__ == "__main__":
    sys.exit(main())