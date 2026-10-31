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


def test_the_officer_decides_the_judgment_calls_only():
    # PROPOSE_PRE_ALERT is retired (D-64) but kept so old proposals stay readable; PROPOSE_LARGER_HOLD is the freeze
    # ladder's Medium step (D-59). The proactive holds' decisions (confirm, release, keep and refer) go through the
    # checker as their own actions (pipeline/proactive.py).
    assert set(rules["officer"]) == {"PROPOSE_NARROW", "PROPOSE_LARGER_HOLD", "PROPOSE_FULL_FREEZE", "UNMATCHED_NOTICE",
                                     "PROPOSE_RELEASE", "PROPOSE_PRE_ALERT"}


def test_full_freeze_evidence_and_legal_bases():
    ev = rules["full_freeze_evidence"]
    assert set(ev["refused_withdrawal_types"]) == {"PIPE_FOR_FRAUD", "FAST_MOVER"}
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


def test_scoring_v2_weights_cutoffs_and_ladder():
    for score_name in ("pass_through", "fraud_exposure"):
        assert abs(sum(s["start_weight"] for s in scoring[score_name].values()) - 1.0) < 1e-9
        for name, s in scoring[score_name].items():
            assert s["low_at"] != s["high_at"], name
            assert s["high_sentence"] and s["low_sentence"], name
    assert len(scoring["pass_through"]) == 5 and len(scoring["fraud_exposure"]) == 4
    assert scoring["cutoffs"] == {"high_at": 0.60, "low_below": 0.40}
    assert scoring["window_days"] == 90
    assert set(scoring["never_account_hold_types"]) == {"nodal", "pool", "escrow"}
    assert scoring["weight_learning"]["weight_min"] == 0.05 and scoring["weight_learning"]["weight_max"] == 0.50
    assert scoring["freeze_ladder"]["strong_at_cases"] == 3 and scoring["freeze_ladder"]["medium_at_cases"] == 2


def test_proactive_rules_match_the_brief():
    p = rules["proactive"]
    assert p["proactive_hold_mode"] in ("rbi_draft", "current_sop")
    assert p["min_amount"] == 1000 and p["new_payer_days"] == 90
    assert p["fast_mover_size_percentile"] == 90 and p["fast_mover_recent_hours"] == 24
    assert (p["explanation_days"], p["decide_days"], p["decide_after_explanation_days"], p["limit_days"]) == (
        20, 30, 10, 60)
    assert set(p["eligible_types"]) == {"PIPE_FOR_FRAUD", "FAST_MOVER"}
    assert {k for k, v in rules["account_types"].items() if v["proactive"]} == {"PIPE_FOR_FRAUD", "FAST_MOVER"}