"""Proactive holds and pre-alerts (DECISIONS D-60 to D-65). Once a victim's ticket names an account at this bank, the
bank stops waiting for the next victim to report: new money from a stranger into that account is held the moment it
lands, and the stranger's bank or the police are told, so the payer can be asked whether they meant to pay.

  Module 1  eligibility       an account may get proactive holds only when someone outside our scoring pointed at
                              it (a victim paid it directly in a ticket here, or the Suspect Registry lists it) AND
                              it is typed Pipe for fraud or Fast mover AND it is not nodal, pool or escrow
  Module 2  payment checks    on arrival, using only what is known then: Rs 1,000 or more, a first-time payer in 90
                              days, not NACH or AutoPay, not a reversal, not already named by a ticket; a Fast mover
                              also needs one of: unusually large, a flagged payer, within 24 h of the last victim
                              payment
  Module 3  the hold          through the checker only, in one of two modes: rbi_draft (the payment's own amount,
                              capped at the free balance, the draft's clocks) or current_sop (only up to the room
                              under the existing complaint, D-47, under that notice)
  Module 4  the clocks        hold + 20 days to explain, + 30 to decide (explanation + 10), + 60 at most; the next-day
                              check by 18:00 on the next working day; escalations, the day-50 alert and the two limit
                              releases. Never released early without a decision, never kept past the limit without one
  Module 5  pre-alerts        a fixed template filled from the database, never from the model: sent at once for a
                              Pipe for fraud, waiting for the officer's click for a Fast mover; a flagged payer gets a
                              layering record instead. Five endings
The score can stop a hold, never start one. The words "victim" and "mule" never appear in anything sent out.
Times are the simulated clock's, never the machine's, so `--until` replays a day exactly.
"""
from __future__ import annotations

import json
from datetime import date, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import yaml

from pipeline.explain import inr

ROOT = Path(__file__).resolve().parents[1]
RULES = yaml.safe_load((ROOT / "config" / "rules.yaml").read_text(encoding="utf-8"))
SCORING = yaml.safe_load((ROOT / "config" / "scoring.yaml").read_text(encoding="utf-8"))
P = RULES["proactive"]
ELIGIBLE_TYPES = tuple(P["eligible_types"])
NEVER_TYPES = tuple(P["never_types"])
TYPE_NAMES = {k: v["name"] for k, v in SCORING["account_types"].items()}
HIGH_AT, LOW_BELOW = float(SCORING["cutoffs"]["high_at"]), float(SCORING["cutoffs"]["low_below"])
AUTOMATIC_CHANNELS = ("NACH", "AUTOPAY")
FMT = "%Y-%m-%d %H:%M:%S"
MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")
CLOSING = "Nothing here says the payer has done anything wrong."
CALL_SCRIPT = ("A payment you made has been held because the receiving account is under investigation. "
               "Did you mean to make it?")
FORBIDDEN_WORDS = ("victim", "mule")
OPEN_STATES = ("NEXT_DAY_CHECK", "CONFIRMED", "EXPLAINED", "REFER_DRAFTED", "GENUINE")
LIMIT_RELEASE = "released at the limit, no decision recorded"
NO_POLICE_RELEASE = "released: no police direction within 30 days"


def _d(v) -> Decimal:
    return Decimal(str(v)) if v is not None else Decimal(0)


def _ts(v) -> str:
    return str(v)[:19]


def _dt(v) -> datetime:
    return datetime.strptime(_ts(v), FMT)


def add_days(ts: str, days: int) -> str:
    return (_dt(ts) + timedelta(days=days)).strftime(FMT)


def when(ts) -> str:
    """'2026-10-18 02:41:00' -> '02:41 on 18 Oct'."""
    t = _ts(ts)
    return f"{t[11:16]} on {int(t[8:10])} {MONTHS[int(t[5:7]) - 1]}"


def day_label(ts) -> str:
    t = _ts(ts)
    return f"{int(t[8:10])} {MONTHS[int(t[5:7]) - 1]} {t[:4]}"


def type_name(pattern: str | None) -> str:
    return TYPE_NAMES.get(pattern or "", "not scored yet")


def ending(account: str | None) -> str:
    return (account or "")[-4:]


def no_forbidden_words(text: str) -> bool:
    low = text.lower()
    return not any(w in low for w in FORBIDDEN_WORDS)


# ---------------------------------------------------------------- settings and the calendar

def mode(conn) -> str:
    """rbi_draft or current_sop: the run's setting (RUN_SETTING, written at reset), else config/rules.yaml."""
    try:
        v = conn.execute("SELECT MAX(setting_value) FROM RUN_SETTING WHERE setting_name = 'proactive_hold_mode'"
                         ).fetchval()
    except Exception:       # an older schema without the table: the config decides
        v = None
    return v if v in ("rbi_draft", "current_sop") else P["proactive_hold_mode"]


def holidays() -> set[str]:
    return {str(h["date"]) for h in P.get("bank_holidays") or []}


def is_working_day(d: date) -> bool:
    return d.weekday() != 6 and d.isoformat() not in holidays()


def next_day_check_due(placed_at: str) -> str:
    """18:00 on the next working day after the hold (Sundays and bank holidays skipped)."""
    d = _dt(placed_at).date() + timedelta(days=1)
    while not is_working_day(d):
        d += timedelta(days=1)
    return f"{d.isoformat()} {P['next_day_check_by']}:00"


def clocks(placed_at: str) -> dict:
    """The draft's clocks, calendar days from the hold (D-63)."""
    return {"explanation_due_on": add_days(placed_at, int(P["explanation_days"])),
            "decide_by": add_days(placed_at, int(P["decide_days"])),
            "expires_on": add_days(placed_at, int(P["limit_days"])),
            "must_decide_on": add_days(placed_at, int(P["must_decide_day"]))}


# ---------------------------------------------------------------- Module 1: scoring snapshot and eligibility

def snapshot(conn, account: str, snapshot_id: int | None = None) -> dict | None:
    """The latest scoring snapshot of an account (or a given one)."""
    cols = ("snapshot_id", "case_id", "computed_at", "weight_version", "pass_through", "fraud_exposure",
            "account_pattern", "fraud_money_leaves_fast", "fraud_case_count", "ladder_step", "signals_text")
    where = "snapshot_id = {s!d}" if snapshot_id else "account_id = {a}"
    row = conn.execute(f"SELECT {', '.join(cols)} FROM SCORE_SNAPSHOT WHERE {where} "
                       "ORDER BY computed_at DESC, snapshot_id DESC LIMIT 1",
                       {"a": account, "s": str(int(snapshot_id or 0))}).fetchone()
    if not row:
        return None
    s = dict(zip(cols, row, strict=True))
    s["pass_through"], s["fraud_exposure"] = float(s["pass_through"]), float(s["fraud_exposure"])
    s["snapshot_id"] = int(s["snapshot_id"])
    return s


def rescore(maker, bank: str, account: str, now: str) -> dict | None:
    """Score one own account as of now (a watch event, or the daily rescoring) and return its new snapshot."""
    from pipeline import steps

    key = f"WATCH:{account}"
    maker.execute("DELETE FROM SCORE_TARGET WHERE case_id = {k}", {"k": key})
    maker.execute("INSERT INTO SCORE_TARGET (case_id, account_id) VALUES ({k}, {a})", {"k": key, "a": account})
    steps.score(maker, key, bank, now, targets=False)
    return snapshot(maker, account)


def eligibility_reason(conn, account: str, now: str) -> str | None:
    """layer1_ticket: a ticket at this bank names a payment straight into the account (a victim was told to pay it);
    suspect_registry: listed there. Accounts deeper in the trail, or flagged only by our own score, never qualify."""
    n = conn.execute(
        "SELECT COUNT(*) FROM COMPLAINT c JOIN INTAKE_CHECK k ON k.case_id = c.case_id "
        "AND k.match_status IN ('EXACT', 'ACCEPTED') JOIN TXN t ON t.txn_id = k.matched_txn_id "
        "WHERE t.to_account = {a} AND c.ticket_kind IN ('POLICE', 'IO_NOTICE') "
        "AND c.reported_at <= TO_TIMESTAMP({n}, 'YYYY-MM-DD HH24:MI:SS')", {"a": account, "n": now}).fetchval()
    if int(n or 0):
        return "layer1_ticket"
    n = conn.execute("SELECT COUNT(*) FROM SUSPECT_REGISTRY WHERE account_id = {a} "
                     "AND listed_on <= TO_TIMESTAMP({n}, 'YYYY-MM-DD HH24:MI:SS')", {"a": account, "n": now}).fetchval()
    return "suspect_registry" if int(n or 0) else None


def never_type(conn, account: str) -> bool:
    t = conn.execute("SELECT account_type FROM ACCOUNT WHERE account_id = {a}", {"a": account}).fetchval()
    return t in NEVER_TYPES


def decide_eligibility(conn, account: str, now: str, snap: dict | None, reason: str | None = "-") -> dict:
    """proactive_allowed = an eligibility reason AND the type is Pipe for fraud or Fast mover AND not nodal etc.
    reason: pass it when already looked up ("-" means look it up here)."""
    reason = eligibility_reason(conn, account, now) if reason == "-" else reason
    pattern = snap["account_pattern"] if snap else None
    allowed = bool(reason) and pattern in ELIGIBLE_TYPES and not never_type(conn, account)
    return {"reason": reason, "pattern": pattern, "allowed": allowed,
            "snapshot_id": snap["snapshot_id"] if snap else None}


# ---------------------------------------------------------------- Module 2: payment checks on arrival

def payer_type(conn, bank: str, payer: str | None, now: str) -> str:
    if payer and conn.execute("SELECT COUNT(*) FROM FLAGGED_ACCOUNT WHERE account_id = {p} "
                              "AND flagged_since <= TO_TIMESTAMP({n}, 'YYYY-MM-DD HH24:MI:SS')",
                              {"p": payer, "n": now}).fetchval():
        return "flagged"
    payer_bank = conn.execute("SELECT bank_id FROM ACCOUNT WHERE account_id = {p}", {"p": payer}).fetchval()
    return "own_customer" if payer_bank == bank else "other_bank"


