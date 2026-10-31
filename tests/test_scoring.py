"""Account scoring v2 (DECISIONS D-57 to D-59), no database: the five demo accounts of the scoring doc land in their
types and get their actions, the demo night's accounts get the types its story needs, the edge cases hold, and the
generated history still has the shape each account needs."""
from collections import Counter
from datetime import datetime
from itertools import product

import pytest

from pipeline import scoring
from pipeline.cases import DEMO_CASE, PROACTIVE_CASE, load_case
from pipeline.gen_history import END, generate
from pipeline.load_cases import CASE_FILES, rows_for
from tests.score_oracle import CONFIG, classify, match_credits, signals, speed

cases = {k: load_case(v) for k, v in CASE_FILES.items()}
demo = load_case(DEMO_CASE)
pro = load_case(PROACTIVE_CASE)
AS_OF = demo.tool_as_of


@pytest.fixture(scope="module")
def rows():
    return rows_for(cases)


# ---------------------------------------------------------------- the five demo accounts (the scoring doc's Tests)

@pytest.mark.parametrize("name", sorted(scoring.DEMO_ACCOUNTS))
def test_demo_account_lands_in_its_type(name):
    acc = scoring.DEMO_ACCOUNTS[name]
    assert scoring.classify(acc)["account_type"] == acc["expected_type"], name


def test_the_worked_examples_add_up():
    mule = scoring.classify(scoring.DEMO_ACCOUNTS["classic_mule"])
    assert mule["pass_through"] == pytest.approx(0.998, abs=0.001)
    assert mule["fraud_exposure"] == pytest.approx(0.642, abs=0.001)
    pump = scoring.classify(scoring.DEMO_ACCOUNTS["murugan_fuel_pump"])
    assert pump["pass_through"] == 0 and pump["fraud_exposure"] == 0


def test_demo_accounts_get_their_actions():
    a = scoring.DEMO_ACCOUNTS
    mule = scoring.classify(a["classic_mule"])
    assert mule["proactive"] and not mule["shrink"]
    assert scoring.classify({**a["classic_mule"], "refused_withdrawal": True})["freeze"] == "STRONG"
    for name in ("murugan_fuel_pump", "salaried_person_who_sold_a_phone"):
        c = scoring.classify(a[name])
        assert not c["proactive"] and c["shrink"] and c["freeze"] is None, name
    jeweller = scoring.classify(a["jeweller_helping_the_ring"])
    assert jeweller["leaves_fast"] and jeweller["ladder"] == "STRONG" and jeweller["freeze"] == "STRONG"
    honest = scoring.classify(a["honest_fuel_pump_near_the_ring"])
    assert not honest["leaves_fast"] and honest["freeze"] is None and not honest["shrink"]


# ---------------------------------------------------------------- the demo night

@pytest.mark.parametrize("account,at,attribution,want", [
    ("ACC_A", "2026-10-18 02:16:00", True, "FAST_MOVER"),           # CASE_DEMO's Layer-1 account
    ("ACC_C", "2026-10-18 02:16:00", True, "FAST_MOVER"),           # a refused withdrawal at 04:30: Strong
    ("ACC_D", "2026-10-18 02:16:00", True, "ORDINARY_CUSTOMER"),    # Murugan: shrink to the victim's Rs 500
    ("ACC_A", "2026-10-18 05:45:00", False, "FAST_MOVER"),          # when Ravi's money lands
])
def test_demo_case_types(rows, account, at, attribution, want):
    case = demo
    got = classify(signals(rows, case, account, at, attribution))
    assert got["account_type"] == want, (account, got)


@pytest.mark.parametrize("account,at,attribution,want", [
    ("ACC_P10", "2026-10-18 00:48:00", True, "GREY_ZONE"),        # one complaint: the officer decides, no auto hold
    ("ACC_P10", "2026-10-18 00:50:00", False, "GREY_ZONE"),       # victim P2's money lands: nothing held on arrival
    ("ACC_P10", "2026-10-18 01:08:00", True, "PIPE_FOR_FRAUD"),   # the second complaint
    ("ACC_P10", "2026-10-18 02:41:00", False, "PIPE_FOR_FRAUD"),  # scenario A
    ("ACC_P10", "2026-10-18 03:15:00", False, "PIPE_FOR_FRAUD"),  # scenario C
    ("ACC_P10", "2026-10-18 03:50:00", False, "PIPE_FOR_FRAUD"),  # scenario D
    ("ACC_P20", "2026-10-18 01:53:00", True, "FAST_MOVER"),
    ("ACC_P20", "2026-10-18 04:20:00", False, "FAST_MOVER"),      # scenario B
    ("ACC_P20", "2026-10-18 05:05:00", False, "FAST_MOVER"),      # scenario F
])
def test_proactive_case_types(rows, account, at, attribution, want):
    got = classify(signals(rows, pro, account, at, attribution))
    assert got["account_type"] == want, (account, at, got)


