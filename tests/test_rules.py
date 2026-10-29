"""The rules engine and the checker, in plain Python on the case files (no database).
The SQL (070_actions.sql, 075_check.sql) is compared with the same oracle in tests/test_db_agent.py."""
import pytest

from pipeline.cases import DEMO_CASE, EXTRA_CASES, bank_view, load_case, paise
from pipeline.load_cases import CASE_FILES, rows_for
from pipeline.steps import default_as_of
from tests.rules_oracle import RULES, actions, check, facts, trace

demo = load_case(DEMO_CASE)


@pytest.fixture(scope="module")
def rows():
    return rows_for({k: load_case(v) for k, v in CASE_FILES.items()})


@pytest.fixture(scope="module")
def demo_actions(rows):
    f = facts(demo, rows, "BANK_X")
    return f, actions(f)


def test_trace_at_2_16_sees_four_hops_one_bank_y_one_exit():
    edges = trace(bank_view(demo, "BANK_X"), demo.complaints[0], demo.tool_as_of, own_bank="BANK_X")
    got = {t.to_account: hop for t, hop in edges.values()}
    assert got == {"ACC_A": 1, "ACC_B": 2, "ACC_C": 2, "ACC_D": 3, "EXIT:ATM:BX-ATM-0042": 3}


def test_bank_x_never_follows_the_money_inside_bank_y():
    world = trace(demo, demo.complaints[0], demo.tool_as_of)
    assert "T_DEMO_08" in world                              # the narrator sees B's hop to G at Bank Z
    x = trace(bank_view(demo, "BANK_X"), demo.complaints[0], demo.tool_as_of, own_bank="BANK_X")
    assert "T_DEMO_08" not in x                              # Bank X does not: it files the onward entry instead


def test_cash_outs_after_the_ticket_are_not_on_the_trail_yet():
    edges = trace(demo, demo.complaints[0], demo.tool_as_of)
    assert "T_DEMO_06" not in edges and "T_DEMO_07" not in edges


def test_automatic_actions_match_the_case_file(demo_actions):
    _, acts = demo_actions
    auto = {a["account_id"]: a for a in acts if a["mode"] == "AUTOMATIC"}
    for acc, want in demo.expected_actions.items():
        assert auto[acc]["action_code"] == want["automatic"], acc
        assert auto[acc]["amount"] == paise(want["amount_inr"]), acc


def test_murugan_gets_a_narrowing_proposal_not_a_freeze(demo_actions):
    _, acts = demo_actions
    d = [a for a in acts if a["account_id"] == "ACC_D" and a["mode"] == "OFFICER"]
    assert [(a["action_code"], a["amount"]) for a in d] == [("PROPOSE_NARROW", paise(500))]


def test_no_freeze_at_the_first_pass_one_ticket_is_not_repeated_credits(demo_actions):
    # A and C look like pass-through accounts, but one ticket is not the repeated pattern courts accept: a freeze
    # on C comes later, from the watch, when C tries to cash out (tests/test_db_night.py)
    _, acts = demo_actions
    assert not [a for a in acts if a["action_code"] == "PROPOSE_FULL_FREEZE"]
    assert not any(a["mode"] == "AUTOMATIC" and "FREEZE" in a["action_code"] for a in acts)


def test_one_decision_waits_at_the_first_pass(demo_actions):
    _, acts = demo_actions
    assert [(a["account_id"], a["action_code"]) for a in acts if a["mode"] == "OFFICER"] == [
        ("ACC_D", "PROPOSE_NARROW")]


def test_every_check_passes_on_the_demo(demo_actions):
    f, acts = demo_actions
    assert check(demo, f, acts) == dict.fromkeys(check(demo, f, acts), True)


def test_checker_catches_a_hold_above_the_balance(demo_actions):
    f, acts = demo_actions
    bad = [dict(a, amount=a["amount"] + 1) if a["action_code"] == "MANDATORY_HOLD" else a for a in acts]
    assert check(demo, f, bad)["holds_within_balance"] is False


def test_checker_catches_an_automatic_freeze(demo_actions):
    f, acts = demo_actions
    bad = acts + [{"account_id": "ACC_C", "action_code": "PROPOSE_FULL_FREEZE", "mode": "AUTOMATIC", "amount": 0}]
    assert check(demo, f, bad)["automatic_allowed"] is False


def test_checker_catches_lost_money(demo_actions):
    f, acts = demo_actions
    broken = {k: dict(v, victim_everywhere=v["victim_everywhere"] - 1) for k, v in f.items()}
    assert check(demo, broken, acts)["conservation"] is False


def test_rules_never_hold_another_banks_account(demo_actions):
    f, acts = demo_actions
    assert all(f[a["account_id"]]["is_own"] for a in acts if a["action_code"] in ("MANDATORY_HOLD", "HOLD_MARKER"))


def test_extra_cases_pass_the_checker(rows):
    extra = load_case(EXTRA_CASES)
    for c in extra.complaints:
        bank = extra.bank_of(c["beneficiary_account"])
        f = facts(extra, rows, bank, c, default_as_of(c["reported_at"]), evidence=False)
        acts = actions(f)
        assert all(check(extra, f, acts, c).values()), c["case_id"]
        assert any(a["action_code"] == "MANDATORY_HOLD" for a in acts) or all(
            v["hold"] == 0 for v in f.values()), c["case_id"]


def test_rules_config_names_every_action():
    assert set(RULES["automatic"]) == {"MANDATORY_HOLD", "HOLD_MARKER", "RECORD_EXIT", "ONWARD_ENTRY", "KYC_PACKET",
                                       "SEND_BACK", "LINK_DUPLICATE", "RECORD_SPLIT", "WATCH_HOLD", "WATCH_ADD"}
    assert set(RULES["officer"]) == {"PROPOSE_NARROW", "PROPOSE_FULL_FREEZE", "UNMATCHED_NOTICE", "PROPOSE_RELEASE",
                                     "PROPOSE_PRE_ALERT"}


def test_holds_never_exceed_the_reported_amount(demo_actions):
    f, acts = demo_actions
    assert all(a["amount"] <= f[a["account_id"]]["reported"] for a in acts if a["action_code"] == "MANDATORY_HOLD")


def test_a_second_victim_on_account_a_gets_its_own_hold_from_the_money_already_there(rows):
    v2 = demo.complaint("CASE_DEMO_V2")
    f = facts(demo, rows, "BANK_X", v2, default_as_of(v2["reported_at"]), evidence=False)
    acts = actions(f)
    assert [(a["account_id"], a["action_code"], a["amount"]) for a in acts if a["mode"] == "AUTOMATIC"] == [
        ("ACC_A", "MANDATORY_HOLD", paise(40000))]
    assert f["ACC_A"]["victim"] == paise(40000)
    assert not any("FREEZE" in k for k in RULES["automatic"])