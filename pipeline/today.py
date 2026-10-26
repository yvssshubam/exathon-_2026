"""Today's process vs the agent, on the same ticket and the same transactions (the Compare screen, FR-14).

Today: nothing happens until the desk opens; then the desk reaches each own-bank account on the trail in turn and
freezes the whole account (the over-freeze incentive), and types the onward entries by hand. With the agent: the
holds the rules require land minutes after the ticket, at any hour; judgment calls wait for the officer.

The numbers are computed in Exasol: sql/steps/090_today.sql replays the money (same UDF as step 2) up to the moment
today's desk would reach each account. Settings: data/demo_case.yaml `today:` overrides config/rules.yaml
`today_defaults` for the demo case.

    python -m pipeline.today CASE_DEMO
"""
from __future__ import annotations

import sys
from datetime import datetime, timedelta
from decimal import Decimal
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
RULES = yaml.safe_load((ROOT / "config" / "rules.yaml").read_text(encoding="utf-8"))
FMT = "%Y-%m-%d %H:%M:%S"


def desk_settings(case_id: str) -> dict:
    """The demo case file can describe its own desk; everything else uses the defaults."""
    out = dict(RULES["today_defaults"])
    from pipeline.cases import DEMO_CASE, load_case

    demo = load_case(DEMO_CASE)
    if case_id == demo.case_id and demo.today:
        out.update(demo.today)
    return out


def desk_opens_after(received_at: str, opens: str) -> datetime:
    """When today's desk first looks at a ticket that arrived at received_at (same day if the desk is not open yet,
    else straight away; a ticket after hours waits for the next opening)."""
    got = datetime.strptime(received_at[:19], FMT)
    if len(opens) > 5:                              # a full timestamp in the case file
        return max(got, datetime.strptime(opens[:19], FMT))
    hh, mm = (int(x) for x in opens.split(":"))
    today_open = got.replace(hour=hh, minute=mm, second=0)
    if got < today_open:
        return today_open
    if got.hour < 18:                               # desk open: picks it up now
        return got
    return today_open + timedelta(days=1)


def plan(received_at: str, accounts: list[tuple[str, int]], s: dict) -> dict[str, str]:
    """Action time per own-bank account: desk opening + first lookup, then per_account_minutes for each next one."""
    t = desk_opens_after(received_at, str(s["desk_opens"])) + timedelta(minutes=int(s["first_lookup_minutes"]))
    out = {}
    for i, (acc, _hop) in enumerate(sorted(accounts, key=lambda a: (a[1], a[0]))):
        at = t + timedelta(minutes=i * int(s["per_account_minutes"]))
        close = at.replace(hour=DESK_HOURS[1], minute=0, second=0)
        if at >= close and len(str(s["desk_opens"])) <= 5:   # the desk closes at 18:00: the rest waits for 10:00
            hh, mm = (int(x) for x in str(s["desk_opens"]).split(":"))
            at = (close + timedelta(days=1)).replace(hour=hh, minute=mm) + (at - close)
        out[acc] = at.strftime(FMT)
    return out


def run_today(conn, case_id: str, bank: str) -> list[dict]:
    """Replay today's desk for every own-bank account on the trail. Needs the agent's trace and actions first."""
    from pipeline import steps

    s = desk_settings(case_id)
    c = steps.complaint(conn, case_id)
    received = conn.execute("SELECT received_at FROM TICKET WHERE case_id = {c}", {"c": case_id}).fetchval()
    received = str(received or c["reported_at"])[:19]
    accounts = conn.execute(
        "SELECT account_id, hop, trail_amount_inr FROM CASE_ACCOUNT WHERE case_id = {c} AND viewing_bank_id = {b} "
        "AND is_own_bank AND NOT is_exit", {"c": case_id, "b": bank}).fetchall()
    if not accounts:
        raise steps.StepFailed(f"No accounts for {case_id}: let the agent work the ticket first.")
    times = plan(received, [(a, int(h)) for a, h, _ in accounts], s)
    root_ts = str(conn.execute("SELECT MIN(ts) FROM TXN WHERE utr = {u}", {"u": c["utr"]}).fetchval())[:19]
    snapshot = conn.execute(
        "SELECT MAX(ts) FROM BALANCE_SNAPSHOT WHERE ts <= TO_TIMESTAMP({r}, 'YYYY-MM-DD HH24:MI:SS')",
        {"r": root_ts}).fetchval()
    replay_from = str(snapshot)[:19] if snapshot else (
        datetime.strptime(root_ts, FMT) - timedelta(seconds=1)).strftime(FMT)
    for acc, hop, trail in accounts:
        steps.run_step(conn, "090_today.sql", {
            "case_id": case_id, "own_bank_id": bank, "probe_id": acc, "hop": str(int(hop)),
            "action_at": times[acc], "replay_from": replay_from, "victim_tag": c["victim_tag"],
            "trail_amount": str(trail), "freeze_whole": "1" if s["freezes_whole_account"] else "0"})
    from pipeline.db import audit

    audit(conn, "today_baseline", case_id, f"bank={bank} desk_opens={s['desk_opens']}")
    return [{"account_id": a, "action_at": times[a]} for a, _, _ in accounts]


