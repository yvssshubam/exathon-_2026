"""AT-07 (no database): the signals, computed in Python on the same data, give the bands the requirements expect,
and the generated history has the shape each account needs."""
from collections import Counter

import pytest

from pipeline.cases import DEMO_CASE, load_case
from pipeline.gen_history import END, generate
from pipeline.load_cases import CASE_FILES, rows_for
from tests.score_oracle import CONFIG, score, signals

cases = {k: load_case(v) for k, v in CASE_FILES.items()}
demo = load_case(DEMO_CASE)
AS_OF = demo.tool_as_of


@pytest.fixture(scope="module")
def rows():
    return rows_for(cases)


@pytest.mark.parametrize("account,band", [("ACC_A", "MULE"), ("ACC_B", "MULE"), ("ACC_C", "MULE"),
                                          ("ACC_D", "HONEST")])
def test_demo_bands(rows, account, band):                                        # AT-07
    total, got = score(signals(rows, demo, account, AS_OF))
    assert got == band, (account, total)


def test_murugan_looks_like_a_shop(rows):
    v = signals(rows, demo, "ACC_D", AS_OF)
    assert v["account_age_days"] > 3 * 365 - 30
    assert v["pass_through_ratio"] < 0.2 and v["large_payer_share"] < 0.05
    assert v["cash_or_crypto_exits"] == 0 and v["balance_retained_ratio"] > 1000


def test_history_is_deterministic_and_before_the_case_day():
    accounts = [a for c in cases.values() for a in c.accounts]
    h1, h2 = generate(accounts), generate(accounts)
    assert h1 == h2
    assert max(r["ts"] for r in h1) < END.strftime("%Y-%m-%d %H:%M:%S")
    per_day = Counter(r["ts"][:10] for r in h1 if r["to"] == "ACC_D")
    assert 270 <= min(per_day.values()) and max(per_day.values()) <= 330
    exits = Counter(r["from"] for r in h1 if r["to"].startswith("EXIT:"))
    for a in accounts:
        if a.get("history") == "fast_pass_through":
            assert exits[a["account_id"]] >= 1, a["account_id"]


def test_config_signals_are_the_eight_in_the_requirements():
    assert set(CONFIG["signals"]) == {
        "account_age_days", "median_dwell_minutes", "pass_through_ratio", "complaint_linked_credits_7d",
        "large_payer_share", "balance_retained_ratio", "cash_or_crypto_exits", "credit_vs_profile"}
    for s in CONFIG["signals"].values():
        assert "{" not in s["mule_sentence"].replace("{v}", "").replace("{v1}", "").replace("{pct}", "").replace(
            "{years}", "")