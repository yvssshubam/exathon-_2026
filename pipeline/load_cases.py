"""Load the demo, extra and loop cases into Exasol with bulk IMPORT (`make seed`). FR-01 (case half).

Uses pyexasol's import_from_iterable, which runs a native IMPORT over HTTP transport: no row-by-row INSERT
(requirements s8). Reloading is safe: the case tables are truncated first.

  python -m pipeline.load_cases            # load
  python -m pipeline.load_cases --verify   # load, then compare row counts and sums with the files (AT-01, case half)
"""
from __future__ import annotations

import argparse
import sys
from decimal import Decimal

from pipeline.cases import DEMO_CASE, EXTRA_CASES, LOOP_CASE, Case, load_case

SNAPSHOT_TS = "2026-10-18 00:00:00.000"   # opening balances are as of the start of the case day
CASE_FILES = {"demo_case": DEMO_CASE, "extra_cases": EXTRA_CASES, "loop_case": LOOP_CASE}
TABLES = ["BANK", "ACCOUNT", "TXN", "BALANCE_SNAPSHOT", "COMPLAINT"]


def ts(value: str | None) -> str | None:
    return None if value is None else (value if "." in value else value + ".000")


def b(value: bool) -> str:
    return "TRUE" if value else "FALSE"


def rows_for(cases: dict[str, Case]) -> dict[str, list[tuple]]:
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
                                         a["account_type"], a["holder_type"], a["profile_band"], a.get("history"))
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
    out["BANK"] = list(banks.values())
    out["ACCOUNT"] = list(accounts.values())
    own = [r for r in out["BANK"] if r[2] == "TRUE"]
    if len(own) != 1:
        raise ValueError(f"exactly one bank must have is_own_bank = true, found {len(own)}")
    return out


def load(conn, rows: dict[str, list[tuple]]) -> None:
    for table in TABLES:
        conn.execute(f"TRUNCATE TABLE PH.{table}")
        conn.import_from_iterable(rows[table], ("PH", table))
        print(f"loaded {len(rows[table]):6} rows into PH.{table}")


def verify(conn, rows: dict[str, list[tuple]]) -> bool:
    ok = True
    for table in TABLES:
        n = conn.execute(f"SELECT COUNT(*) FROM PH.{table}").fetchval()
        good = n == len(rows[table])
        ok &= good
        print(f"{'ok ' if good else 'BAD'} PH.{table:17} db={n:6}  file={len(rows[table]):6}")
    db_sum = Decimal(str(conn.execute("SELECT SUM(amount_inr) FROM PH.TXN").fetchval()))
    file_sum = sum(Decimal(r[5]) for r in rows["TXN"])
    good = db_sum == file_sum
    ok &= good
    print(f"{'ok ' if good else 'BAD'} SUM(TXN.amount_inr)    db={db_sum}  file={file_sum}")
    demo = conn.execute(
        "SELECT COUNT(*) FROM PH.TXN WHERE txn_id LIKE 'T_DEMO_%' AND ts BETWEEN "
        "TIMESTAMP '2026-10-18 10:00:00' AND TIMESTAMP '2026-10-18 12:30:00'"
    ).fetchval()
    good = demo == 6
    ok &= good
    print(f"{'ok ' if good else 'BAD'} demo case transactions with correct times: {demo} of 6")
    return ok


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--verify", action="store_true")
    args = ap.parse_args(argv)
    cases = {source: load_case(path) for source, path in CASE_FILES.items()}
    rows = rows_for(cases)

    from pipeline.db import audit, connect

    conn = connect()
    load(conn, rows)
    audit(conn, "seed_cases", detail=f"{sum(len(r) for r in rows.values())} rows")
    good = verify(conn, rows) if args.verify else True
    conn.close()
    return 0 if good else 1


if __name__ == "__main__":
    sys.exit(main())