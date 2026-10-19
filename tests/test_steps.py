"""Case-step SQL files are well formed and every placeholder gets a value (no database needed)."""
import string
from datetime import datetime

import pytest

from pipeline.run_sql import split_statements
from pipeline.steps import STEPS, default_as_of

TRACE_PARAMS = {"case_id", "utr", "as_of", "max_hops", "window_hours", "own_bank_id"}


def placeholders(sql: str) -> set[str]:
    return {name for _, name, _, _ in string.Formatter().parse(sql) if name}


@pytest.mark.parametrize("path", sorted(STEPS.glob("*.sql")), ids=lambda p: p.name)
def test_step_files_parse(path):
    assert split_statements(path.read_text(encoding="utf-8"))


def test_trace_uses_only_known_parameters():
    statements = split_statements((STEPS / "040_trace.sql").read_text(encoding="utf-8"))
    used = set().union(*(placeholders(s) for s in statements))
    assert used == TRACE_PARAMS


def test_trace_keeps_cycle_guard_and_hop_limit():
    sql = (STEPS / "040_trace.sql").read_text(encoding="utf-8")
    assert "CONNECT BY NOCYCLE" in sql and "LEVEL <= {max_hops!d}" in sql
    assert "CURRENT_STATEMENT" in sql                     # NFR-06: rows carry their statement id


def test_demo_holds_are_placed_at_11_13():
    # Ticket reaches the bank 11:10; holds 3 minutes later (requirements s3, DECISIONS D-19)
    assert default_as_of("2026-10-18 11:10:00") == "2026-10-18 11:13:00"
    assert default_as_of(datetime(2026, 10, 18, 11, 10)) == "2026-10-18 11:13:00"