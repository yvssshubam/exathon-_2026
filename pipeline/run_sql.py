"""Build the database (`make db`): one schema per bank, the portal schema, then the users and their privileges.

  1. every file in sql/bank/ runs once per bank in config/banks.yaml, after OPEN SCHEMA <the bank's schema>
     (PH_X, PH_Y, PH_Z), so the same files build identical, separate schemas
  2. every file in sql/portal/ runs once, after OPEN SCHEMA PH_PORTAL
  3. pipeline/users.py creates the maker, checker and portal users and grants each only what it may touch
The schema PH of earlier builds (one schema for every bank) is dropped first.

Rules for the files:
  * names are NNN_name.sql and run in numeric order
  * ordinary statements end with ';' at the end of a line
  * CREATE ... SCRIPT bodies end at a line holding only '/' (same as `exasol connect`)
  * inside a script body, a line `--@include sql/udf/file.py` is replaced by that file's contents, so UDF
    Python lives in real .py files that CI can test without Exasol (DECISIONS D-10)

Usage:
  python -m pipeline.run_sql                  # everything (the folder argument `sql` is accepted and ignored)
  python -m pipeline.run_sql --only 050       # files starting with 050, in every bank
  python -m pipeline.run_sql --dry-run        # print what would run
"""
from __future__ import annotations

import argparse
import re
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT_START = re.compile(
    r"^\s*CREATE\s+(OR\s+REPLACE\s+)?(PYTHON3\s+|PYTHON\s+|LUA\s+|JAVA\s+|R\s+)?(SCALAR\s+|SET\s+)?SCRIPT\b",
    re.IGNORECASE,
)
INCLUDE = re.compile(r"^\s*--@include\s+(\S+)\s*$")
FILE_NAME = re.compile(r"^\d{3}_.+\.sql$")
BANK_SQL = ROOT / "sql" / "bank"
PORTAL_SQL = ROOT / "sql" / "portal"
OLD_SCHEMAS = ("PH",)


def split_statements(text: str, root: Path = ROOT) -> list[str]:
    """Split a SQL file into statements, keeping script bodies whole and expanding --@include."""
    statements: list[str] = []
    buf: list[str] = []
    in_script = False
    for line in text.splitlines():
        stripped = line.strip()
        if not buf:
            if not stripped or stripped.startswith("--"):
                continue
            in_script = bool(SCRIPT_START.match(line))
        if in_script:
            if stripped == "/":
                statements.append("\n".join(buf).rstrip())
                buf, in_script = [], False
                continue
            m = INCLUDE.match(line)
            if m:
                buf.extend((root / m.group(1)).read_text(encoding="utf-8").splitlines())
                continue
            buf.append(line)
        else:
            buf.append(line)
            if stripped.endswith(";") and not stripped.startswith("--"):   # a ';' in a comment ends nothing
                statements.append("\n".join(buf).rstrip()[:-1].rstrip())
                buf = []
    if buf:
        kind = "script (missing a '/' line)" if in_script else "statement (missing ';')"
        raise ValueError(f"unterminated {kind}: {buf[0].strip()[:80]}")
    return statements


def sql_files(folder: Path, only: str | None = None) -> list[Path]:
    files = sorted(p for p in folder.iterdir() if FILE_NAME.match(p.name))
    if only:
        files = [p for p in files if p.name.startswith(only)]
    return files


def plan(only: str | None = None) -> list[tuple[str, Path]]:
    """(schema, file) pairs in the order they run."""
    from pipeline.db import BANKS, PORTAL_SCHEMA, schema_for

    out = [(schema_for(bank), path) for bank in BANKS for path in sql_files(BANK_SQL, only)]
    out += [(PORTAL_SCHEMA, path) for path in sql_files(PORTAL_SQL, only)]
    return out


def build(conn, only: str | None = None, dry_run: bool = False) -> int:
    """Run the plan on an admin connection. Returns 0 on success, 1 on the first failure."""
    if not only and not dry_run:
        for old in OLD_SCHEMAS:
            conn.execute(f"DROP SCHEMA IF EXISTS {old} CASCADE")
    opened = None
    for schema, path in plan(only):
        statements = split_statements(path.read_text(encoding="utf-8"))
        started = time.perf_counter()
        if dry_run:
            for i, stmt in enumerate(statements, 1):
                print(f"{schema:9} {path.parent.name}/{path.name} [{i}] {stmt.splitlines()[0][:80]}")
            continue
        if schema != opened:
            conn.execute(f"CREATE SCHEMA IF NOT EXISTS {schema}")
            conn.execute(f"OPEN SCHEMA {schema}")
            opened = schema
        for i, stmt in enumerate(statements, 1):
            try:
                conn.execute(stmt)
            except Exception as exc:  # show exactly where it broke, then stop
                first = stmt.splitlines()[0][:90]
                print(f"\nFAILED {schema} {path.name} statement {i}: {first}\n{exc}", file=sys.stderr)
                return 1
        print(f"ok  {schema:9} {path.parent.name + '/' + path.name:30} {len(statements):3} statements  "
              f"{time.perf_counter() - started:6.2f}s")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("folder", nargs="?", default="sql", help="ignored; kept so `run_sql sql` still works")
    ap.add_argument("--only", help="run only files starting with this prefix, e.g. 050")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--no-users", action="store_true", help="skip creating users and grants")
    args = ap.parse_args(argv)

    if args.dry_run:
        return build(None, args.only, dry_run=True)
    from pipeline.db import audit, connect, settings

    conn = connect()
    try:
        code = build(conn, args.only)
        if code:
            return code
        if not args.no_users:
            from pipeline.users import apply

            if apply(conn, settings()["role_password"]):
                return 1
        from pipeline.db import BANKS, schema_for

        for bank in BANKS:
            conn.execute(f"OPEN SCHEMA {schema_for(bank)}")
            audit(conn, "make_db", actor="admin", detail=f"only={args.only or 'all'}")
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())