def test_murugan_looks_like_a_shop(rows):
    v = signals(rows, demo, "ACC_D", AS_OF)
    assert v["passed_on_within_hour"] < 0.2 and v["top3_payer_share"] < 0.05
    assert v["cashed_out_within_hour"] == 0 and v["balance_kept_ratio"] > 1000


# ---------------------------------------------------------------- edge cases

def _pay(ts, frm, to, amt, ch, tid=None):
    return dict(id=tid or f"{frm}>{to}@{ts}", ts=datetime.strptime(ts, "%Y-%m-%d %H:%M:%S"), f=frm, t=to,
                amt=float(amt), ch=ch, exit=ch in ("ATM", "CASH", "CRYPTO"))


def test_one_debit_is_never_matched_to_two_credits():
    m = match_credits()
    events = [{"direction": "C", "ts": "2026-10-01 10:00:00", "txn_id": "c1", "amount_paise": 1000, "exit_kind": "NONE",
               "fraud_linked": False},
              {"direction": "C", "ts": "2026-10-01 10:10:00", "txn_id": "c2", "amount_paise": 1000, "exit_kind": "NONE",
               "fraud_linked": True},
              {"direction": "D", "ts": "2026-10-01 10:20:00", "txn_id": "d1", "amount_paise": 1500, "exit_kind": "CASH",
               "fraud_linked": False}]
    got = {c["txn_id"]: c for c in m.match(events)}
    assert got["c1"]["out_paise"] == 1000 and got["c2"]["out_paise"] == 500      # the earliest credit first
    assert sum(c["out_paise"] for c in got.values()) == 1500                      # never more than the debit
    assert got["c2"]["cash_paise"] == 500


def test_a_debit_more_than_an_hour_later_is_not_matched():
    m = match_credits()
    got = m.match([{"direction": "C", "ts": "2026-10-01 10:00:00", "txn_id": "c", "amount_paise": 1000,
                    "exit_kind": "NONE", "fraud_linked": False},
                   {"direction": "D", "ts": "2026-10-01 11:00:01", "txn_id": "d", "amount_paise": 1000,
                    "exit_kind": "MOVE", "fraud_linked": False}])
    assert got[0]["out_paise"] == 0


def test_a_shop_that_withdraws_cash_daily_but_not_after_credits_scores_low_on_p3():
    now = datetime(2026, 10, 18)
    credits = [_pay(f"2026-10-{d:02d} {h:02d}:00:00", f"EXT_{d}_{h}", "SHOP", 500, "UPI")
               for d in range(1, 15) for h in (9, 11, 13)]
    debits = [_pay(f"2026-10-{d:02d} 20:30:00", "SHOP", "EXIT:ATM:1", 1000, "ATM") for d in range(1, 15)]
    matched = match_credits().match(
        [{"direction": "C", "ts": c["ts"], "txn_id": c["id"], "amount_paise": 50000, "exit_kind": "NONE",
          "fraud_linked": False} for c in credits] +
        [{"direction": "D", "ts": d["ts"], "txn_id": d["id"], "amount_paise": 100000, "exit_kind": "CASH",
          "fraud_linked": False} for d in debits])
    assert sum(c["cash_paise"] for c in matched) == 0
    assert speed(credits, debits, now)[1] == 0


def test_automatic_payments_do_not_count_as_money_moving_on():                  # D-54
    """A salary lands and the EMI and SIP leave by NACH within minutes: that is not money being passed on."""
    now = datetime(2026, 10, 18)
    credits, debits = [], []
    for month in (7, 8, 9):
        credits.append(_pay(f"2026-{month:02d}-01 09:00:00", "EXT_EMPLOYER", "ACC_SAL", 50000, "NEFT"))
        debits.append(_pay(f"2026-{month:02d}-01 09:05:00", "ACC_SAL", "EXT_HOME_LOAN", 20000, "NACH"))
        debits.append(_pay(f"2026-{month:02d}-01 09:10:00", "ACC_SAL", "EXT_MUTUAL_FUND", 5000, "AUTOPAY"))
        debits.append(_pay(f"2026-{month:02d}-20 19:00:00", "ACC_SAL", "EXT_GROCER", 8000, "UPI"))
    dwell, passed = speed(credits, debits, now)
    assert passed == 0 and dwell > 6 * 60
    by_hand = [{**d, "ch": "IMPS"} for d in debits]
    dwell2, passed2 = speed(credits, by_hand, now)
    assert passed2 == 0.5 and dwell2 == 5


def test_nach_salary_raises_neither_p5_nor_e3():
    """A salaried account: its top payer is its employer, paid by NACH. Left out of P5 and E3 (both shares)."""
    sql = (scoring.ROOT / "sql" / "steps" / "060_score.sql").read_text(encoding="utf-8")
    assert sql.count("AND t5.channel <> 'NACH'") == 1 and sql.count("AND t8.channel <> 'NACH'") == 1
    assert CONFIG["pass_through"]["top3_payer_share"]["skip_nach_credits"]
    assert CONFIG["fraud_exposure"]["money_from_flagged_share"]["skip_nach_credits"]


