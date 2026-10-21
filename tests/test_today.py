"""Today's desk timing for the Compare screen (no database)."""
from pipeline.cases import load_case
from pipeline.today import desk_opens_after, desk_settings, plan

demo = load_case()


def test_demo_desk_reaches_a_c_d_from_10_20():
    s = desk_settings("CASE_DEMO")
    times = plan("2026-10-18 02:13:00", [("ACC_D", 3), ("ACC_A", 1), ("ACC_C", 2)], s)
    assert times == {"ACC_A": "2026-10-18 10:20:00", "ACC_C": "2026-10-18 10:35:00",
                     "ACC_D": "2026-10-18 10:50:00"}


def test_minutes_to_first_desk_action_match_the_case_file():
    s = desk_settings("CASE_DEMO")
    first = plan("2026-10-18 02:13:00", [("ACC_A", 1)], s)["ACC_A"]
    assert first == "2026-10-18 10:20:00"
    assert (10 * 60 + 20) - (2 * 60 + 13) == demo.expected_compare["minutes_ticket_to_first_hold"]["today"]


def test_desk_hours():
    assert str(desk_opens_after("2026-10-18 02:13:00", "10:00"))[:16] == "2026-10-18 10:00"
    assert str(desk_opens_after("2026-10-18 11:47:00", "10:00"))[:16] == "2026-10-18 11:47"   # open: now
    assert str(desk_opens_after("2026-10-18 21:30:00", "10:00"))[:16] == "2026-10-19 10:00"   # next morning