def _num(v) -> Decimal:
    return Decimal(str(v or 0))


def _minutes(a: str | None, b: str | None) -> int | None:
    if not a or not b:
        return None
    return int((datetime.strptime(b[:19], FMT) - datetime.strptime(a[:19], FMT)).total_seconds() // 60)


def compare(conn, case_id: str, bank: str) -> dict:
    """The scoreboard: victim money held, honest money locked, time from ticket to first hold, onward ticket time."""
    q = lambda sql, **p: conn.execute(sql, {"c": case_id, "b": bank, **p})  # noqa: E731
    received = str(q("SELECT received_at FROM TICKET WHERE case_id = {c}").fetchval() or "")[:19]
    s = desk_settings(case_id)

    today_rows = q("SELECT account_id, hop, action_at, balance_then_inr, victim_then_inr, today_action, locked_inr, "
                   "honest_locked_inr, stmt_id FROM TODAY_OUTCOME WHERE case_id = {c} AND viewing_bank_id = {b} "
                   "ORDER BY action_at").fetchall()
    if not today_rows:
        run_today(conn, case_id, bank)
        return compare(conn, case_id, bank)
    names = dict(q("SELECT account_id, COALESCE(holder_name, account_id) FROM ACCOUNT "
                   "WHERE account_id IN (SELECT to_account FROM TRACE_EDGE WHERE case_id = {c})").fetchall())
    today_accounts, t_victim, t_honest, t_first = [], Decimal(0), Decimal(0), None
    for acc, _hop, at, bal, victim, action, locked, honest, stmt in today_rows:
        held_victim = min(_num(victim), _num(locked))
        t_victim += held_victim
        t_honest += _num(honest)
        at = str(at)[:19]
        if t_first is None or at < t_first:          # the desk's first action on the ticket, money left or not
            t_first = at
        today_accounts.append({"account_id": acc, "name": names.get(acc, acc), "action_at": at,
                               "action": action, "balance_then_inr": float(_num(bal)),
                               "victim_held_inr": float(held_victim), "honest_locked_inr": float(_num(honest)),
                               "stmt_id": stmt})

    facts = {a: (_num(v), _num(o)) for a, v, o in q(
        "SELECT account_id, victim_amount_inr, other_victims_inr FROM CASE_ACCOUNT WHERE case_id = {c} "
        "AND viewing_bank_id = {b}").fetchall()}

    def tool_side(where: str) -> tuple[Decimal, Decimal, list[dict]]:
        """Totals per account: the widest hold on an account counts once (a freeze covers the lien under it)."""
        rows, widest = [], {}
        for acc, kind, amount, by, placed in q(
                "SELECT account_id, hold_kind, amount_inr, placed_by, placed_at FROM HOLD_LEDGER "
                f"WHERE case_id = {{c}} AND viewing_bank_id = {{b}} AND {where} ORDER BY hold_id").fetchall():
            if kind == "MARKER":
                continue
            widest[acc] = max(widest.get(acc, Decimal(0)), _num(amount))
            rows.append({"account_id": acc, "name": names.get(acc, acc), "kind": kind,
                         "amount_inr": float(_num(amount)), "placed_by": by, "placed_at": str(placed)[:19]})
        victim_total, honest_total = Decimal(0), Decimal(0)
        for acc, amount in widest.items():
            victim, others = facts.get(acc, (Decimal(0), Decimal(0)))
            victim_total += min(victim, amount)
            honest_total += max(Decimal(0), amount - min(amount, victim + others))
        return victim_total, honest_total, rows

    o_victim, o_honest, o_rows = tool_side("placed_by = 'agent'")
    n_victim, n_honest, n_rows = tool_side("hold_status = 'ACTIVE'")
    first_hold = q("SELECT MIN(placed_at) FROM HOLD_LEDGER WHERE case_id = {c} AND viewing_bank_id = {b} "
                   "AND placed_by = 'agent'").fetchval()
    first_hold = str(first_hold)[:19] if first_hold else None
    onward_tool = q("SELECT MIN(filed_at) FROM PORTAL_ENTRY WHERE case_id = {c} AND viewing_bank_id = {b} "
                    "AND entry_kind = 'ONWARD'").fetchval()
    # Today the onward entry is typed after the paying account has been looked up.
    payer_time = q("SELECT MIN(t.action_at) FROM TODAY_OUTCOME t JOIN PORTAL_ENTRY p "
                   "ON p.case_id = t.case_id AND p.from_account = t.account_id AND p.entry_kind = 'ONWARD' "
                   "WHERE t.case_id = {c} AND t.viewing_bank_id = {b}").fetchval()
    onward_today = ((datetime.strptime(str(payer_time)[:19], FMT) + timedelta(minutes=int(s["per_account_minutes"])))
                    .strftime(FMT) if payer_time else None)
    paid, paid_at = q("SELECT SUM(t.amount_inr), MIN(t.ts) FROM TXN t JOIN COMPLAINT c ON c.utr = t.utr "
                      "WHERE c.case_id = {c}").fetchone()
    last_today = max((a["action_at"] for a in today_accounts), default=received)
    # Money that walked out of the trail's own-bank accounts after the ticket, before today's desk got there.
    walked = [{"at": str(ts)[:19], "account_id": acc, "name": names.get(acc, acc), "amount_inr": float(_num(amt)),
               "where": ident or (f"an account at {bank_name}" if bank_name else to)}
              for ts, acc, amt, ident, to, bank_name in q(
                  "SELECT t.ts, t.from_account, t.amount_inr, t.exit_identifier, t.to_account, b.bank_name FROM TXN t "
                  "JOIN CASE_ACCOUNT ca ON ca.account_id = t.from_account AND ca.case_id = {c} "
                  "AND ca.viewing_bank_id = {b} AND ca.is_own_bank "
                  "LEFT JOIN ACCOUNT ta ON ta.account_id = t.to_account LEFT JOIN BANK b ON b.bank_id = ta.bank_id "
                  "WHERE (t.is_exit OR (ta.bank_id IS NOT NULL AND ta.bank_id <> {b})) "
                  "AND t.ts > TO_TIMESTAMP({r}, 'YYYY-MM-DD HH24:MI:SS') "
                  "AND t.ts <= TO_TIMESTAMP({l}, 'YYYY-MM-DD HH24:MI:SS') ORDER BY t.ts",
                  r=received, l=last_today).fetchall()]
    decisions = int(q("SELECT COUNT(*) FROM PROPOSAL WHERE case_id = {c} AND act_mode = 'OFFICER' "
                      "AND proposal_status IN ('WAITING', 'APPROVED', 'EDITED', 'REJECTED')").fetchval() or 0)
    kyc_at = q("SELECT MIN(filed_at) FROM PORTAL_ENTRY WHERE case_id = {c} AND entry_kind = 'KYC'").fetchval()
    return {
        "case_id": case_id, "bank": bank, "received_at": received, "victim_paid_inr": float(_num(paid)),
        "paid_at": str(paid_at)[:19] if paid_at else None,
        "desk": {k: str(v) for k, v in s.items()},
        "walked_out_after_ticket": walked,
        "today": {"victim_held_inr": float(t_victim), "honest_locked_inr": float(t_honest),
                  "first_hold_at": t_first, "minutes_to_first_hold": _minutes(received, t_first),
                  "onward_filed_at": onward_today, "accounts": today_accounts},
        "tool": {"victim_held_inr": float(o_victim), "honest_locked_overnight_inr": float(o_honest),
                 "honest_locked_now_inr": float(n_honest), "victim_held_now_inr": float(n_victim),
                 "first_hold_at": first_hold, "minutes_to_first_hold": _minutes(received, first_hold),
                 "onward_filed_at": str(onward_tool)[:19] if onward_tool else None,
                 "holds_overnight": o_rows, "holds_now": n_rows,
                 "officer_decisions": decisions,
                 "kyc_filed_at": str(kyc_at)[:19] if kyc_at else None,
                 "kyc_minutes": _minutes(received, str(kyc_at)[:19]) if kyc_at else None},
        "today_process": {"manual_steps": int(RULES["today_defaults"]["manual_steps_per_ticket"]),
                          "kyc_allowed_days": int(RULES["today_defaults"]["kyc_allowed_days"])},
    }


DESK_HOURS = (10, 18)


def after_hours(ts: str | None) -> bool:
    if not ts:
        return False
    hour = int(str(ts)[11:13])
    return hour < DESK_HOURS[0] or hour >= DESK_HOURS[1]


def day_summary(conn, bank: str) -> dict:
    """Today's desk vs the agent over every ticket the agent has worked (the Review band and the Whole day tab).
    An account frozen whole in several tickets' replays counts once, at its largest."""
    worked = [c for (c,) in conn.execute(
        "SELECT t.case_id FROM TICKET t JOIN AGENT_RUN r ON r.run_id = t.run_id "
        "WHERE r.run_status = 'DONE' ORDER BY t.received_at, t.case_id").fetchall()]
    tickets, frozen_today = [], {}
    for case_id in worked:
        c = compare(conn, case_id, bank)
        for a in c["today"]["accounts"]:
            if a["action"] == "WHOLE_FREEZE" and a["honest_locked_inr"] > 0:
                frozen_today[a["account_id"]] = max(frozen_today.get(a["account_id"], 0), a["honest_locked_inr"])
        tickets.append({
            "case_id": case_id, "received_at": c["received_at"], "after_hours": after_hours(c["received_at"]),
            "victim_paid_inr": c["victim_paid_inr"],
            "today_victim_held_inr": c["today"]["victim_held_inr"],
            "tool_victim_held_inr": c["tool"]["victim_held_inr"],
            "today_honest_locked_inr": c["today"]["honest_locked_inr"],
            "tool_honest_locked_overnight_inr": c["tool"]["honest_locked_overnight_inr"],
            "tool_honest_locked_now_inr": c["tool"]["honest_locked_now_inr"],
            "today_minutes": c["today"]["minutes_to_first_hold"], "tool_minutes": c["tool"]["minutes_to_first_hold"]})
    s = lambda key: round(sum(t[key] or 0 for t in tickets), 2)  # noqa: E731
    night = [t for t in tickets if t["after_hours"]]
    return {
        "tickets": tickets, "worked": len(tickets), "night": len(night),
        "victim_paid_inr": s("victim_paid_inr"),
        "today": {"victim_held_inr": s("today_victim_held_inr"),
                  "honest_locked_inr": round(sum(frozen_today.values()), 2),
                  "frozen_names": [n for (n,) in conn.execute(
                      "SELECT COALESCE(holder_name, account_id) FROM ACCOUNT WHERE account_id IN ({ids}) "
                      "ORDER BY holder_name".replace("{ids}", ", ".join(
                          "'" + a.replace("'", "''") + "'" for a in frozen_today))).fetchall()] if frozen_today else [],
                  "accounts_frozen_whole": len(frozen_today),
                  "night_minutes": [t["today_minutes"] for t in night if t["today_minutes"] is not None]},
        "tool": {"victim_held_inr": s("tool_victim_held_inr"),
                 "honest_locked_overnight_inr": s("tool_honest_locked_overnight_inr"),
                 "honest_locked_now_inr": s("tool_honest_locked_now_inr"),
                 "max_minutes": max((t["tool_minutes"] or 0 for t in tickets), default=None),
                 "night_minutes": [t["tool_minutes"] for t in night if t["tool_minutes"] is not None]},
    }


def main(argv: list[str] | None = None) -> int:
    import argparse
    import json

    ap = argparse.ArgumentParser()
    ap.add_argument("case_id")
    ap.add_argument("--bank")
    args = ap.parse_args(argv)
    from pipeline.db import connect, settings

    conn = connect()
    try:
        run_today(conn, args.case_id, args.bank or settings()["own_bank_id"])
        print(json.dumps(compare(conn, args.case_id, args.bank or settings()["own_bank_id"]), indent=2))
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())