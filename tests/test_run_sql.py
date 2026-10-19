"""The SQL file runner splits files correctly, so `make db` never sends half a statement to Exasol."""
from pathlib import Path

import pytest

from pipeline.cases import load_case
from pipeline.load_cases import CASE_FILES, rows_for
from pipeline.run_sql import ROOT, split_statements, sql_files


def test_plain_statements_and_comments():
    sql = "-- header\nCREATE SCHEMA PH;\n\nCREATE TABLE PH.X (\n  a INT\n);\n"
    assert split_statements(sql) == ["CREATE SCHEMA PH", "CREATE TABLE PH.X (\n  a INT\n)"]


def test_script_body_ends_at_slash_and_keeps_semicolons():
    sql = (
        "CREATE OR REPLACE PYTHON3 SET SCRIPT PH.F(a INT) EMITS (b INT) AS\n"
        "def run(ctx):\n    x = 1; y = 2\n    ctx.emit(x)\n/\nSELECT 1;\n"
    )
    stmts = split_statements(sql)
    assert len(stmts) == 2
    assert stmts[0].endswith("ctx.emit(x)") and "x = 1; y = 2" in stmts[0]
    assert stmts[1] == "SELECT 1"


def test_include_inlines_python_file(tmp_path: Path):
    (tmp_path / "udf.py").write_text("def run(ctx):\n    return 42\n")
    sql = "CREATE OR REPLACE PYTHON3 SCALAR SCRIPT PH.G() RETURNS INT AS\n--@include udf.py\n/\n"
    assert split_statements(sql, root=tmp_path) == [
        "CREATE OR REPLACE PYTHON3 SCALAR SCRIPT PH.G() RETURNS INT AS\ndef run(ctx):\n    return 42"
    ]


def test_unterminated_script_is_an_error():
    with pytest.raises(ValueError, match="missing a '/'"):
        split_statements("CREATE PYTHON3 SCALAR SCRIPT PH.H() RETURNS INT AS\ndef run(ctx):\n    return 1\n")


def test_unterminated_statement_is_an_error():
    with pytest.raises(ValueError, match="missing ';'"):
        split_statements("SELECT 1\n")


def test_repo_sql_files_parse_and_are_numbered():
    files = sql_files(ROOT / "sql")
    assert [p.name for p in files][:2] == ["010_schema.sql", "015_smoke_udf.sql"]
    schema = split_statements((ROOT / "sql" / "010_schema.sql").read_text())
    tables = [s for s in schema if s.upper().startswith("CREATE OR REPLACE TABLE")]
    assert len(tables) == 14
    for stmt in tables:                       # every table carries a COMMENT for the MCP server (s7)
        assert ") COMMENT IS '" in stmt, stmt.splitlines()[0]
    for f in files:
        split_statements(f.read_text())


def test_seed_rows_are_consistent():
    rows = rows_for({k: load_case(v) for k, v in CASE_FILES.items()})
    accounts = {r[0] for r in rows["ACCOUNT"]}
    for txn in rows["TXN"]:
        assert txn[3] in accounts
        assert txn[4] in accounts or txn[4].startswith("EXIT:")
        assert txn[2].endswith(".000")
    assert sum(1 for r in rows["BANK"] if r[2] == "TRUE") == 1
    assert len(rows["COMPLAINT"]) == 22       # 1 demo + 20 extra + 1 loop