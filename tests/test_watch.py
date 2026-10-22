"""The watch (pipeline/watch.py) on the demo night and small hand-made cases. No database (DECISIONS D-38)."""
from decimal import Decimal as D

from pipeline import watch as w
from pipeline.cases import load_case

HOLDS_AT = "2026-10-18 02:16:00"


def demo_night():
    case = load_case()
    txns = [w.Txn(t.txn_id, str(t.ts)[:19], t.from_account, t.to_account, D(str(t.amount_inr)), t.channel,
                  t.is_exit, t.exit_identifier) for t in case.txns]
    watched = {"ACC_A": w.Watched("ACC_A", "A", HOLDS_AT, D("50000"), True, cases=["CASE_DEMO"]),
               "ACC_C": w.Watched("ACC_C", "C", HOLDS_AT, D("29500"), True, cases=["CASE_DEMO"]),
               "ACC_D": w.Watched("ACC_D", "Murugan", HOLDS_AT, D("820000"), False, cases=["CASE_DEMO"])}
    holds = [w.Hold("ACC_A", HOLDS_AT, D("50000"), "LIEN"), w.Hold("ACC_C", HOLDS_AT, D("29500"), "LIEN"),
             w.Hold("ACC_D", HOLDS_AT, D("1000"), "LIEN")]
    return txns, watched, holds


def test_the_demo_night_two_withdrawals_are_refused():
    events = w.replay(*demo_night())
    assert [(e.at[11:16], e.account_id, e.kind, e.amount, e.channel) for e in events] == [
        ("04:30", "ACC_C", "BLOCKED", D("29500"), "ATM"),
        ("05:10", "ACC_A", "BLOCKED", D("50000"), "CRYPTO"),
    ]
    assert all(e.balance == e.held for e in events)          # refused: the money is still there


def test_fixed_policy_alerts_the_officer_on_refused_exits_at_night():
    events = w.decide(w.replay(*demo_night()), {"ACC_A": "A", "ACC_C": "C"}, w.Controller())
    assert [(e.options, e.response, e.chosen_by) for e in events] == [
        (["evidence", "evidence_and_alert"], "ALERT", "pipeline")] * 2
    assert events[0].alert_text.startswith("04:30: C tried to move Rs 29,500 to cash at ATM")


def test_in_desk_hours_there_is_no_choice_to_make():
    e = w.Event("ACC_X", "T1", "2026-10-18 14:01:00", "NEW_MONEY", D("246800"), "VX07", "IMPS", D(0), D("246800"),
                False, ["CASE_X08"])
    w.decide([e], {}, w.Controller())
    assert (e.options, e.response, e.chosen_by, e.alert_text) == (["evidence"], "EVIDENCE", "rule", None)


def test_a_refused_debit_never_reaches_the_next_watched_account():
    t = [w.Txn("T1", "2026-10-18 14:24:00", "M02", "M09", D("1000"), "IMPS", False)]
    watched = {"M02": w.Watched("M02", "Mule 02", "2026-10-18 12:00:00", D("1000"), True),
               "M09": w.Watched("M09", "Mule 09", "2026-10-18 12:00:00", D("0"), True)}
    events = w.replay(t, watched, [w.Hold("M02", "2026-10-18 12:00:00", D("1000"), "LIEN")])
    assert [(e.account_id, e.kind) for e in events] == [("M02", "BLOCKED")]


def test_money_that_is_not_held_moves_and_counts_as_evidence():
    t = [w.Txn("T1", "2026-10-18 14:24:00", "M02", "M09", D("400"), "IMPS", False)]
    watched = {"M02": w.Watched("M02", "Mule 02", "2026-10-18 12:00:00", D("1000"), True),
               "M09": w.Watched("M09", "Mule 09", "2026-10-18 12:00:00", D("0"), True)}
    events = w.replay(t, watched, [w.Hold("M02", "2026-10-18 12:00:00", D("600"), "LIEN")])
    assert [(e.account_id, e.kind, e.balance) for e in events] == [("M02", "MOVED", D("600")),
                                                                    ("M09", "NEW_MONEY", D("400"))]


def test_a_marker_catches_later_credit_up_to_its_cap_and_the_hold_then_bites():
    t = [w.Txn("T1", "2026-10-18 03:10:00", "X", "E", D("15000"), "IMPS", False),
         w.Txn("T2", "2026-10-18 03:20:00", "E", "EXIT:ATM:1", D("15000"), "ATM", True, "ATM 1")]
    watched = {"E": w.Watched("E", "E", "2026-10-18 02:16:00", D("0"), True, marker_cap=D("12000"))}
    events = w.replay(t, watched, [])
    assert [(e.kind, e.hold_amount, e.held) for e in events] == [("MARKER_HOLD", D("12000"), D("12000")),
                                                                  ("BLOCKED", D("0"), D("12000"))]
    w.decide(events, {}, w.Controller())
    assert [e.response for e in events] == ["ALERT", "ALERT"]


def test_a_freeze_refuses_every_debit():
    t = [w.Txn("T1", "2026-10-18 03:00:00", "A", "B", D("1"), "UPI", False)]
    watched = {"A": w.Watched("A", "A", "2026-10-18 02:00:00", D("100"), True)}
    events = w.replay(t, watched, [w.Hold("A", "2026-10-18 02:30:00", D("100"), "ACCOUNT_FREEZE")])
    assert events[0].kind == "BLOCKED"


class FakeModel:
    def __init__(self, pick=None, fail=False, words=None):
        self.pick, self.fail, self.words, self.calls = pick, fail, words, 0

    def pick_tool(self, system, brief, tools):
        self.calls += 1
        if self.fail:
            raise RuntimeError("down")
        return self.pick

    def write(self, system, facts):
        return self.words or facts


def test_the_model_chooses_within_the_allowed_options():
    events = w.decide(w.replay(*demo_night()), {}, w.Controller("llm", FakeModel("evidence")))
    assert [(e.response, e.chosen_by) for e in events] == [("EVIDENCE", "llm")] * 2


def test_an_option_the_rules_do_not_allow_falls_back_to_the_fixed_policy():
    events = w.decide(w.replay(*demo_night()), {}, w.Controller("llm", FakeModel("freeze_the_account")))
    assert [(e.response, e.chosen_by) for e in events] == [("ALERT", "fallback")] * 2


def test_a_failed_model_hands_over_for_the_rest_of_the_watch():
    model = FakeModel(fail=True)
    events = w.decide(w.replay(*demo_night()), {}, w.Controller("llm", model))
    assert model.calls == 1 and [e.chosen_by for e in events] == ["fallback", "fallback"]


def test_model_alert_with_an_invented_amount_is_replaced_by_the_template():
    events = w.decide(w.replay(*demo_night()), {"ACC_C": "C", "ACC_A": "A"},
                      w.Controller("llm", FakeModel("evidence_and_alert", words="C tried to take Rs 99,999.")))
    assert events[0].alert_text.startswith("04:30: C tried to move Rs 29,500")