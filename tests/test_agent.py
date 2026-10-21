"""The agent's control flow (no database): fixed order, the checker as a hard gate, the model as an optional
orchestrator that the fixed order replaces when it fails, and grounded wording."""
import pytest

from pipeline.agent import CHANGE_TOOLS, Agent, State, ToolResult, send_back_template, summary_template
from pipeline.llm import LLMUnavailable, amounts, numbers_are_grounded

FACTS = {"case_id": "CASE_DEMO", "received_at": "2026-10-18 02:13:00", "utr": "IMPS2026101801520001",
         "paid": 200000, "problems": "", "holds": [{"name": "A", "amount": 50000}, {"name": "C", "amount": 29500},
                                                   {"name": "Murugan", "amount": 1000}],
         "markers": [], "exits": [{"victim": 50000, "where": "ATM BX-ATM-0042, Katpadi"}],
         "onward": [{"victim": 70000, "bank": "Bank Y"}], "kyc": 3,
         "waiting": [{"name": "Murugan", "headline": "Narrow the hold to Rs 500?"}], "check_failed": []}


class FakeBackend:
    def __init__(self, ticket_ok=True, check_ok=True):
        self.ticket_ok, self.check_ok = ticket_ok, check_ok
        self.calls, self.log, self.status, self.summary = [], [], None, None

    def start_run(self, mode, model):
        self.mode = mode

    def log_step(self, step_no, tool, chosen_by, can_change, result, ms):
        self.log.append((tool, chosen_by))

    def finish(self, status):
        self.status = status

    def __getattr__(self, name):                      # every analysis and change tool
        def tool():
            self.calls.append(name)
            if name == "read_ticket":
                return ToolResult("ok", "1", {"ok": self.ticket_ok, "problems": "" if self.ticket_ok else "no amount"})
            return ToolResult(name, "2")
        return tool

    def check(self):
        self.calls.append("check")
        return ToolResult("checks", "3", {"passed": self.check_ok})

    def summary_facts(self):
        f = dict(FACTS, problems="" if self.ticket_ok else "no amount")
        f["text"] = summary_template(f) if self.ticket_ok else send_back_template(f)
        return f

    def write_case_summary(self, text):
        self.calls.append("write_case_summary")
        self.summary = text
        return ToolResult(text)

    def send_back_for_correction(self, text):
        self.calls.append("send_back_for_correction")
        self.summary = text
        return ToolResult(text)


PIPELINE = ["read_ticket", "trace_money", "attribute_victim_share", "get_account_evidence", "allowed_actions",
            "check", "place_mandatory_hold", "record_exit", "draft_onward_entry", "draft_kyc_packet",
            "escalate_to_officer", "write_case_summary"]


def test_fixed_order_works_the_whole_ticket():
    b = FakeBackend()
    assert Agent(b).run() == "DONE"
    assert b.calls == PIPELINE
    assert ("checker", "runtime") in b.log


def test_a_failed_check_means_nothing_changes():
    b = FakeBackend(check_ok=False)
    assert Agent(b).run() == "CHECK_FAILED"
    assert not set(CHANGE_TOOLS) & set(b.calls)
    assert b.calls[-2:] == ["escalate_to_officer", "write_case_summary"]


def test_an_incomplete_notice_is_sent_back_not_frozen():
    b = FakeBackend(ticket_ok=False)
    assert Agent(b).run() == "SENT_BACK"
    assert b.calls == ["read_ticket", "send_back_for_correction", "write_case_summary"]
    assert "nothing was frozen" in b.summary


def test_change_tools_are_never_offered_before_the_checker():
    s = State(ticket_ok=True, done=["read_ticket", "trace_money", "attribute_victim_share",
                                    "get_account_evidence", "allowed_actions"])
    assert not set(CHANGE_TOOLS) & set(s.available())
    s.check_passed = True
    assert set(s.available()) >= {"place_mandatory_hold", "record_exit", "draft_onward_entry"}
    assert "draft_kyc_packet" not in s.available()           # the packet follows the holds


class Model:
    """A scripted local model."""

    model = "fake"

    def __init__(self, picks=None, fail=False, words=None):
        self.picks, self.fail, self.words = list(picks or []), fail, words

    def pick_tool(self, system, state, tools):
        if self.fail:
            raise LLMUnavailable("connection refused")
        offered = [t["function"]["name"] for t in tools]
        return self.picks.pop(0) if self.picks else offered[-1]

    def write(self, system, facts):
        if self.words is None:
            raise LLMUnavailable("no words")
        return self.words


def test_model_can_reorder_within_the_rules():
    b = FakeBackend()
    assert Agent(b, "llm", Model()).run() == "DONE"       # always picks the last offered tool
    assert set(b.calls) == set(PIPELINE)
    assert b.calls.index("check") < min(b.calls.index(t) for t in CHANGE_TOOLS)
    assert b.calls[-1] == "write_case_summary"


def test_model_asking_for_a_forbidden_tool_is_overruled():
    b = FakeBackend()
    Agent(b, "llm", Model(picks=["place_mandatory_hold"])).run()
    assert b.calls[0] == "read_ticket"
    assert b.log[0][0] == "model" and b.log[1] == ("read_ticket", "fallback")


def test_unreachable_model_hands_over_to_the_fixed_order():
    b = FakeBackend()
    assert Agent(b, "llm", Model(fail=True)).run() == "DONE"
    assert b.calls == PIPELINE
    assert all(by in ("fallback", "runtime") for _, by in b.log)


def test_model_words_with_invented_numbers_are_thrown_away():
    b = FakeBackend()
    Agent(b, "llm", Model(words="Held Rs 80,000 in total overnight.")).run()   # 80,000 is a sum it made up
    assert b.summary == summary_template(b.summary_facts())


def test_model_words_with_grounded_numbers_are_kept():
    b = FakeBackend()
    Agent(b, "llm", Model(words="Overnight Rs 50,000 on A and Rs 29,500 on C were held.")).run()
    assert b.summary.startswith("Overnight")


def test_summary_template_reads_like_the_morning_review():
    text = summary_template(FACTS)
    assert text.startswith("At 02:13 the ticket for Rs 2,00,000")
    assert "Rs 50,000 on A, Rs 29,500 on C and Rs 1,000 on Murugan" in text
    assert "Rs 70,000 to Bank Y" in text and "Waiting for you: Narrow the hold to Rs 500? (Murugan)" in text


@pytest.mark.parametrize("text,expected", [("Rs 1,000 and ₹500.00", {"1000", "500"}), ("no money", set()),
                                           ("Rs 2,00,000.50", {"200000.5"})])
def test_amounts_are_normalised(text, expected):
    assert amounts(text) == expected


def test_grounding():
    assert numbers_are_grounded("held Rs 500", "proposal Rs 500")
    assert not numbers_are_grounded("held Rs 501", "proposal Rs 500")