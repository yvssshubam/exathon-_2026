"""Config files say what the requirements say (sections 4, 7 and 9). Cheap tests, but they catch silent edits."""
import json
from pathlib import Path

import yaml

CONFIG = Path(__file__).resolve().parents[1] / "config"
lanes = yaml.safe_load((CONFIG / "lanes.yaml").read_text(encoding="utf-8"))["lanes"]
scoring = yaml.safe_load((CONFIG / "scoring.yaml").read_text(encoding="utf-8"))


def days(lane: str) -> dict[str, int]:
    return {d["name"]: d["days"] for d in lanes[lane]["deadlines"]}


def test_lane_a_clock_matches_sop():          # AT-11, Lane A: 7, 15, 90
    d = days("A")
    assert d["forward_grievance"] == 7
    assert d["officer_decision"] == 15
    assert d["hold_removal"] == 90
    assert d["trail_update_to_cfcfrms"] == 7
    assert d["district_review"] == d["state_review"] == 15
    assert lanes["A"]["extension_days"] == 90


def test_lane_b_clock_matches_rbi_draft():   # AT-11, Lane B: 20, 30, 60
    d = days("B")
    assert d["customer_explanation"] == 20
    assert d["bank_decision_no_explanation"] == 30
    assert d["bank_decision_after_explanation"] == 10
    assert d["max_hold_without_police"] == 60
    assert d["police_instruction"] == 30


def test_lane_rules():
    assert lanes["A"]["in_force"] is True and lanes["B"]["in_force"] is False
    assert lanes["A"]["account_hold_allowed"] is False
    assert lanes["B"]["trigger_min_inr"] == 1000
    assert lanes["A"]["interim_custody_threshold_inr"] == 50000


def test_scoring_weights_and_bands():
    weights = [s["weight"] for s in scoring["signals"].values()]
    assert abs(sum(weights) - 1.0) < 1e-9
    assert len(scoring["signals"]) == 8
    assert scoring["bands"] == {"mule": 0.70, "review": 0.40}
    assert scoring["window_days"] == 90
    assert set(scoring["never_account_hold_types"]) == {"nodal", "pool", "escrow"}
    for name, s in scoring["signals"].items():
        assert s["honest_at"] != s["mule_at"], name
        assert s["mule_sentence"] and s["honest_sentence"], name


def test_mcp_is_read_only():                  # NFR-05, AT-14
    s = json.loads((CONFIG / "mcp_settings.json").read_text(encoding="utf-8"))
    assert s["enable_read_query"] is True
    assert s["enable_write_query"] is False
    assert s["default_row_limit"] == 500
    assert s["schemas"]["like_pattern"] == "PH%"
    assert "disable_elicitation" not in s