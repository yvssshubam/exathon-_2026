"""Config files say what the design says (docs/DESIGN.md, requirements s7, s9). Cheap tests that catch silent edits."""
import json
from pathlib import Path

import yaml

CONFIG = Path(__file__).resolve().parents[1] / "config"
rules = yaml.safe_load((CONFIG / "rules.yaml").read_text(encoding="utf-8"))
scoring = yaml.safe_load((CONFIG / "scoring.yaml").read_text(encoding="utf-8"))


def test_rules_split_automatic_and_officer():
    assert set(rules["automatic"]) & set(rules["officer"]) == set()
    assert "PROPOSE_FULL_FREEZE" in rules["officer"]
    assert not any("FREEZE" in name for name in rules["automatic"])


def test_full_freeze_evidence_and_legal_bases():
    ev = rules["full_freeze_evidence"]
    assert ev == {"complaint_links_min": 2, "pass_through_min": 0.8, "cash_exits_min": 2}
    assert "S.168" in rules["legal_basis"]["hold"] and "9.1(i)" in rules["legal_basis"]["hold"]
    assert "Rajasthan" in rules["legal_basis"]["freeze"]
    assert rules["today_defaults"]["freezes_whole_account"] is True


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