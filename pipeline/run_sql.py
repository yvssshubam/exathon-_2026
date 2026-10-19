"""Run the numbered SQL files in order (`make db`). Requirements s8: numbered, idempotent, run in order.

Rules for files in sql/:
  * names are NNN_name.sql and run in numeric order
  * ordinary statements end with ';' at the end of a line
  * CREATE ... SCRIPT bodies end at a line holding only '/' (same as `exasol connect`, requirements s7)
  * inside a script body, a line `--@include sql/udf/file.py` is replaced by that file's contents, so UDF
    Python lives in real .py files that CI can test without Exasol (DECISIONS D-10)

Usage:
  python -m pipeline.run_sql sql              # all files
  python -m pipeline.run_sql sql --only 040   # one file
  python -m pipeline.run_sql sql --dry-run    # print what would run
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
            if stripped.endswith(";"):
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


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("folder", nargs="?", default="sql")
    ap.add_argument("--only", help="run only files starting with this prefix, e.g. 040")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args(argv)

    files = sql_files(ROOT / args.folder, args.only)
    if not files:
        print(f"no NNN_*.sql files in {args.folder}", file=sys.stderr)
        return 1

    conn = None
    if not args.dry_run:
        from pipeline.db import connect

        conn = connect()
    for path in files:
        statements = split_statements(path.read_text(encoding="utf-8"))
        started = time.perf_counter()
        for i, stmt in enumerate(statements, 1):
            first = stmt.splitlines()[0][:90]
            if args.dry_run:
                print(f"{path.name} [{i}] {first}")
                continue
            try:
                conn.execute(stmt)
            except Exception as exc:  # show exactly where it broke, then stop
                print(f"\nFAILED {path.name} statement {i}: {first}\n{exc}", file=sys.stderr)
                return 1
        if conn is not None:
            print(f"ok  {path.name:32} {len(statements):3} statements  {time.perf_counter() - started:6.2f}s")
    if conn is not None:
        try:
            from pipeline.db import audit

            audit(conn, "make_db", detail=",".join(p.name for p in files))
        except Exception as exc:  # AUDIT_LOG may not exist when running a single early file
            print(f"(audit skipped: {exc})", file=sys.stderr)
        conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())