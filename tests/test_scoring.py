"""AT-07 (no database): the signals, computed in Python on the same data, give the bands the requirements expect,
and the generated history has the shape each account needs."""
from collections import Counter
from datetime import datetime

import pytest

from pipeline.cases import DEMO_CASE, load_case
from pipeline.gen_history import END, generate
from pipeline.load_cases import CASE_FILES, rows_for
from tests.score_oracle import CONFIG, declared_ratio, score, signals, speed

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


def _pay(ts, frm, to, amt, ch):
    return dict(ts=datetime.strptime(ts, "%Y-%m-%d %H:%M:%S"), f=frm, t=to, amt=float(amt), ch=ch)


def test_automatic_payments_do_not_count_as_money_moving_on():                  # D-54
    """A salary lands and the EMI and SIP leave by NACH within minutes: that is not a mule forwarding money."""
    now = datetime(2026, 10, 18)
    credits, debits = [], []
    for month in (7, 8, 9):
        credits.append(_pay(f"2026-{month:02d}-01 09:00:00", "EXT_EMPLOYER", "ACC_SAL", 50000, "NEFT"))
        debits.append(_pay(f"2026-{month:02d}-01 09:05:00", "ACC_SAL", "EXT_HOME_LOAN", 20000, "NACH"))
        debits.append(_pay(f"2026-{month:02d}-01 09:10:00", "ACC_SAL", "EXT_MUTUAL_FUND", 5000, "AUTOPAY"))
        debits.append(_pay(f"2026-{month:02d}-20 19:00:00", "ACC_SAL", "EXT_GROCER", 8000, "UPI"))
    dwell, passed = speed(credits, debits, now)
    assert passed == 0                                   # the EMI and SIP are not counted as passed on
    assert dwell > 6 * 60                                # money stays until the grocery bill, days later
    # the same payments sent by hand (IMPS) to other accounts would look like forwarding
    by_hand = [{**d, "ch": "IMPS"} for d in debits]
    dwell2, passed2 = speed(credits, by_hand, now)
    assert passed2 == 0.5 and dwell2 == 5


def test_automatic_channels_match_the_sql():                                    # D-54
    from tests.score_oracle import AUTOMATIC_CHANNELS, ROOT
    clause = "d.channel NOT IN (" + ", ".join(f"'{c}'" for c in AUTOMATIC_CHANNELS) + ")"
    for f in ("sql/steps/060_score.sql", "sql/learn/010_features.sql"):
        assert (ROOT / f).read_text(encoding="utf-8").count(clause) == 2, f   # dwell and pass-through



def test_money_from_companies_by_nach_does_not_count_as_a_jump():              # D-55
    """A salaried customer declared low (Rs 50,000 a month). A mutual fund pays out Rs 3,00,000 by NACH:
    that is not the sudden jump a rented account shows. The same money sent by UPI from people would be."""
    now = datetime(2026, 10, 18)
    expected = CONFIG["profile_band_expected_monthly_credit_inr"]["low"]
    credits = [_pay("2026-10-01 09:00:00", "EXT_EMPLOYER", "ACC_SAL", 45000, "NACH"),
               _pay("2026-10-10 11:00:00", "EXT_MUTUAL_FUND", "ACC_SAL", 300000, "NACH"),
               _pay("2026-10-12 18:00:00", "EXT_FRIEND", "ACC_SAL", 2000, "UPI")]
    assert declared_ratio(credits, now, expected) == 2000 / expected
    from_people = [{**c, "ch": "UPI"} for c in credits]
    assert declared_ratio(from_people, now, expected) == 347000 / expected     # almost 7 times: a fraud sign


def test_nach_credits_left_out_in_the_sql():                                   # D-55
    from tests.score_oracle import ROOT
    for f in ("sql/steps/060_score.sql", "sql/learn/010_features.sql"):
        assert (ROOT / f).read_text(encoding="utf-8").count("AND t.channel <> 'NACH'") == 1, f