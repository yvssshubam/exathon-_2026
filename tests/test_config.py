"""Config files say what the design says (docs/DESIGN.md, requirements s7, s9). Cheap tests that catch silent edits."""
from pathlib import Path

import yaml

CONFIG = Path(__file__).resolve().parents[1] / "config"
rules = yaml.safe_load((CONFIG / "rules.yaml").read_text(encoding="utf-8"))
scoring = yaml.safe_load((CONFIG / "scoring.yaml").read_text(encoding="utf-8"))


banks = yaml.safe_load((CONFIG / "banks.yaml").read_text(encoding="utf-8"))


def test_rules_split_automatic_and_officer():
    assert set(rules["automatic"]) & set(rules["officer"]) == set()
    assert not any("FREEZE" in name or "RELEASE" in name or "NARROW" in name for name in rules["automatic"])


def test_the_officer_decides_five_things_only():
    assert set(rules["officer"]) == {"PROPOSE_NARROW", "PROPOSE_FULL_FREEZE", "UNMATCHED_NOTICE", "PROPOSE_RELEASE",
                                     "PROPOSE_PRE_ALERT"}


def test_full_freeze_evidence_and_legal_bases():
    ev = rules["full_freeze_evidence"]
    assert ev["complaint_links_min"] == 3
    assert set(ev["watch_cash_out_channels"]) == {"ATM", "CASH", "CRYPTO"}
    assert "S.168" in rules["legal_basis"]["hold"] and "9.1(i)" in rules["legal_basis"]["hold"]
    assert "Rajasthan" in rules["legal_basis"]["freeze"]
    assert set(rules["legal_basis"]) >= {"hold", "narrow", "freeze", "trail", "release", "send_back"}
    assert rules["today_defaults"]["freezes_whole_account"] is True
    assert rules["watch"]["release_review_days"] == 75


def test_three_banks_each_with_its_own_schema():
    assert {b["schema"] for b in banks.values()} == {"PH_X", "PH_Y", "PH_Z"}
    assert [k for k, b in banks.items() if b["works_tickets"] == "desk"] == ["BANK_Z"]
    assert banks["BANK_Z"]["desk_opens"] == "10:00"


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