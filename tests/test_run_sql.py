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
    from pipeline.run_sql import BANK_SQL, PORTAL_SQL

    assert [p.name for p in sql_files(BANK_SQL)] == ["010_schema.sql", "015_smoke_udf.sql", "050_attribute_udf.sql",
                                                     "090_views.sql"]
    assert [p.name for p in sql_files(PORTAL_SQL)] == ["010_portal.sql"]
    schema = split_statements((BANK_SQL / "010_schema.sql").read_text())
    tables = [s for s in schema if s.upper().startswith("CREATE OR REPLACE TABLE")]
    assert len(tables) == 39
    for stmt in tables:                       # every table says what it is for
        assert ") COMMENT IS '" in stmt, stmt.splitlines()[0]
    for folder in ("bank", "portal", "lab", "learn", "steps", "amlworld"):
        for f in sql_files(ROOT / "sql" / folder):
            split_statements(f.read_text())


def test_every_bank_gets_its_own_schema_and_names_stay_unqualified():
    from pipeline.run_sql import plan

    schemas = [schema for schema, _ in plan()]
    assert schemas.count("PH_X") == schemas.count("PH_Y") == schemas.count("PH_Z") == 4
    assert schemas.count("PH_PORTAL") == 1
    for folder in ("bank", "steps"):          # bank SQL runs in whichever bank schema is open: no PH. prefix
        for f in sql_files(ROOT / "sql" / folder):
            assert "PH." not in f.read_text(), f.name


def test_seed_rows_are_consistent():

    rows = rows_for({k: load_case(v) for k, v in CASE_FILES.items()})
    accounts = {r[0] for r in rows["ACCOUNT"]}
    for txn in rows["TXN"]:
        assert txn[3] in accounts or txn[3].startswith("EXT_")       # EXT_: history counterparties
        assert txn[4] in accounts or txn[4].startswith(("EXIT:", "EXT_"))
        assert txn[2].endswith(".000")
    from pipeline import learned

    trained = learned.load() is not None
    assert len(rows["SCORING_CONFIG"]) == 8 + trained and len(rows["PROFILE_BAND"]) == 3
    assert bool(rows["LEARNED_COEF"]) == trained
    assert len(rows["PORTAL_TICKET"]) == len(rows["COMPLAINTS"])
    assert sum(1 for r in rows["BANK"] if r[2] == "TRUE") == 0       # the world belongs to no bank


def test_each_bank_gets_only_its_own_share():
    from pipeline.load_cases import bank_rows

    rows = rows_for({k: load_case(v) for k, v in CASE_FILES.items()}, history=False)
    bank_of = {r[0]: r[1] for r in rows["ACCOUNT"]}
    for bank in ("BANK_X", "BANK_Y", "BANK_Z"):
        share = bank_rows(rows, bank)
        assert [r[0] for r in share["BANK"] if r[2] == "TRUE"] == [bank]
        assert all(bank_of.get(t[3]) == bank or bank_of.get(t[4]) == bank for t in share["TXN"])
        own = [r for r in share["ACCOUNT"] if r[1] == bank]
        stubs = [r for r in share["ACCOUNT"] if r[1] != bank]
        assert own and all(r[3] is None for r in stubs)              # other banks' accounts: no KYC, no history
        assert all(bank_of[r[0]] == bank for r in share["BALANCE_SNAPSHOT"])


def test_every_seed_row_has_one_value_per_column():
    """Each bank's rows match its tables' column counts, so IMPORT never sees a short row."""
    import re

    from pipeline.load_cases import BANK_TABLES, bank_rows

    columns = {}
    for stmt in split_statements((ROOT / "sql" / "bank" / "010_schema.sql").read_text()):
        m = re.match(r"CREATE OR REPLACE TABLE (\w+) \(", stmt)
        if m:
            body = stmt[m.end():]
            columns[m.group(1)] = len([ln for ln in body.splitlines()
                                       if re.match(r"\s+[a-z_]+\s+[A-Z]", ln) and "CONSTRAINT" not in ln])
    world = rows_for({k: load_case(v) for k, v in CASE_FILES.items()}, history=False)
    for bank in ("BANK_X", "BANK_Y", "BANK_Z"):
        share = bank_rows(world, bank)
        for table in BANK_TABLES:
            widths = {len(r) for r in share[table]}
            assert widths <= {columns[table]}, (bank, table, widths, columns[table])