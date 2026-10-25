"""Case-step SQL files are well formed and every placeholder gets a value (no database needed)."""
import string
from datetime import datetime

import pytest

from pipeline.run_sql import split_statements
from pipeline.steps import STEPS, default_as_of

PARAMS = {
    "030_intake.sql": {"case_id", "checked_at"},
    "040_trace.sql": {"case_id", "as_of", "max_hops", "window_hours", "own_bank_id"},
    "050_attribute.sql": {"case_id", "victim_tag", "as_of", "replay_from", "own_bank_id", "rule"},
    "055_score_target.sql": {"case_id", "own_bank_id"},
    "060_score.sql": {"case_id", "as_of", "mule_band", "review_band"},
    "070_actions.sql": {"case_id", "own_bank_id", "basis_hold", "basis_narrow", "basis_freeze", "basis_trail",
                        "freeze_links_min"},
    "075_check.sql": {"case_id", "own_bank_id"},
    "081_hold.sql": {"case_id", "own_bank_id", "placed_at", "account_id", "proposal_id"},
    "082_exits.sql": {"case_id", "own_bank_id", "placed_at", "account_id", "proposal_id"},
    "083_onward.sql": {"case_id", "own_bank_id", "placed_at", "account_id", "proposal_id"},
    "084_kyc.sql": {"case_id", "own_bank_id", "placed_at", "account_id", "proposal_id"},
    "090_today.sql": {"case_id", "own_bank_id", "probe_id", "hop", "action_at", "replay_from", "victim_tag",
                      "trail_amount", "freeze_whole"},
}


def placeholders(sql: str) -> set[str]:
    return {name for _, name, _, _ in string.Formatter().parse(sql) if name}


def test_every_step_file_is_listed():
    assert {p.name for p in STEPS.glob("*.sql")} == set(PARAMS)


@pytest.mark.parametrize("name", sorted(PARAMS))
def test_step_files_parse_and_use_only_known_parameters(name):
    statements = split_statements((STEPS / name).read_text(encoding="utf-8"))
    assert statements
    used = set().union(*(placeholders(s) for s in statements))
    assert used <= PARAMS[name], used - PARAMS[name]


def test_steps_pass_exactly_those_parameters():
    from pipeline import steps

    src = (STEPS.parents[1] / "pipeline" / "steps.py").read_text(encoding="utf-8")
    for key in PARAMS["070_actions.sql"]:
        assert f'"{key}"' in src, key
    assert steps.RULES["full_freeze_evidence"]


def test_trace_keeps_cycle_guard_and_hop_limit():
    sql = (STEPS / "040_trace.sql").read_text(encoding="utf-8")
    assert "CONNECT BY NOCYCLE" in sql and "LEVEL <= {max_hops!d}" in sql
    assert "CURRENT_STATEMENT" in sql                     # NFR-06: rows carry their statement id


@pytest.mark.parametrize("name", ["081_hold.sql", "082_exits.sql", "083_onward.sql"])
def test_change_tools_refuse_to_act_without_a_passed_check(name):
    sql = (STEPS / name).read_text(encoding="utf-8")
    assert "NOT k.check_passed" in sql and "FROM CHECK_RESULT k" in sql
    assert "{proposal_id!d}" in sql                  # every change carries the proposal it carried out


def test_only_the_hold_tool_writes_holds_and_it_writes_no_freezes():
    for path in STEPS.glob("*.sql"):
        sql = path.read_text(encoding="utf-8")
        if "INSERT INTO HOLD_LEDGER" in sql:
            assert path.name == "081_hold.sql"
            assert "ACCOUNT_FREEZE" not in sql


def test_demo_holds_land_at_2_16():
    # The ticket reaches the bank 02:13; the agent's holds land 3 minutes later (DECISIONS D-19)
    assert default_as_of("2026-10-18 02:13:00") == "2026-10-18 02:16:00"
    assert default_as_of(datetime(2026, 10, 18, 2, 13)) == "2026-10-18 02:16:00"