def percentile(values: list[Decimal], pct: float) -> Decimal | None:
    """Nearest-rank percentile (no interpolation, so the number can be found in the account's own credits)."""
    if not values:
        return None
    ordered = sorted(values)
    rank = max(1, int(-(-pct * len(ordered) // 100)))        # ceil(pct / 100 * n)
    return ordered[min(rank, len(ordered)) - 1]


def check_new_payment(conn, bank: str, credit: dict, pattern: str | None, now: str) -> dict:
    """Module 2: hold or pass, with every check and its value. credit: txn_id, utr, from_account, to_account,
    amount, channel, ts. Only what is known at arrival is used."""
    acc, payer, amount = credit["to_account"], credit["from_account"], _d(credit["amount"])
    since = add_days(now, -int(P["new_payer_days"]))
    q = conn.execute
    checks = []

    def add(name: str, rule: str, value, passed: bool) -> None:
        checks.append({"check": name, "rule": rule, "value": value, "passed": bool(passed)})

    add("amount", f"{inr(P['min_amount'])} or more", inr(amount), amount >= Decimal(str(P["min_amount"])))
    paid_before = int(q("SELECT COUNT(*) FROM TXN_LIVE WHERE from_account = {p} AND to_account = {a} "
                        "AND txn_id <> {t} AND ts >= TO_TIMESTAMP({s}, 'YYYY-MM-DD HH24:MI:SS') "
                        "AND ts <= TO_TIMESTAMP({n}, 'YYYY-MM-DD HH24:MI:SS')",
                        {"p": payer, "a": acc, "t": credit["txn_id"], "s": since, "n": now}).fetchval() or 0)
    add("new_payer", f"no payment from this payer into the account in the previous {P['new_payer_days']} days",
        f"{paid_before} earlier payments", paid_before == 0)
    automatic = credit.get("channel") in AUTOMATIC_CHANNELS
    add("not_automatic", "not a NACH or UPI AutoPay credit", credit.get("channel"),
        not (automatic and P.get("skip_nach_credits", True)))
    reversal = int(q("SELECT COUNT(*) FROM TXN_LIVE WHERE from_account = {a} AND to_account = {p} "
                     "AND amount_inr = {amt!d} AND ts >= TO_TIMESTAMP({s}, 'YYYY-MM-DD HH24:MI:SS') "
                     "AND ts < TO_TIMESTAMP({n}, 'YYYY-MM-DD HH24:MI:SS')",
                     {"a": acc, "p": payer, "amt": str(amount), "s": since, "n": now}).fetchval() or 0)
    add("not_reversal", "not a return of a payment this account made earlier",
        "a return of its own payment" if reversal else "no", reversal == 0)
    named = int(q("SELECT COUNT(*) FROM COMPLAINT WHERE utr = {u} "
                  "AND reported_at <= TO_TIMESTAMP({n}, 'YYYY-MM-DD HH24:MI:SS')",
                  {"u": credit.get("utr"), "n": now}).fetchval() or 0)
    add("not_already_named", "no ticket names this credit's UTR yet (if one does, it is a normal hold)",
        "named by a ticket" if named else "no", named == 0)
    kind = payer_type(conn, bank, payer, now)
    extra = []
    if pattern == "FAST_MOVER":
        amounts = [_d(a) for (a,) in q(
            "SELECT amount_inr FROM TXN_LIVE WHERE to_account = {a} AND txn_id <> {t} "
            "AND ts > TO_TIMESTAMP({s}, 'YYYY-MM-DD HH24:MI:SS') AND ts <= TO_TIMESTAMP({n}, 'YYYY-MM-DD HH24:MI:SS')",
            {"a": acc, "t": credit["txn_id"], "s": since, "n": now}).fetchall()]
        p90 = percentile(amounts, float(P["fast_mover_size_percentile"]))
        extra.append({"check": "unusually_large", "rule": f"above the {P['fast_mover_size_percentile']}th percentile "
                      f"of its credits in 90 days", "value": f"{inr(amount)} against {inr(p90)}" if p90 is not None
                      else "no earlier credits", "passed": p90 is None or amount > p90})
        extra.append({"check": "payer_flagged", "rule": "the payer is a flagged account", "value": kind,
                      "passed": kind == "flagged"})
        last = q("SELECT MAX(t.ts) FROM TXN_LIVE t JOIN ROOT_PAYMENT rp ON rp.utr = t.utr "
                 "AND rp.reported_at <= TO_TIMESTAMP({n}, 'YYYY-MM-DD HH24:MI:SS') "
                 "WHERE t.to_account = {a} AND t.ts <= TO_TIMESTAMP({n}, 'YYYY-MM-DD HH24:MI:SS')",
                 {"a": acc, "n": now}).fetchval()
        hours = None if last is None else (_dt(now) - _dt(last)).total_seconds() / 3600
        extra.append({"check": "close_to_the_fraud", "rule": f"within {P['fast_mover_recent_hours']} hours of the last "
                      f"reported payment into it", "value": "none" if hours is None else f"{hours:.1f} hours",
                      "passed": hours is not None and hours <= float(P["fast_mover_recent_hours"])})
        checks.append({"check": "fast_mover_extra", "rule": "a Fast mover also needs one of: unusually large, a "
                       "flagged payer, close to the fraud", "value": ", ".join(e["check"] for e in extra if e["passed"])
                       or "none", "passed": any(e["passed"] for e in extra)})
    result = "hold" if all(c["passed"] for c in checks) else "pass"
    return {"result": result, "checks": checks + extra, "payer_type": kind, "named": bool(named),
            "failed": [c for c in checks if not c["passed"]]}


CHECK_WORDS = {
    "amount": "it is below Rs 1,000",
    "new_payer": "the payer has paid this account in the last 90 days",
    "not_automatic": "it is an automatic NACH or AutoPay credit",
    "not_reversal": "it returns a payment the account made earlier",
    "not_already_named": "a ticket already names it, so it gets the normal hold",
    "fast_mover_extra": "it is a Fast mover and none of the three extra conditions holds",
}


def hold_amount(checker, account: str, case_id: str, credit_amount: Decimal, now: str, run_mode: str,
                reported) -> tuple[Decimal, str]:
    """rbi_draft: the payment's own amount, never above the free balance. current_sop: only up to the room left
    under the existing complaint's reported amount (D-47), never above the payment or the free balance."""
    free = checker.balance_at(account, now) - checker.held(account)
    amount = min(credit_amount, max(free, Decimal(0)))
    if run_mode == "current_sop":
        if case_id.startswith("REGISTRY:"):
            return Decimal(0), "no police notice on the account: current SOP mode holds new money only under one"
        room = max(_d(reported) - checker.held(account, case_id), Decimal(0))
        return min(amount, room), f"room under the existing complaint {inr(room)}"
    return amount, f"free balance {inr(free)}"


# ---------------------------------------------------------------- the watch calls this for new money

def on_new_money(watch, account: str, txn_id: str, at: str, amount: Decimal, payer: str) -> list[str]:
    """A credit into a watched own account. Eligible accounts: the payment checks, then a proactive hold through the
    checker and its pre-alert. Others: say why nothing is held."""
    from pipeline.recalc import propose

    maker, checker, bank = watch.maker, watch.checker, watch.bank
    entries = [w for w in watch.entries(account) if at > w["added_at"] and w["first_pass"] != "CHECKED"]
    if not entries:
        return []
    name, hhmm = watch.name(account), at[11:16]
    reason = eligibility_reason(maker, account, at)
    if not reason and amount < Decimal(str(P["min_amount"])):
        # Never eligible (no ticket names a payment into it, not in the registry; neither can be undone) and too
        # small to report: nothing to check, score or write. Murugan's small daytime sales end here.
        return []
    never = never_type(maker, account)
    # The account's latest scores (D-60): every ticket that names it, a refused cash-out and the daily run rescore
    # it. A payment arriving does not: rescoring runs a Python UDF inside Exasol, too slow to repeat per payment.
    snap = snapshot(maker, account)
    decision = decide_eligibility(maker, account, at, snap, reason)
    checker.refresh_eligibility(account, at, decision)
    case_id = entries[0]["case_id"]
    if not decision["allowed"]:
        if amount < Decimal(str(P["min_amount"])):
            return []
        scores = (f" (pass-through {snap['pass_through']:.2f}, fraud exposure {snap['fraud_exposure']:.2f})"
                  if snap else "")
        if never:
            why = "it is a nodal, pool or escrow account, where new money is never held automatically"
        elif not reason:
            why = (f"no reported payment went straight into it and it is not in the Suspect Registry, so new money "
                   f"is not held here; it is typed {type_name(decision['pattern'])}{scores}")
        else:
            why = (f"it is typed {type_name(decision['pattern'])}{scores}: the score can stop a hold, never start one")
        sentence = (f"{hhmm}: {name} received {inr(amount)} from {watch.counterparty(payer)}: not held. "
                    f"{why[:1].upper()}{why[1:]}.")
        watch.event(account, case_id, at, "NEW_MONEY_HONEST", sentence, amount, watch.counterparty(payer), ref=txn_id)
        return [sentence]
    allowed_case = next((w["case_id"] for w in entries), case_id)
    row = maker.execute("SELECT utr, channel, ts FROM TXN_LIVE WHERE txn_id = {t}", {"t": txn_id}).fetchone()
    credit = {"txn_id": txn_id, "utr": row[0] if row else None, "channel": row[1] if row else None,
              "from_account": payer, "to_account": account, "amount": amount, "ts": at}
    result = check_new_payment(maker, bank, credit, decision["pattern"], at)
    run_mode = mode(maker)
    maker.execute(
        "INSERT INTO PAYMENT_CHECK (account_id, case_id, txn_id, checked_at, check_result, payer_type, "
        "account_pattern, checks_json, snapshot_id, stmt_id) VALUES ({a}, {c}, {t}, "
        "TO_TIMESTAMP({n}, 'YYYY-MM-DD HH24:MI:SS'), {r}, {pt}, {pat}, {j}, {s}, CURRENT_STATEMENT)",
        {"a": account, "c": allowed_case, "t": txn_id, "n": at, "r": result["result"], "pt": result["payer_type"],
         "pat": decision["pattern"], "j": json.dumps(result["checks"], default=str)[:4000],
         "s": None if decision["snapshot_id"] is None else str(decision["snapshot_id"])})
    from pipeline.db import audit

    audit(maker, "payment_check", allowed_case, json.dumps(
        {"txn_id": txn_id, "result": result["result"], "payer_type": result["payer_type"], "mode": run_mode,
         "snapshot_id": decision["snapshot_id"], "checks": [(c["check"], c["value"], c["passed"])
                                                            for c in result["checks"]]}, default=str),
          actor="maker", sim_at=at, account_id=account)
    who = watch.counterparty(payer)
    if result["result"] == "pass":
        failed = "; ".join(CHECK_WORDS.get(c["check"], c["check"]) for c in result["failed"])
        sentence = f"{hhmm}: {name} received {inr(amount)} from {who}: not held, because {failed}."
        watch.event(account, allowed_case, at, "NEW_MONEY_PASSED", sentence, amount, who, ref=txn_id)
        return [sentence]
    reported = maker.execute("SELECT reported_inr FROM WATCH_LIST WHERE account_id = {a} AND case_id = {c} "
                             "AND left_at IS NULL", {"a": account, "c": allowed_case}).fetchval()
    take, why = hold_amount(checker, account, allowed_case, amount, at, run_mode, reported)
    if take <= 0:
        sentence = (f"{hhmm}: {name} received {inr(amount)} from {who}: it passed the checks, but nothing is held "
                    f"({why}).")
        watch.event(account, allowed_case, at, "NEW_MONEY_PASSED", sentence, amount, who, ref=txn_id)
        return [sentence]
    pid = propose(maker, allowed_case, account, "PROACTIVE_HOLD", "AUTOMATIC", take, "watch", at,
                  {"trigger": "proactive", "source_txn_id": txn_id, "payer_account": payer,
                   "payer_type": result["payer_type"], "account_pattern": decision["pattern"],
                   "eligibility_reason": decision["reason"], "snapshot_id": decision["snapshot_id"],
                   "mode": run_mode, "why_amount": why, "credit_inr": str(amount),
                   "checks": [(c["check"], c["passed"]) for c in result["checks"]]})
    r = checker.process(None, at, [pid])
    if not r["done"]:
        sentence = f"{hhmm}: {name} received {inr(amount)} from {who}; the checker refused the hold ({r['text']})."
        watch.event(account, allowed_case, at, "NEW_MONEY_PASSED", sentence, amount, who, ref=txn_id)
        return [sentence]
    basis = ("a temporary debit hold under the RBI draft" if run_mode == "rbi_draft"
             else "held under the existing police notice")
    sentence = (f"{hhmm}: {name} received {inr(amount)} from {who}, a first-time payer: {inr(take)} held at once "
                f"({basis}). Nobody has complained about this payment yet. The money is already safe.")
    watch.event(account, allowed_case, at, "PROACTIVE_HOLD", sentence, amount, who, ref=txn_id, proposal_id=pid)
    out = [sentence]
    hold_id = maker.execute("SELECT MAX(hold_id) FROM HOLD_LEDGER WHERE proposal_id = {p!d}",
                            {"p": str(pid)}).fetchval()
    for (s,) in maker.execute("SELECT sentence FROM WATCH_EVENT WHERE hold_id = {h!d} AND event_kind IN "
                              "('PRE_ALERT_SENT', 'PRE_ALERT_READY', 'LAYERING_HELD') ORDER BY event_id",
                              {"h": str(int(hold_id or 0))}).fetchall():
        out.append(s)
    return out


def sync_registry(maker, checker, bank: str, now: str) -> list[str]:
    """Suspect Registry accounts at this bank go on the watch (case REGISTRY:<account>), scored, so new money into
    them can be checked like a Layer-1 account's (D-60). The registry is simulated; the README says so."""
    from pipeline.recalc import propose

    out = []
    for (acc,) in maker.execute(
            "SELECT r.account_id FROM SUSPECT_REGISTRY r JOIN ACCOUNT a ON a.account_id = r.account_id "
            "AND a.bank_id = {b} WHERE r.listed_on <= TO_TIMESTAMP({n}, 'YYYY-MM-DD HH24:MI:SS') "
            "AND NOT EXISTS (SELECT 1 FROM WATCH_LIST w WHERE w.account_id = r.account_id AND w.left_at IS NULL) "
            "ORDER BY r.account_id", {"b": bank, "n": now}).fetchall():
        snap = rescore(maker, bank, acc, now)
        pid = propose(maker, f"REGISTRY:{acc}", acc, "WATCH_ADD", "AUTOMATIC", None, "watch", now,
                      {"trigger": "registry", "snapshot_id": snap["snapshot_id"] if snap else None,
                       "why": "Listed in the I4C Suspect Registry (simulated)"})
        checker.process(None, now, [pid])
        out.append(f"{acc} is in the Suspect Registry: on the watch, typed "
                   f"{type_name(snap['account_pattern'] if snap else None)}.")
    return out


def daily_rescore(maker, checker, bank: str, now: str) -> int:
    """Once a day, every account on the watch is scored again and its eligibility rewritten (D-57). The night runs
    it before the first payment of each simulated day, so a jump of weeks with nothing happening costs nothing."""
    n = 0
    for (acc,) in maker.execute("SELECT DISTINCT account_id FROM WATCH_LIST WHERE left_at IS NULL "
                                "AND first_pass <> 'CHECKED' ORDER BY 1").fetchall():
        snap = rescore(maker, bank, acc, now)
        checker.refresh_eligibility(acc, now, decide_eligibility(maker, acc, now, snap))
        n += 1
    return n


# ---------------------------------------------------------------- Module 3: the checker places the hold

def validate(ck, p: dict, own) -> str | None:
    """Why the checker refuses a PROACTIVE_HOLD, or None."""
    acc, case, r = p["account_id"], p["case_id"], p["reasons"]
    if own is None:
        return "not this bank's account"
    if own[0] in NEVER_TYPES:
        return "never an automatic hold on new money in a nodal, pool or escrow account"
    w = ck._q("SELECT proactive_allowed, added_at, reported_inr FROM WATCH_LIST WHERE account_id = {a} "
              "AND case_id = {c} AND left_at IS NULL", a=acc, c=case).fetchone()
    if not w or not w[0]:
        return "proactive holds are not allowed on this account (proactive_allowed is false)"
    txn = r.get("source_txn_id")
    credit = ck._q("SELECT amount_inr, ts FROM TXN_LIVE WHERE txn_id = {t} AND to_account = {a}",
                   t=txn, a=acc).fetchone()
    if credit is None or _ts(credit[1]) <= _ts(w[1]):
        return "no new credit on the account after it was put on the watch"
    if ck._q("SELECT COUNT(*) FROM HOLD_LEDGER WHERE source_txn_id = {t} AND action_code = 'PROACTIVE_HOLD'",
             t=txn).fetchval():
        return "this payment is already held"
    amount = _d(p["amount_inr"])
    free = ck.balance_at(acc, _ts(p["proposed_at"])) - ck.held(acc)
    if amount <= 0 or amount > _d(credit[0]):
        return f"{inr(amount)} is above the payment's own amount ({inr(credit[0])})"
    if amount > free:
        return f"{inr(amount)} is above the free balance ({inr(free)})"
    if r.get("mode") == "current_sop":
        room = _d(w[2]) - ck.held(acc, case)
        if amount > room:
            return f"{inr(amount)} is above the room under the existing complaint ({inr(room)})"
    return None


def holder_text(ck, account: str, amount, txn: dict, h: dict, run_mode: str) -> str:
    """What the holder is told at once (rbi_draft): the reason, how to get the hold removed and the officer's
    contact. Never the evidence."""
    masked = ending(account)
    if run_mode != "rbi_draft":
        from pipeline.reasons import holder_message

        return holder_message(ck.complaint(h["case_id"]), account, amount, "LIEN", h["placed_at"])
    return (f"A temporary debit hold of {inr(amount)} has been placed on a payment into your account ending {masked} "
            f"(UTR {txn.get('utr') or '-'}, received at {when(txn['ts'])}), under the RBI's draft KYC Amendment "
            f"Directions, 2026. The rest of your balance is free to use. To have the hold removed, explain where the "
            f"money came from by {day_label(h['explanation_due_on'])} to {P['holder_contact']}, or at your branch "
            f"with ID. The bank will decide within 10 days of your explanation, or by {day_label(h['decide_by'])} if "
            f"none comes.")


def place(ck, p: dict, now: str) -> str:
    """Carry out a validated PROACTIVE_HOLD: the lien on that one credit, its legal basis and clocks, the holder
    notice (rbi_draft), then the pre-alert or the layering record."""
    acc, case, r, pid = p["account_id"], p["case_id"], p["reasons"], int(p["proposal_id"])
    run_mode = r.get("mode") or mode(ck.conn)
    basis = "rbi_draft" if run_mode == "rbi_draft" else "police_notice"
    c = clocks(now) if run_mode == "rbi_draft" else {"explanation_due_on": None, "decide_by": None, "expires_on": None}
    tag = "Held under the RBI draft, not linked to a complaint yet" if run_mode == "rbi_draft" else \
        "Held under the existing notice, not linked to a reported victim yet"
    ck._q("INSERT INTO HOLD_LEDGER (case_id, viewing_bank_id, account_id, hold_kind, amount_inr, hold_tag, "
          "source_txn_id, placed_at, placed_by, hold_status, action_code, reason_text, proposal_id, stmt_id, "
          "legal_basis, proactive, payer_type, payer_account, snapshot_id, hold_mode, check_due_at, "
          "explanation_due_on, decide_by, expires_on, review_state) VALUES ({c}, {b}, {a}, 'LIEN', {amt!d}, "
          "'NOT_LINKED', {src}, TO_TIMESTAMP({t}, 'YYYY-MM-DD HH24:MI:SS'), 'agent', 'ACTIVE', 'PROACTIVE_HOLD', "
          "{why}, {p!d}, CURRENT_STATEMENT, {lb}, TRUE, {pt}, {pa}, {snap}, {m}, "
          "TO_TIMESTAMP({due}, 'YYYY-MM-DD HH24:MI:SS'), TO_TIMESTAMP({ed}, 'YYYY-MM-DD HH24:MI:SS'), "
          "TO_TIMESTAMP({db}, 'YYYY-MM-DD HH24:MI:SS'), TO_TIMESTAMP({ex}, 'YYYY-MM-DD HH24:MI:SS'), "
          "'NEXT_DAY_CHECK')",
          c=case, a=acc, amt=str(_d(p["amount_inr"])), src=r.get("source_txn_id"), t=now, why=tag, p=str(pid),
          lb=basis, pt=r.get("payer_type"), pa=r.get("payer_account"),
          snap=None if r.get("snapshot_id") is None else str(int(r["snapshot_id"])), m=run_mode,
          due=next_day_check_due(now), ed=c["explanation_due_on"], db=c["decide_by"], ex=c["expires_on"])
    hold_id = int(ck._q("SELECT MAX(hold_id) FROM HOLD_LEDGER WHERE proposal_id = {p!d}", p=str(pid)).fetchval())
    txn = dict(zip(("txn_id", "utr", "ts", "from_account", "amount_inr", "channel"), ck._q(
        "SELECT txn_id, utr, ts, from_account, amount_inr, channel FROM TXN_LIVE WHERE txn_id = {t}",
        t=r.get("source_txn_id")).fetchone(), strict=True))
    h = {"case_id": case, "placed_at": now, **c}
    text = holder_text(ck, acc, p["amount_inr"], txn, h, run_mode)
    ck._q("INSERT INTO HOLDER_MESSAGE (case_id, account_id, hold_id, message_text, written_at, stmt_id) VALUES "
          "({c}, {a}, {h!d}, {t}, TO_TIMESTAMP({n}, 'YYYY-MM-DD HH24:MI:SS'), CURRENT_STATEMENT)",
          c=case, a=acc, h=str(hold_id), t=text[:1000], n=now)
    if run_mode == "rbi_draft":
        ck._q("UPDATE HOLD_LEDGER SET holder_told_at = TO_TIMESTAMP({n}, 'YYYY-MM-DD HH24:MI:SS') "
              "WHERE hold_id = {h!d}", n=now, h=str(hold_id))
    ck._audit("proactive_hold_placed", case, json.dumps(
        {"hold_id": hold_id, "amount": str(p["amount_inr"]), "txn_id": txn["txn_id"], "legal_basis": basis,
         "mode": run_mode, "snapshot_id": r.get("snapshot_id"), "payer_type": r.get("payer_type")}), now, acc)
    follow = after_hold(ck, hold_id, r.get("account_pattern"), now)
    return f"held {inr(p['amount_inr'])} of a first-time payer's money ({basis}), hold PH-H{hold_id}; {follow}"


# ---------------------------------------------------------------- Module 5: pre-alerts

def notice_words(ck, case_id: str) -> str:
    if case_id.startswith("REGISTRY:"):
        return "that is listed in the I4C Suspect Registry"
    ack = ck._q("SELECT ack_no FROM COMPLAINT WHERE case_id = {c}", c=case_id).fetchval()
    return f"that is already under police notice {ack or case_id} for a reported fraud"


def alert_text(ck, hold: dict, txn: dict) -> str:
    """The fixed template, filled from the database, never from the model (D-64)."""
    from pipeline.db import BANKS

    payer_bank = ck._q("SELECT bank_id FROM ACCOUNT WHERE account_id = {p}", p=txn["from_account"]).fetchval()
    bank_words = "our bank" if payer_bank == ck.bank else BANKS.get(payer_bank or "", {}).get("name") or "another bank"
    held = ("We have placed a temporary debit hold on this payment." if hold["legal_basis"] == "rbi_draft" else
            "We have held this payment under the existing notice.")
    text = (f"At {when(txn['ts'])}, {inr(txn['amount_inr'])} (UTR {txn['utr'] or '-'}) was paid from an account at "
            f"{bank_words} (ending {ending(txn['from_account'])}) into an account at our bank "
            f"{notice_words(ck, hold['case_id'])}. The payer had not paid this account before. {held} Please contact "
            f"the payer to confirm whether it was intended. {CLOSING}")
    assert no_forbidden_words(text), text
    return text


def _hold(ck, hold_id: int) -> dict:
    cols = ("hold_id", "case_id", "account_id", "amount_inr", "source_txn_id", "placed_at", "hold_status",
            "legal_basis", "payer_type", "payer_account", "snapshot_id", "hold_mode", "check_due_at", "checked_at",
            "explanation_due_on", "explained_at", "decide_by", "expires_on", "referred_at", "police_reply_due_on",
            "police_direction", "review_state", "escalated_at", "must_decide_at", "closed_why", "compliance_miss",
            "linked_case_id", "linked_at", "proactive", "checked_by", "proposal_id")
    row = ck._q(f"SELECT {', '.join(cols)} FROM HOLD_LEDGER WHERE hold_id = {{h!d}}", h=str(int(hold_id))).fetchone()
    if not row:
        return {}
    h = dict(zip(cols, row, strict=True))
    for k in ("placed_at", "check_due_at", "checked_at", "explanation_due_on", "explained_at", "decide_by",
              "expires_on", "referred_at", "police_reply_due_on", "escalated_at", "must_decide_at", "linked_at"):
        h[k] = _ts(h[k]) if h[k] is not None else None
    return h


def _event(ck, h: dict, kind: str, sentence: str, now: str, pre_alert_id=None) -> None:
    ck._q("INSERT INTO WATCH_EVENT (viewing_bank_id, account_id, case_id, event_at, event_kind, amount_inr, "
          "counterparty, txn_ref, hold_id, sentence, stmt_id) VALUES ({b}, {a}, {c}, "
          "TO_TIMESTAMP({n}, 'YYYY-MM-DD HH24:MI:SS'), {k}, {amt!d}, {cp}, {t}, {h!d}, {s}, CURRENT_STATEMENT)",
          a=h["account_id"], c=h["case_id"], n=now, k=kind, amt=str(_d(h["amount_inr"])),
          cp=h.get("payer_account"), t=h.get("source_txn_id"), h=str(int(h["hold_id"])), s=sentence[:1000])


def after_hold(ck, hold_id: int, pattern: str | None, now: str) -> str:
    """Who paid decides what happens next: a flagged payer gets a layering record for the police and no pre-alert;
    anyone else gets a pre-alert, sent at once for a Pipe for fraud, waiting for the officer for a Fast mover."""
    h = _hold(ck, hold_id)
    txn = dict(zip(("txn_id", "utr", "ts", "from_account", "amount_inr", "channel"), ck._q(
        "SELECT txn_id, utr, ts, from_account, amount_inr, channel FROM TXN_LIVE WHERE txn_id = {t}",
        t=h["source_txn_id"]).fetchone(), strict=True))
    name = ck._q("SELECT COALESCE(holder_name, account_id) FROM ACCOUNT WHERE account_id = {a}",
                 a=h["account_id"]).fetchval() or h["account_id"]
    if h["payer_type"] == "flagged":
        payer_name = ck._q("SELECT COALESCE(holder_name, account_id) FROM ACCOUNT WHERE account_id = {a}",
                           a=txn["from_account"]).fetchval() or txn["from_account"]
        detail = (f"At {when(txn['ts'])}, {payer_name}, an account under a police notice or in the Suspect Registry, "
                  f"paid {inr(txn['amount_inr'])} (UTR {txn['utr'] or '-'}) into {name}, which is held for the same "
                  f"reason: money moving between flagged accounts. The payment is held; recorded for the police.")
        ck._q("INSERT INTO LAYERING_RECORD (account_id, payer_account, txn_id, amount_inr, hold_id, recorded_at, "
              "detail, stmt_id) VALUES ({a}, {p}, {t}, {amt!d}, {h!d}, TO_TIMESTAMP({n}, 'YYYY-MM-DD HH24:MI:SS'), "
              "{d}, CURRENT_STATEMENT)", a=h["account_id"], p=txn["from_account"], t=txn["txn_id"],
              amt=str(_d(txn["amount_inr"])), h=str(hold_id), n=now, d=detail)
        _event(ck, h, "LAYERING_HELD", f"{now[11:16]}: the payer is itself flagged: recorded for the police as "
                                       f"layering, and no pre-alert (the payer is not someone to warn).", now)
        ck._audit("layering_recorded", h["case_id"], f"hold PH-H{hold_id}: {txn['from_account']} -> "
                                                     f"{h['account_id']}", now, h["account_id"])
        return "recorded as layering, no pre-alert"
    route = "fraud_team" if h["payer_type"] == "own_customer" else "cfcfrms"
    status = "sent" if pattern == "PIPE_FOR_FRAUD" else "waiting_for_officer"
    payer_bank = ck._q("SELECT bank_id FROM ACCOUNT WHERE account_id = {p}", p=txn["from_account"]).fetchval()
    text = alert_text(ck, h, txn)
    ck._q("INSERT INTO PRE_ALERT (hold_id, case_id, account_id, txn_id, utr, amount_inr, paid_at, payer_account, "
          "payer_bank_id, payer_type, route, alert_status, alert_text, created_at, stmt_id) VALUES ({h!d}, {c}, {a}, "
          "{t}, {u}, {amt!d}, TO_TIMESTAMP({pd}, 'YYYY-MM-DD HH24:MI:SS'), {pa}, {pb}, {pt}, {r}, "
          "'waiting_for_officer', "
          "{x}, TO_TIMESTAMP({n}, 'YYYY-MM-DD HH24:MI:SS'), CURRENT_STATEMENT)",
          h=str(hold_id), c=h["case_id"], a=h["account_id"], t=txn["txn_id"], u=txn["utr"],
          amt=str(_d(txn["amount_inr"])), pd=_ts(txn["ts"]), pa=txn["from_account"], pb=payer_bank,
          pt=h["payer_type"], r=route, x=text, n=now)
    alert_id = int(ck._q("SELECT MAX(pre_alert_id) FROM PRE_ALERT WHERE hold_id = {h!d}", h=str(hold_id)).fetchval())
    if status == "sent":
        send(ck, alert_id, "agent", now)
        return f"pre-alert {alert_id} sent at once ({'fraud team call' if route == 'fraud_team' else 'CFCFRMS'})"
    _event(ck, h, "PRE_ALERT_READY", f"{now[11:16]}: {name} is a Fast mover, so the pre-alert waits for the officer's "
                                     f"click at the morning check, together with its hold.", now)
    ck._audit("pre_alert_drafted", h["case_id"], f"pre-alert {alert_id} on hold PH-H{hold_id}: waiting for the "
                                                 f"officer", now, h["account_id"])
    return f"pre-alert {alert_id} waiting for the officer"


def send(ck, pre_alert_id: int, sent_by: str, now: str) -> str:
    """Send a pre-alert through its route: our own customer gets a fraud-team call task, another bank's customer a
    report to the police through CFCFRMS with the payer's bank and the UTR."""
    row = ck._q("SELECT hold_id, case_id, account_id, txn_id, utr, amount_inr, paid_at, payer_account, payer_bank_id, "
                "route, alert_status, alert_text FROM PRE_ALERT WHERE pre_alert_id = {p!d}",
                p=str(int(pre_alert_id))).fetchone()
    if not row:
        raise ValueError(f"No pre-alert {pre_alert_id}.")
    hold_id, case, acc, txn, utr, amount, paid, payer, payer_bank, route, status, text = row
    if status != "waiting_for_officer":
        raise ValueError(f"Pre-alert {pre_alert_id} is {status}, not waiting to be sent.")
    if route == "fraud_team":
        facts = (f"{inr(amount)} paid at {when(paid)} (UTR {utr or '-'}) from the customer's account ending "
                 f"{ending(payer)}.")
        ck._q("INSERT INTO FRAUD_TEAM_TASK (pre_alert_id, payer_account, call_script, facts_text, created_at, stmt_id) "
              "VALUES ({p!d}, {pa}, {s}, {f}, TO_TIMESTAMP({n}, 'YYYY-MM-DD HH24:MI:SS'), CURRENT_STATEMENT)",
              p=str(int(pre_alert_id)), pa=payer, s=CALL_SCRIPT, f=facts, n=now)
        where = "a call task for our fraud team"
    else:
        # the entry follows the proactive hold, so it names the hold's proposal (every portal entry names one)
        pid = ck._q("SELECT proposal_id FROM HOLD_LEDGER WHERE hold_id = {h!d}", h=str(int(hold_id))).fetchval()
        ck._q("INSERT INTO PORTAL_ENTRY (case_id, viewing_bank_id, entry_kind, from_account, account_id, bank_id, "
              "txn_id, utr, amount_inr, moved_at, detail, entry_status, filed_at, filed_by, proposal_id, stmt_id) "
              "VALUES ({c}, {b}, 'PRE_ALERT', {pa}, {a}, {pb}, {t}, {u}, {amt!d}, "
              "TO_TIMESTAMP({m}, 'YYYY-MM-DD HH24:MI:SS'), {d}, 'FILED', TO_TIMESTAMP({n}, 'YYYY-MM-DD HH24:MI:SS'), "
              "{by}, {p!d}, CURRENT_STATEMENT)",
              c=case, pa=payer, a=acc, pb=payer_bank, t=txn, u=utr, amt=str(_d(amount)), m=_ts(paid), d=text,
              n=now, by=sent_by[:60], p=str(int(pid)))
        where = "a report to the police through CFCFRMS, with the payer's bank and the UTR"
    ck._q("UPDATE PRE_ALERT SET alert_status = 'sent', sent_at = TO_TIMESTAMP({n}, 'YYYY-MM-DD HH24:MI:SS'), "
          "sent_by = {by} WHERE pre_alert_id = {p!d}", n=now, by=sent_by[:60], p=str(int(pre_alert_id)))
    h = _hold(ck, hold_id)
    who = "the agent, at once (Pipe for fraud)" if sent_by == "agent" else sent_by
    _event(ck, h, "PRE_ALERT_SENT", f"{now[11:16]}: pre-alert sent by {who}: {where}.", now)
    ck._audit("pre_alert_sent", case, f"pre-alert {int(pre_alert_id)} on hold PH-H{int(hold_id)} via {route}", now,
              acc, actor="checker" if sent_by == "agent" else sent_by)
    return where


# ---------------------------------------------------------------- endings: a ticket, a payer's answer

def on_ticket(ck, case_id: str, now: str) -> None:
    """A ticket was worked: a pre-alert whose payment it names is answered (the minutes it came early are recorded,
    the hold was linked by the checker); one whose payment is on its trail was layering, not needed (D-56)."""
    matched = ck.intake(case_id).get("matched_txn_id")
    if not matched:
        return
    received = ck._q("SELECT received_at FROM TICKET WHERE case_id = {c}", c=case_id).fetchval()
    came = _ts(received) if received else now
    trail = {t for (t,) in ck._q("SELECT DISTINCT txn_id FROM TRACE_EDGE WHERE case_id = {c}", c=case_id).fetchall()}
    for alert_id, hold_id, txn, status, _created in ck._q(
            "SELECT pre_alert_id, hold_id, txn_id, alert_status, created_at FROM PRE_ALERT "
            "WHERE alert_status IN ('sent', 'waiting_for_officer', 'confirmed')").fetchall():
        h = _hold(ck, hold_id)
        if txn == matched:
            early = max(0, int((_dt(came) - _dt(h["placed_at"])).total_seconds() // 60))
            ck._q("UPDATE PRE_ALERT SET alert_status = 'answered', "
                  "ended_at = TO_TIMESTAMP({n}, 'YYYY-MM-DD HH24:MI:SS'), "
                  "minutes_early = {m!d}, ending_note = {e} WHERE pre_alert_id = {p!d}", n=now, m=str(early),
                  e=f"Complaint {case_id} names the payment; the money was already held ({early} minutes early).",
                  p=str(int(alert_id)))
            _event(ck, h, "PRE_ALERT_ANSWERED", f"{now[11:16]}: complaint {case_id} names the payment held at "
                                                f"{h['placed_at'][11:16]}: it links to money already held, "
                                                f"{early} minutes early.", now)
            ck._audit("pre_alert_answered", case_id, f"pre-alert {int(alert_id)}: {early} minutes early", now,
                      h["account_id"])
        elif txn in trail and status != "confirmed":
            ck._q("UPDATE PRE_ALERT SET alert_status = 'not_needed', ended_at = TO_TIMESTAMP({n}, "
                  "'YYYY-MM-DD HH24:MI:SS'), ending_note = {e} WHERE pre_alert_id = {p!d}", n=now,
                  e=f"Complaint {case_id}'s trail shows the payment was layering.", p=str(int(alert_id)))
            _event(ck, h, "PRE_ALERT_NOT_NEEDED", f"{now[11:16]}: complaint {case_id}'s trail shows the held payment "
                                                  f"was the same scheme's money moving on: closed as not needed.", now)
            ck._audit("pre_alert_not_needed", case_id, f"pre-alert {int(alert_id)}: on the trail", now,
                      h["account_id"])


def mark_linked(ck, hold_id: int, case_id: str, now: str) -> None:
    """The checker linked a proactive hold to the complaint that named its credit: its clocks stop."""
    received = ck._q("SELECT received_at FROM TICKET WHERE case_id = {c}", c=case_id).fetchval()
    ck._q("UPDATE HOLD_LEDGER SET linked_case_id = {c}, linked_at = TO_TIMESTAMP({t}, 'YYYY-MM-DD HH24:MI:SS'), "
          "review_state = 'LINKED', closed_why = 'linked to a complaint' WHERE hold_id = {h!d} "
          "AND action_code = 'PROACTIVE_HOLD'", c=case_id, t=_ts(received) if received else now, h=str(int(hold_id)))


def apply_inputs(ck, now: str) -> list[str]:
    """The simulated inputs that reached the bank by now: holders' explanations, payers' answers, police directions."""
    out = []
    for acc, txn, at, text in ck._q(
            "SELECT account_id, txn_id, explained_at, explanation FROM HOLDER_EXPLANATION WHERE processed_at IS NULL "
            "AND explained_at <= TO_TIMESTAMP({n}, 'YYYY-MM-DD HH24:MI:SS') ORDER BY explained_at", n=now).fetchall():
        h = _active_proactive(ck, txn)
        ck._q("UPDATE HOLDER_EXPLANATION SET processed_at = TO_TIMESTAMP({n}, 'YYYY-MM-DD HH24:MI:SS') "
              "WHERE txn_id = {t} AND processed_at IS NULL", n=now, t=txn)
        if not h or h["legal_basis"] != "rbi_draft":
            continue
        decide = add_days(_ts(at), int(P["decide_after_explanation_days"]))
        ck._q("UPDATE HOLD_LEDGER SET explained_at = TO_TIMESTAMP({e}, 'YYYY-MM-DD HH24:MI:SS'), "
              "decide_by = TO_TIMESTAMP({d}, 'YYYY-MM-DD HH24:MI:SS'), review_state = 'EXPLAINED' "
              "WHERE hold_id = {h!d}", e=_ts(at), d=decide, h=str(h["hold_id"]))
        s = (f"{_ts(at)[11:16]} on {day_label(at)}: the holder explained the held {inr(h['amount_inr'])} "
             f"(\"{(text or '')[:120]}\"). Release, or keep and refer to the police, is due by {day_label(decide)}.")
        _event(ck, h, "HOLDER_EXPLAINED", s, _ts(at))
        ck._audit("holder_explained", h["case_id"], f"hold PH-H{h['hold_id']}: decide by {decide}", _ts(at), acc)
        out.append(s)
    for txn, response, at, note in ck._q(
            "SELECT txn_id, response, responded_at, note FROM PAYER_RESPONSE WHERE processed_at IS NULL "
            "AND responded_at <= TO_TIMESTAMP({n}, 'YYYY-MM-DD HH24:MI:SS') ORDER BY responded_at", n=now).fetchall():
        ck._q("UPDATE PAYER_RESPONSE SET processed_at = TO_TIMESTAMP({n}, 'YYYY-MM-DD HH24:MI:SS') "
              "WHERE txn_id = {t} AND processed_at IS NULL", n=now, t=txn)
        out += payer_answer(ck, txn, response, _ts(at), note)
    for acc, txn, direction, ref, at in ck._q(
            "SELECT account_id, txn_id, direction, reference, directed_at FROM POLICE_DIRECTION WHERE processed_at IS "
            "NULL AND directed_at <= TO_TIMESTAMP({n}, 'YYYY-MM-DD HH24:MI:SS') ORDER BY directed_at",
            n=now).fetchall():
        ck._q("UPDATE POLICE_DIRECTION SET processed_at = TO_TIMESTAMP({n}, 'YYYY-MM-DD HH24:MI:SS') "
              "WHERE txn_id = {t} AND processed_at IS NULL", n=now, t=txn)
        h = _active_proactive(ck, txn)
        if not h:
            continue
        if direction == "KEEP":
            ck._q("UPDATE HOLD_LEDGER SET police_direction = 'KEEP', review_state = 'POLICE_KEEP' "
                  "WHERE hold_id = {h!d}", h=str(h["hold_id"]))
            s = (f"{day_label(at)}: the police directed the bank to keep the hold ({ref}); the 60-day limit no longer "
                 f"applies, the hold follows the direction.")
            _event(ck, h, "POLICE_KEEP", s, _ts(at))
        else:
            _release(ck, h, f"released on the police's direction ({ref})", _ts(at), "police")
            s = f"{day_label(at)}: the police directed the bank to release the hold ({ref}): released."
        ck._audit("police_direction", h["case_id"], f"hold PH-H{h['hold_id']}: {direction} {ref}", _ts(at), acc)
        out.append(s)
    return out


def _active_proactive(ck, txn_id: str) -> dict:
    hid = ck._q("SELECT MAX(hold_id) FROM HOLD_LEDGER WHERE source_txn_id = {t} AND action_code = 'PROACTIVE_HOLD' "
                "AND hold_status = 'ACTIVE'", t=txn_id).fetchval()
    return _hold(ck, hid) if hid else {}


def payer_answer(ck, txn_id: str, response: str, at: str, note: str | None) -> list[str]:
    """reported_fraud with no ticket yet: confirmed (for our own customer, the NCRP complaint the bank files for
    them is drafted); genuine: a release of that one hold goes to the officer's queue."""
    row = ck._q("SELECT pre_alert_id, hold_id, alert_status, payer_type, payer_account, amount_inr, paid_at, utr, "
                "account_id FROM PRE_ALERT WHERE txn_id = {t} ORDER BY pre_alert_id DESC LIMIT 1", t=txn_id).fetchone()
    if not row:
        return []
    alert_id, hold_id, status, kind, payer, amount, paid, utr, acc = row
    if status in ("answered", "not_needed"):
        return []
    h = _hold(ck, hold_id)
    if response == "reported_fraud":
        draft = None
        if kind == "own_customer":
            draft = (f"Complaint filed by {ck.bank} on behalf of its customer (account ending {ending(payer)}): at "
                     f"{when(paid)} the customer paid {inr(amount)} (UTR {utr or '-'}) into account {acc} at this "
                     f"bank, "
                     f"which is held, and says the payment was obtained by fraud. Filed on NCRP for the customer.")
        ck._q("UPDATE PRE_ALERT SET alert_status = 'confirmed', ending_note = {e}, complaint_draft = {d} "
              "WHERE pre_alert_id = {p!d}", e=f"The payer says they were defrauded ({note or 'no note'}).",
              d=draft, p=str(int(alert_id)))
        s = (f"{at[11:16]}: the payer answered the pre-alert: the payment was obtained by fraud. The money is already "
             f"held" + ("; the NCRP complaint the bank files for its customer is drafted." if draft else
                        "; their complaint will link to it."))
        _event(ck, h, "PRE_ALERT_CONFIRMED", s, at)
        ck._audit("pre_alert_confirmed", h["case_id"], f"pre-alert {int(alert_id)}", at, acc)
        return [s]
    if response == "genuine":
        ck._q("UPDATE PRE_ALERT SET alert_status = 'genuine', ended_at = TO_TIMESTAMP({n}, 'YYYY-MM-DD HH24:MI:SS'), "
              "ending_note = {e} WHERE pre_alert_id = {p!d}", n=at,
              e=f"The payer says it was a genuine payment ({note or 'no note'}).", p=str(int(alert_id)))
        if h and h["hold_status"] == "ACTIVE":
            ck._q("UPDATE HOLD_LEDGER SET review_state = 'GENUINE' WHERE hold_id = {h!d}", h=str(int(hold_id)))
        s = (f"{at[11:16]}: the payer says the payment was genuine ({note or 'no note'}): a release of that one hold "
             f"waits for the officer.")
        _event(ck, h, "PRE_ALERT_GENUINE", s, at)
        ck._audit("pre_alert_genuine", h["case_id"], f"pre-alert {int(alert_id)}", at, acc)
        return [s]
    return []


# ---------------------------------------------------------------- Module 4: the clock sweep

def _active_draft_holds(ck) -> list[dict]:
    return [_hold(ck, hid) for (hid,) in ck._q(
        "SELECT hold_id FROM HOLD_LEDGER WHERE action_code = 'PROACTIVE_HOLD' AND hold_status = 'ACTIVE' "
        "ORDER BY hold_id").fetchall()]


def _release(ck, h: dict, why: str, now: str, by: str, miss: bool = False) -> None:
    ck._q("UPDATE HOLD_LEDGER SET hold_status = 'RELEASED', review_state = 'CLOSED', closed_why = {w}, "
          f"compliance_miss = {'TRUE' if miss else 'FALSE'} WHERE hold_id = {{h!d}}", w=why[:60],
          h=str(int(h["hold_id"])))
    from pipeline.reasons import holder_message

    if h["case_id"].startswith("REGISTRY:"):
        text = f"The temporary debit hold on your account ending {ending(h['account_id'])} has been lifted."
    else:
        text = holder_message(ck.complaint(h["case_id"]), h["account_id"], 0, "RELEASED", now)
    ck._q("INSERT INTO HOLDER_MESSAGE (case_id, account_id, hold_id, message_text, written_at, stmt_id) VALUES "
          "({c}, {a}, {h!d}, {t}, TO_TIMESTAMP({n}, 'YYYY-MM-DD HH24:MI:SS'), CURRENT_STATEMENT)",
          c=h["case_id"], a=h["account_id"], h=str(int(h["hold_id"])), t=text, n=now)
    kind = "HOLD_RELEASED_LIMIT" if miss or why in (LIMIT_RELEASE, NO_POLICE_RELEASE) else "HOLD_RELEASED"
    _event(ck, h, kind, f"{day_label(now)}: the proactive hold of {inr(h['amount_inr'])} was {why}.", now)
    ck._audit("proactive_hold_released", h["case_id"], json.dumps({"hold_id": h["hold_id"], "why": why, "by": by,
                                                                   "compliance_miss": miss}), now, h["account_id"],
              actor=by if by not in ("checker", "police") else "checker")


def sweep(ck, now: str) -> list[str]:
    """Run the draft's clocks on every rbi_draft proactive hold (D-63). One rule covers every timeout: never release
    early without a decision, and never hold past the limit without one."""
    out = apply_inputs(ck, now)
    head = P["escalate_to"]
    for h in _active_draft_holds(ck):
        if h["review_state"] == "LINKED" or h["legal_basis"] != "rbi_draft":
            continue
        name = ck._q("SELECT COALESCE(holder_name, account_id) FROM ACCOUNT WHERE account_id = {a}",
                     a=h["account_id"]).fetchval() or h["account_id"]
        state = h["review_state"]
        if state == "NEXT_DAY_CHECK" and not h["escalated_at"] and h["check_due_at"] and now >= h["check_due_at"]:
            ck._q("UPDATE HOLD_LEDGER SET escalated_at = TO_TIMESTAMP({n}, 'YYYY-MM-DD HH24:MI:SS') "
                  "WHERE hold_id = {h!d}", n=now, h=str(h["hold_id"]))
            s = (f"{now[11:16]} on {day_label(now)}: the next-day check of the {inr(h['amount_inr'])} held on {name} "
                 f"was not done: the hold stays and the case goes to the {head}.")
            _event(ck, h, "ESCALATED", s, now)
            ck._audit("escalated", h["case_id"], f"hold PH-H{h['hold_id']}: next-day check missed", now,
                      h["account_id"])
            out.append(s)
        if (state in ("NEXT_DAY_CHECK", "CONFIRMED") and not h["explained_at"] and h["decide_by"]
                and now >= h["decide_by"]):
            ck._q("UPDATE HOLD_LEDGER SET review_state = 'REFER_DRAFTED', escalated_at = "
                  "TO_TIMESTAMP({n}, 'YYYY-MM-DD HH24:MI:SS') WHERE hold_id = {h!d}", n=now, h=str(h["hold_id"]))
            s = (f"{day_label(now)}: no explanation in 30 days for the {inr(h['amount_inr'])} held on {name}. The "
                 f"referral to the police is drafted and at the top of the queue (keep and refer, one click); the "
                 f"case also goes to the {head}.")
            _event(ck, h, "REFER_DRAFTED", s, now)
            ck._audit("refer_drafted", h["case_id"], f"hold PH-H{h['hold_id']}: day 30, no explanation", now,
                      h["account_id"])
            out.append(s)
        must = add_days(h["placed_at"], int(P["must_decide_day"]))
        if (h["review_state"] not in ("REFERRED", "POLICE_KEEP") and not h["must_decide_at"] and now >= must
                and h["police_direction"] != "KEEP"):
            ck._q("UPDATE HOLD_LEDGER SET must_decide_at = TO_TIMESTAMP({n}, 'YYYY-MM-DD HH24:MI:SS') "
                  "WHERE hold_id = {h!d}", n=now, h=str(h["hold_id"]))
            s = (f"{day_label(now)}: must decide: the {inr(h['amount_inr'])} held on {name} ends in 10 days "
                 f"({day_label(h['expires_on'])}) unless the bank keeps and refers it. Alert to the officer and the "
                 f"{head}.")
            _event(ck, h, "MUST_DECIDE", s, now)
            ck._audit("must_decide", h["case_id"], f"hold PH-H{h['hold_id']}: day 50", now, h["account_id"])
            out.append(s)
        if (h["review_state"] == "REFERRED" and h["police_direction"] is None and h["police_reply_due_on"]
                and now >= h["police_reply_due_on"]):
            _release(ck, h, NO_POLICE_RELEASE, now, "checker")
            out.append(f"{day_label(now)}: {name}: the proactive hold was {NO_POLICE_RELEASE}.")
            continue
        if h["police_direction"] != "KEEP" and h["expires_on"] and now >= h["expires_on"]:
            _release(ck, h, LIMIT_RELEASE, now, "checker", miss=True)
            out.append(f"{day_label(now)}: {name}: the proactive hold was {LIMIT_RELEASE} (a compliance miss).")
    return out


def next_due(conn, now: str) -> str | None:
    """The next moment the sweep has something to do after now: a clock, or a simulated input arriving. One query:
    the night asks this before every event."""
    live = ("action_code = 'PROACTIVE_HOLD' AND hold_status = 'ACTIVE' AND legal_basis = 'rbi_draft' "
            "AND COALESCE(review_state, '-') <> 'LINKED'")
    after = "> TO_TIMESTAMP({n}, 'YYYY-MM-DD HH24:MI:SS')"
    parts = [f"SELECT {col} AS due FROM HOLD_LEDGER WHERE {live} AND {col} {after}"
             for col in ("check_due_at", "decide_by", "police_reply_due_on", "expires_on")]
    parts.append("SELECT ADD_DAYS(placed_at, {d!d}) FROM HOLD_LEDGER WHERE action_code = 'PROACTIVE_HOLD' "
                 "AND hold_status = 'ACTIVE' AND legal_basis = 'rbi_draft' AND must_decide_at IS NULL "
                 f"AND ADD_DAYS(placed_at, {{d!d}}) {after}")
    # an input that has arrived but not been handled yet is due now
    parts += [f"SELECT GREATEST({col}, TO_TIMESTAMP({{n}}, 'YYYY-MM-DD HH24:MI:SS')) FROM {table} "
              f"WHERE processed_at IS NULL"
              for table, col in (("HOLDER_EXPLANATION", "explained_at"), ("PAYER_RESPONSE", "responded_at"),
                                 ("POLICE_DIRECTION", "directed_at"))]
    v = conn.execute("SELECT MIN(due) FROM (" + " UNION ALL ".join(parts) + ")",
                     {"n": now, "d": str(int(P["must_decide_day"]))}).fetchval()
    return None if v is None else _ts(v)


# ---------------------------------------------------------------- the officer's decisions (through the checker)

def _need(officer: str | None, reason: str | None) -> tuple[str, str]:
    from pipeline.checker import DecisionError

    officer, reason = (officer or "").strip(), (reason or "").strip()
    if not officer:
        raise DecisionError("An officer name is required: nothing is decided anonymously.")
    if not reason:
        raise DecisionError("Write a reason: every officer action goes into the audit log with its reason.")
    return officer, reason


def _decision_row(ck, h: dict, code: str, officer: str, reason: str, now: str) -> int:
    ck._q("INSERT INTO DECISION (case_id, viewing_bank_id, account_id, action_code, decision_outcome, amount_inr, "
          "proposed_amount, decided_by, decided_at, reason_text, statement_ids) VALUES ({c}, {b}, {a}, {k}, "
          "'APPROVED', "
          "{amt!d}, {amt!d}, {o}, TO_TIMESTAMP({n}, 'YYYY-MM-DD HH24:MI:SS'), {r}, {ids})",
          c=h["case_id"], a=h["account_id"], k=code, amt=str(_d(h["amount_inr"])), o=officer, n=now, r=reason,
          ids=f"hold:PH-H{h['hold_id']} snapshot:{h.get('snapshot_id')} mode:{h.get('hold_mode')}")
    return int(ck._q("SELECT MAX(decision_id) FROM DECISION WHERE account_id = {a} AND action_code = {k}",
                     a=h["account_id"], k=code).fetchval())


def _get_active(ck, hold_id: int) -> dict:
    from pipeline.checker import DecisionError

    h = _hold(ck, hold_id)
    if not h or not h.get("proactive"):
        raise DecisionError(f"No proactive hold PH-H{hold_id}.")
    if h["hold_status"] != "ACTIVE":
        raise DecisionError(f"Hold PH-H{hold_id} is {str(h['hold_status']).lower()}, not active.")
    if h["review_state"] == "LINKED":
        raise DecisionError(f"Hold PH-H{hold_id} is linked to a complaint now: it follows the police process.")
    return h


def is_clear_case(ck, h: dict) -> bool:
    """Bulk confirm accepts only these: Pipe for fraud, both scores outside the grey zone, no payer response yet."""
    snap = snapshot(ck.conn, h["account_id"], h.get("snapshot_id")) if h.get("snapshot_id") else None
    if not snap or snap["account_pattern"] != "PIPE_FOR_FRAUD":
        return False
    if any(LOW_BELOW <= v < HIGH_AT for v in (snap["pass_through"], snap["fraud_exposure"])):
        return False
    answered = ck._q("SELECT COUNT(*) FROM PAYER_RESPONSE WHERE txn_id = {t} AND processed_at IS NOT NULL",
                     t=h["source_txn_id"]).fetchval()
    return not int(answered or 0) and h["review_state"] == "NEXT_DAY_CHECK"


def confirm(ck, hold_id: int, officer: str, reason: str, now: str, bulk: bool = False) -> int:
    from pipeline.checker import DecisionError

    officer, reason = _need(officer, reason)
    h = _get_active(ck, hold_id)
    if h["review_state"] != "NEXT_DAY_CHECK":
        raise DecisionError(f"Hold PH-H{hold_id} is not waiting for its next-day check.")
    if bulk and not is_clear_case(ck, h):
        raise DecisionError(f"Hold PH-H{hold_id} is not a clear case: confirm it on its own card.")
    ck._q("UPDATE HOLD_LEDGER SET review_state = 'CONFIRMED', checked_at = TO_TIMESTAMP({n}, 'YYYY-MM-DD HH24:MI:SS'), "
          "checked_by = {o} WHERE hold_id = {h!d}", n=now, o=officer, h=str(int(hold_id)))
    d = _decision_row(ck, h, "PROACTIVE_CONFIRM", officer, reason, now)
    ck._audit("officer_confirmed", h["case_id"], f"hold PH-H{hold_id} confirmed{' (bulk, clear case)' if bulk else ''}"
                                                 f", decision {d}: {reason}", now, h["account_id"], actor=officer)
    return d


def release(ck, hold_id: int, officer: str, reason: str, now: str) -> int:
    officer, reason = _need(officer, reason)
    h = _get_active(ck, hold_id)
    after = h["review_state"] in ("EXPLAINED", "GENUINE") or h["explained_at"]
    why = "released after an explanation" if after else "released by the officer"
    _release(ck, h, why, now, officer)
    d = _decision_row(ck, h, "PROACTIVE_RELEASE", officer, reason, now)
    ck._audit("officer_released", h["case_id"], f"hold PH-H{hold_id} released, decision {d}: {reason}", now,
              h["account_id"], actor=officer)
    return d


def referral_text(ck, h: dict) -> str:
    name = ck._q("SELECT COALESCE(holder_name, account_id) FROM ACCOUNT WHERE account_id = {a}",
                 a=h["account_id"]).fetchval() or h["account_id"]
    explained = (f"The holder explained on {day_label(h['explained_at'])}; the bank was not satisfied."
                 if h["explained_at"] else "The holder gave no explanation within 20 days.")
    return (f"Referral to the police through NCRP-CFCFRMS: a temporary debit hold of {inr(h['amount_inr'])} was placed "
            f"on {day_label(h['placed_at'])} on a first-time payer's credit into {name} (account ending "
            f"{ending(h['account_id'])}), {notice_words(ck, h['case_id'])}. {explained} The bank keeps the hold and "
            f"asks for a direction; without one by {day_label(h['police_reply_due_on'] or h['expires_on'])} the hold "
            f"is removed. {RULES['legal_basis']['referral']}.")


def keep_and_refer(ck, hold_id: int, officer: str, reason: str, now: str) -> int:
    from pipeline.checker import DecisionError

    officer, reason = _need(officer, reason)
    h = _get_active(ck, hold_id)
    if h["legal_basis"] != "rbi_draft":
        raise DecisionError("This hold is under the existing police notice: it follows the police process already.")
    if h["review_state"] in ("REFERRED", "POLICE_KEEP"):
        raise DecisionError(f"Hold PH-H{hold_id} is already with the police.")
    due = min(add_days(now, int(P["police_reply_days"])), h["expires_on"] or add_days(now, 30))
    ck._q("UPDATE HOLD_LEDGER SET review_state = 'REFERRED', referred_at = TO_TIMESTAMP({n}, 'YYYY-MM-DD HH24:MI:SS'), "
          "police_reply_due_on = TO_TIMESTAMP({d}, 'YYYY-MM-DD HH24:MI:SS') WHERE hold_id = {h!d}",
          n=now, d=due, h=str(int(hold_id)))
    h = _hold(ck, hold_id)
    text = referral_text(ck, h)
    ck._q("INSERT INTO PORTAL_ENTRY (case_id, viewing_bank_id, entry_kind, account_id, txn_id, amount_inr, detail, "
          "entry_status, filed_at, filed_by, proposal_id, stmt_id) VALUES ({c}, {b}, 'REFERRAL', {a}, {t}, {amt!d}, "
          "{d}, 'FILED', TO_TIMESTAMP({n}, 'YYYY-MM-DD HH24:MI:SS'), {o}, {p!d}, CURRENT_STATEMENT)",
          c=h["case_id"], a=h["account_id"], t=h["source_txn_id"], amt=str(_d(h["amount_inr"])), d=text, n=now,
          o=officer, p=str(int(h["proposal_id"])))
    d = _decision_row(ck, h, "PROACTIVE_KEEP_REFER", officer, reason, now)
    _event(ck, h, "REFERRED", f"{day_label(now)}: {officer} kept the hold and referred it to the police; a direction "
                              f"is due by {day_label(due)}.", now)
    ck._audit("officer_kept_and_referred", h["case_id"], f"hold PH-H{hold_id}, decision {d}: {reason}", now,
              h["account_id"], actor=officer)
    return d


def send_by_officer(ck, pre_alert_id: int, officer: str, reason: str, now: str) -> int:
    officer, reason = _need(officer, reason)
    hold_id = ck._q("SELECT hold_id FROM PRE_ALERT WHERE pre_alert_id = {p!d}", p=str(int(pre_alert_id))).fetchval()
    h = _hold(ck, hold_id) if hold_id else {}
    if not h:
        from pipeline.checker import DecisionError

        raise DecisionError(f"No pre-alert {pre_alert_id}.")
    try:
        send(ck, pre_alert_id, officer, now)
    except ValueError as exc:
        from pipeline.checker import DecisionError

        raise DecisionError(str(exc)) from exc
    return _decision_row(ck, h, "PRE_ALERT_SEND", officer, reason, now)


# ---------------------------------------------------------------- what the console reads

def signals_of(snap: dict | None) -> list[dict]:
    """A snapshot's signals_text ("name=value/m; ...") back into rows like ACCOUNT_SIGNALS."""
    out = []
    for part in str((snap or {}).get("signals_text") or "").split(";"):
        name, _, rest = part.strip().partition("=")
        value, _, m = rest.partition("/")
        if not name or not m:
            continue
        out.append({"signal_name": name, "signal_value": None if value == "none" else float(value),
                    "m_value": float(m)})
    return out


def hold_cards(conn, now: str | None = None) -> list[dict]:
    """Every proactive hold with what the officer needs on its card (Module 6)."""
    cols = ("hold_id", "case_id", "account_id", "amount_inr", "source_txn_id", "placed_at", "hold_status",
            "legal_basis", "payer_type", "payer_account", "snapshot_id", "hold_mode", "check_due_at", "checked_at",
            "checked_by", "explanation_due_on", "explained_at", "decide_by", "expires_on", "referred_at",
            "police_reply_due_on", "police_direction", "review_state", "escalated_at", "must_decide_at", "closed_why",
            "compliance_miss", "linked_case_id", "linked_at")
    out = []
    for row in conn.execute(f"SELECT {', '.join('h.' + c for c in cols)}, acc.holder_name, t.utr, t.ts, t.channel, "
                            "pb.bank_name, pc.checks_json, acc.opened_on, "
                            "(SELECT MAX(w.eligibility_reason) FROM WATCH_LIST w WHERE w.account_id = h.account_id) "
                            "FROM HOLD_LEDGER h "
                            "LEFT JOIN ACCOUNT acc ON acc.account_id = h.account_id "
                            "LEFT JOIN TXN t ON t.txn_id = h.source_txn_id "
                            "LEFT JOIN ACCOUNT pa ON pa.account_id = h.payer_account "
                            "LEFT JOIN BANK pb ON pb.bank_id = pa.bank_id "
                            "LEFT JOIN PAYMENT_CHECK pc ON pc.txn_id = h.source_txn_id AND pc.check_result = 'hold' "
                            "WHERE h.action_code = 'PROACTIVE_HOLD' ORDER BY h.placed_at, h.hold_id").fetchall():
        h = dict(zip(cols, row[: len(cols)], strict=True))
        name, utr, paid, channel, payer_bank, checks, opened_on, eligibility = row[len(cols):]
        for k, v in list(h.items()):
            if k.endswith(("_at", "_on", "decide_by")) and v is not None:
                h[k] = _ts(v)
        snap = snapshot(conn, h["account_id"], h["snapshot_id"]) if h["snapshot_id"] else None
        reasons = []
        if snap:
            for score_name in ("pass_through", "fraud_exposure"):
                value = snap[score_name]
                rank = "high_rank" if value >= 0.5 else "low_rank"
                reasons += [s for (s,) in conn.execute(
                    f"SELECT sentence FROM ACCOUNT_SIGNALS WHERE case_id = {{c}} AND account_id = {{a}} "
                    f"AND score_name = {{s}} AND sentence IS NOT NULL ORDER BY {rank} LIMIT 3",
                    {"c": snap["case_id"], "a": h["account_id"], "s": score_name}).fetchall()]
        alert = conn.execute("SELECT pre_alert_id, alert_status, route, alert_text, sent_at, sent_by, minutes_early, "
                             "ending_note FROM PRE_ALERT WHERE hold_id = {h!d}",
                             {"h": str(int(h["hold_id"]))}).fetchone()
        h.update({
            "name": name or h["account_id"], "utr": utr, "paid_at": _ts(paid) if paid else None, "channel": channel,
            "payer_bank": payer_bank, "payer_ending": ending(h["payer_account"]),
            "checks": json.loads(checks) if checks else [],
            "account_pattern": snap["account_pattern"] if snap else None,
            "type_name": type_name(snap["account_pattern"] if snap else None),
            "pass_through": snap["pass_through"] if snap else None,
            "fraud_exposure": snap["fraud_exposure"] if snap else None,
            "reasons": reasons[:3],
            # the signals as they stood when the hold was decided, so its card shows the numbers of that moment
            "signals": signals_of(snap),
            "opened_on": str(opened_on)[:10] if opened_on else None,
            "eligibility_reason": eligibility,
            "pre_alert": dict(zip(("pre_alert_id", "status", "route", "text", "sent_at", "sent_by", "minutes_early",
                                   "ending_note"), alert, strict=True)) if alert else None,
            "days_left": None if not (h["expires_on"] and now) else max(0, (_dt(h["expires_on"]) - _dt(now)).days),
            "with_department_head": bool(h["escalated_at"]),
            "clear_case": False,
        })
        if h["pre_alert"] and h["pre_alert"]["sent_at"]:
            h["pre_alert"]["sent_at"] = _ts(h["pre_alert"]["sent_at"])
        h["clear_case"] = (h["account_pattern"] == "PIPE_FOR_FRAUD" and h["review_state"] == "NEXT_DAY_CHECK"
                           and snap is not None and not any(LOW_BELOW <= v < HIGH_AT for v in
                                                           (snap["pass_through"], snap["fraud_exposure"]))
                           and not (h["pre_alert"] and h["pre_alert"]["status"] in ("confirmed", "genuine")))
        out.append(h)
    return out


def queue_items(cards: list[dict]) -> list[dict]:
    """The officer's queue items this design adds (Module 6.3), most urgent first."""
    items = []
    for h in cards:
        if h["hold_status"] != "ACTIVE" or h["review_state"] == "LINKED":
            continue
        kind = None
        if h["review_state"] == "REFER_DRAFTED":
            kind, title, rank = "refer_drafted", "Day 30, no explanation: keep and refer (drafted)", 0
        elif h["must_decide_at"] and h["review_state"] not in ("REFERRED", "POLICE_KEEP"):
            kind, title, rank = "must_decide", "Must decide: the hold ends in 10 days", 1
        elif h["review_state"] == "EXPLAINED":
            kind, title, rank = "explained", "The holder explained: release, or keep and refer", 2
        elif h["review_state"] == "GENUINE":
            kind, title, rank = "genuine", "Payer says genuine: release this hold?", 3
        if kind:
            items.append({**h, "item": kind, "item_title": title, "rank": rank})
    return sorted(items, key=lambda i: (i["rank"], i["placed_at"]))


def outcomes(conn) -> dict:
    """Module 7: the three numbers beside the existing ones on the compare screen (from synthetic data)."""
    rows = conn.execute("SELECT outcome, minutes_to_complaint FROM PROACTIVE_HOLD_OUTCOME").fetchall()
    closed = [o for o, _ in rows if o != "open"]
    confirmed = sum(1 for o in closed if o == "confirmed_by_victim")
    released = sum(1 for o in closed if o == "released_after_explanation")
    early = sorted(int(m) for o, m in rows if m is not None and o == "confirmed_by_victim")
    median = None
    if early:
        mid = len(early) // 2
        median = early[mid] if len(early) % 2 else (early[mid - 1] + early[mid]) / 2
    return {"proactive_holds": len(rows), "closed": len(closed), "open": len(rows) - len(closed),
            "confirmed_by_victim": confirmed, "released_after_explanation": released,
            "confirmed_share": confirmed / len(closed) if closed else None,
            "released_share": released / len(closed) if closed else None,
            "median_minutes_early": median,
            "by_outcome": {o: sum(1 for x, _ in rows if x == o) for o in
                           ("confirmed_by_victim", "released_after_explanation", "layering", "released_at_limit",
                            "open")},
            "note": "from synthetic data: what a bank would track, not results"}