def test_automatic_channels_match_the_sql():                                    # D-54
    sql = (scoring.ROOT / "sql" / "steps" / "060_score.sql").read_text(encoding="utf-8")
    assert "d1.channel NOT IN ('NACH', 'AUTOPAY')" in sql                         # P1
    assert "WHEN d.channel IN ('NACH', 'AUTOPAY')       THEN 'AUTO'" in sql      # P2, P3, E4: never matched


def test_missing_data_never_raises_a_score():
    nothing = {"values": {}, "fraud_out_share": None, "other_out_share": None, "cases": 0, "fraud_money_share": None}
    c = scoring.classify(nothing)
    assert c["pass_through"] == 0 and c["fraud_exposure"] == 0 and c["account_type"] == "ORDINARY_CUSTOMER"
    for name, acc in scoring.DEMO_ACCOUNTS.items():
        full = scoring.classify(acc)
        for key in acc["values"]:
            gap = scoring.classify({**acc, "values": {k: v for k, v in acc["values"].items() if k != key}})
            assert gap["pass_through"] <= full["pass_through"] + 1e-12, (name, key)
            assert gap["fraud_exposure"] <= full["fraud_exposure"] + 1e-12, (name, key)


def test_a_large_business_with_little_fraud_money_stops_at_weak():
    biz = {**scoring.DEMO_ACCOUNTS["jeweller_helping_the_ring"], "fraud_money_share": 0.01}
    assert scoring.classify(biz)["ladder"] == "WEAK" and scoring.classify(biz)["freeze"] is None


def test_nodal_pool_escrow_never_strong_and_never_an_automatic_hold():
    for name, acc in scoring.DEMO_ACCOUNTS.items():
        c = scoring.classify({**acc, "never_full": True, "refused_withdrawal": True})
        assert c["freeze"] != "STRONG" and not c["proactive"], name
        assert c["ladder"] != "STRONG", name


def test_the_grey_zone_triggers_nothing_automatic():
    for pt, fe in product((0.45, 0.5, 0.59), (0.1, 0.45, 0.7)):
        assert scoring.account_type(pt, fe) == "GREY_ZONE"
    s = scoring.suggestions("GREY_ZONE", "STRONG")
    assert not s["proactive"] and not s["shrink"] and s["freeze"] is None


def test_no_account_gets_both_a_shrink_and_a_freeze_suggestion():
    for pattern, ladder, never, refused in product(list(scoring.TYPE_NAMES), ("NONE", "WEAK", "MEDIUM", "STRONG"),
                                                   (False, True), (False, True)):
        s = scoring.suggestions(pattern, ladder, never, refused)
        assert not (s["shrink"] and s["freeze"]), (pattern, ladder, never, refused)


def test_e4_needs_both_the_share_and_the_gap():
    assert scoring.fraud_money_leaves_fast(0.62, 0.08)
    assert not scoring.fraud_money_leaves_fast(0.62, 0.40)        # other money leaves fast too: a 22-point gap
    assert not scoring.fraud_money_leaves_fast(0.45, 0.0)
    assert scoring.ladder_step(True, 1, 0.3) == "WEAK"
    assert scoring.ladder_step(True, 2.5, 0.3) == "MEDIUM"
    assert scoring.ladder_step(True, 3, 0.3) == "STRONG"
    assert scoring.ladder_step(False, 9, 0.3) == "NONE"            # the count alone never starts a suggestion


def test_type_names_never_say_mule_or_criminal():
    from pipeline.reasons import VERDICT

    for name in scoring.TYPE_NAMES.values():
        assert not VERDICT.search(name)
    for score_name in ("pass_through", "fraud_exposure"):
        for sig in CONFIG[score_name].values():
            for key in ("high_sentence", "low_sentence"):
                assert not VERDICT.search(sig[key]), sig[key]
                assert "{" not in sig[key].replace("{v}", "").replace("{v1}", "").replace("{pct}", "")


def test_config_has_the_nine_signals_and_start_weights_sum_to_one():
    assert set(CONFIG["pass_through"]) == {"median_dwell_minutes", "passed_on_within_hour", "cashed_out_within_hour",
                                           "balance_kept_ratio", "top3_payer_share"}
    assert set(CONFIG["fraud_exposure"]) == {"direct_complaints_7d", "trail_touches_90d", "money_from_flagged_share",
                                             "pays_back_its_payers"}
    for score_name in ("pass_through", "fraud_exposure"):
        assert sum(s["start_weight"] for s in CONFIG[score_name].values()) == pytest.approx(1.0)
        for s in CONFIG[score_name].values():
            assert s["low_at"] != s["high_at"]


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