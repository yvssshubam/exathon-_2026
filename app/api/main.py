"""Proportionate Holds API (FastAPI). The officer console talks only to this.

The agent (pipeline/agent.py) works tickets; every number comes from Exasol; officer decisions go through
pipeline/officer.py, which enforces the rules. This layer only routes requests and shapes JSON. The only holds that
exist without an officer are the ones the rules require; no endpoint can place a full freeze without an officer's
name and written reasons.

Start it:   python -m app.api            (http://127.0.0.1:8000, interactive docs at /docs)
"""
from __future__ import annotations

import time
from collections.abc import Iterator
from decimal import Decimal
from pathlib import Path

import yaml
from fastapi import Depends, FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, Field

from pipeline import officer, steps
from pipeline.explain import explain, headline

ROOT = Path(__file__).resolve().parents[2]
BANKS = yaml.safe_load((ROOT / "config" / "banks.yaml").read_text(encoding="utf-8"))
RULES = yaml.safe_load((ROOT / "config" / "rules.yaml").read_text(encoding="utf-8"))
SCORING = yaml.safe_load((ROOT / "config" / "scoring.yaml").read_text(encoding="utf-8"))
DESK_OPENS, DESK_CLOSES = 10, 18          # hours; a ticket outside them is one today's desk would not see

app = FastAPI(title="Proportionate Holds API", version="0.2.0",
              description="A golden-hour copilot for the bank nodal officer: the agent works every ticket the moment "
                          "it lands; the officer decides every judgment call.")
app.add_middleware(CORSMiddleware, allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
                   allow_methods=["*"], allow_headers=["*"])


@app.get("/", include_in_schema=False)
def root() -> RedirectResponse:
    """The bare address opens the interactive docs. 307 (temporary) so browsers never cache it."""
    return RedirectResponse("/docs", status_code=307)


# ---------------------------------------------------------------- helpers

def get_conn() -> Iterator:
    """One Exasol session per request, so CURRENT_STATEMENT and the audit log stay consistent."""
    from pipeline.db import connect

    conn = connect()
    try:
        yield conn
    finally:
        conn.close()


def clean(value):
    if isinstance(value, Decimal):
        return float(value)
    if hasattr(value, "isoformat"):
        return str(value)[:19]
    if isinstance(value, str) and len(value) >= 19 and value[4] == "-" and value[10] == " ":
        return value[:19]
    return value


def number(value, scale: int):
    """pyexasol returns DECIMAL columns as strings ('500', '80000.00'); the console wants JSON numbers."""
    if value is None or isinstance(value, (int, float)):
        return value
    return int(value) if scale == 0 and "." not in str(value) else float(value)


def rows(conn, sql: str, params: dict | None = None) -> list[dict]:
    stmt = conn.execute(sql, params or {})
    types = list(stmt.columns().values())
    names = [c.lower() for c in stmt.column_names()]
    out = []
    for r in stmt.fetchall():
        out.append({n: number(v, t.get("scale", 0)) if t.get("type") == "DECIMAL" else clean(v)
                    for n, t, v in zip(names, types, r, strict=True)})
    return out


def one(conn, sql: str, params: dict | None = None) -> dict | None:
    found = rows(conn, sql, params)
    return found[0] if found else None


def check_bank(bank: str) -> str:
    if bank not in BANKS:
        raise HTTPException(400, f"Unknown bank {bank!r}; known: {sorted(BANKS)}")
    return bank


def check_case(conn, case_id: str) -> dict:
    c = one(conn, "SELECT * FROM PH.COMPLAINT WHERE case_id = {c}", {"c": case_id})
    if c is None:
        raise HTTPException(404, f"No case {case_id!r}")
    return c


def bank_name(bank_id: str | None) -> str | None:
    return BANKS.get(bank_id or "", {}).get("name") if bank_id else None


def after_hours(ts: str | None) -> bool:
    if not ts:
        return False
    hour = int(str(ts)[11:13])
    return hour < DESK_OPENS or hour >= DESK_CLOSES


def present(action: dict, names: dict | None = None) -> dict:
    """An action row plus its plain-language headline and sentences."""
    out = {k: clean(v) for k, v in action.items()}
    out["headline"] = headline(action)
    out["explanation"] = explain(action)
    out["bank_name"] = bank_name(action.get("bank_id"))
    if names is not None:
        out["name"] = names.get(action["account_id"], action["account_id"])
    return out


def holder_names(conn) -> dict:
    return {r["account_id"]: r["holder_name"] or r["account_id"]
            for r in rows(conn, "SELECT account_id, holder_name FROM PH.ACCOUNT")}


ACTION_SQL = """
    SELECT account_id, bank_id, hop, action_code, act_mode, amount_inr, current_hold_inr, victim_amount_inr,
           other_victims_inr, balance_now_inr, trail_amount_inr, band, score, reason_code, legal_basis,
           action_status, stmt_id
    FROM PH.CASE_ACTION WHERE case_id = {c} AND viewing_bank_id = {b}"""


# ---------------------------------------------------------------- reference data

@app.get("/api/health")
def health(conn=Depends(get_conn)):
    version = conn.execute(
        "SELECT PARAM_VALUE FROM EXA_METADATA WHERE PARAM_NAME = 'databaseProductVersion'").fetchval()
    return {"ok": True, "exasol": version, "session": conn.session_id()}


@app.get("/api/config")
def config():
    from pipeline.db import settings
    from pipeline.llm import settings as llm_settings

    try:
        own = settings()["own_bank_id"]
    except SystemExit:
        own = "BANK_X"
    llm = llm_settings()
    return {
        "own_bank_id": own,
        "banks": [{"bank_id": k, **v} for k, v in BANKS.items()],
        "automatic": RULES["automatic"],
        "officer": RULES["officer"],
        "legal_basis": RULES["legal_basis"],
        "lane": RULES["lane_a"],
        "model": {"enabled": llm["enabled"], "name": llm["model"] if llm["enabled"] else None},
        "desk_hours": [DESK_OPENS, DESK_CLOSES],
    }


# ---------------------------------------------------------------- the ticket queue and the agent

@app.get("/api/queue")
def queue(bank: str = Query("BANK_X"), conn=Depends(get_conn)):
    """The simulated CFCFRMS queue: every ticket, when it landed and what the agent did with it."""
    check_bank(bank)
    out = rows(conn, """
        SELECT t.case_id, t.received_at, t.ticket_status, t.run_id, c.amount_inr, c.utr, c.complaint_source,
               k.notice_status, k.problems, r.run_status, r.run_mode, r.wall_seconds, r.sim_holds_at
        FROM PH.TICKET t
        JOIN PH.COMPLAINT c ON c.case_id = t.case_id
        JOIN PH.COMPLAINT_CHECK k ON k.case_id = t.case_id
        LEFT JOIN PH.AGENT_RUN r ON r.run_id = t.run_id
        ORDER BY t.received_at, t.case_id""")
    for t in out:
        t["after_hours"] = after_hours(t["received_at"])
    return out


def _work(conn, case_id: str, bank: str, mode: str) -> dict:
    from pipeline.agent import work_ticket

    try:
        return work_ticket(conn, case_id, bank, mode)
    except steps.StepFailed as exc:
        raise HTTPException(500, str(exc)) from exc


@app.post("/api/tickets/{case_id}/work")
def work_one(case_id: str, bank: str = Query("BANK_X"), mode: str = Query("pipeline", pattern="^(pipeline|llm)$"),
             conn=Depends(get_conn)):
    """The agent works one ticket now: trace, attribute, evidence, rules, checker, then only the required holds and
    the paperwork. Judgment calls are queued for the officer."""
    check_bank(bank)
    check_case(conn, case_id)
    return _work(conn, case_id, bank, mode)


@app.post("/api/queue/work")
def work_queue(bank: str = Query("BANK_X"), mode: str = Query("pipeline", pattern="^(pipeline|llm)$"),
               limit: int = Query(50, ge=1, le=500), conn=Depends(get_conn)):
    """Work every QUEUED ticket in the order it landed (the night, replayed)."""
    from pipeline.agent import queued

    check_bank(bank)
    started = time.perf_counter()
    results = [_work(conn, case_id, bank, mode) for case_id in queued(conn)[:limit]]
    return {"worked": results, "seconds": round(time.perf_counter() - started, 3)}


@app.get("/api/inbox")
def inbox(bank: str = Query("BANK_X"), conn=Depends(get_conn)):
    """The officer's morning view: what the agent did on its own, and what is waiting for a decision."""
    check_bank(bank)
    names = holder_names(conn)
    done = rows(conn, """
        SELECT t.case_id, t.received_at, c.amount_inr, r.run_id, r.run_status, r.sim_holds_at, r.wall_seconds,
               r.run_mode, r.summary_text,
               (SELECT COUNT(*) FROM PH.HOLD_LEDGER h WHERE h.case_id = t.case_id AND h.viewing_bank_id = {b}
                  AND h.placed_by = 'agent' AND h.hold_kind = 'LIEN') AS holds,
               (SELECT SUM(h.amount_inr) FROM PH.HOLD_LEDGER h WHERE h.case_id = t.case_id
                  AND h.viewing_bank_id = {b} AND h.placed_by = 'agent' AND h.hold_kind = 'LIEN') AS held_inr,
               (SELECT COUNT(*) FROM PH.PORTAL_ENTRY p WHERE p.case_id = t.case_id AND p.viewing_bank_id = {b}
                  AND p.entry_kind = 'EXIT') AS exits,
               (SELECT COUNT(*) FROM PH.PORTAL_ENTRY p WHERE p.case_id = t.case_id AND p.viewing_bank_id = {b}
                  AND p.entry_kind = 'ONWARD') AS onward,
               (SELECT COUNT(*) FROM PH.CASE_ACTION a WHERE a.case_id = t.case_id AND a.viewing_bank_id = {b}
                  AND a.act_mode = 'OFFICER' AND a.action_status IN ('PROPOSED', 'WAITING')) AS waiting
        FROM PH.TICKET t
        JOIN PH.COMPLAINT c ON c.case_id = t.case_id
        JOIN PH.AGENT_RUN r ON r.run_id = t.run_id
        WHERE t.ticket_status IN ('DONE', 'SENT_BACK', 'FAILED')
        ORDER BY t.received_at DESC, t.case_id""", {"b": bank})
    for d in done:
        d["after_hours"] = after_hours(d["received_at"])
    waiting = [present(a, names) for a in rows(conn, """
        SELECT case_id, account_id, bank_id, hop, action_code, act_mode, amount_inr, current_hold_inr,
               victim_amount_inr, other_victims_inr, balance_now_inr, trail_amount_inr, band, score, reason_code,
               legal_basis, action_status, stmt_id
        FROM PH.CASE_ACTION
        WHERE viewing_bank_id = {b} AND act_mode = 'OFFICER' AND action_status IN ('PROPOSED', 'WAITING')
        ORDER BY case_id, hop, account_id, action_code""", {"b": bank})]
    kyc = rows(conn, "SELECT entry_id, case_id, account_id FROM PH.PORTAL_ENTRY WHERE viewing_bank_id = {b} "
                     "AND entry_kind = 'KYC' AND entry_status = 'DRAFT' ORDER BY case_id, account_id", {"b": bank})
    for k in kyc:
        k["name"] = names.get(k["account_id"], k["account_id"])
    queued = conn.execute("SELECT COUNT(*) FROM PH.TICKET WHERE ticket_status = 'QUEUED'").fetchval()
    return {"done": done, "waiting": waiting, "kyc_to_send": kyc, "queued": int(queued or 0)}


# ---------------------------------------------------------------- one case

@app.get("/api/cases/{case_id}")
def case_detail(case_id: str, bank: str = Query("BANK_X"), conn=Depends(get_conn)):
    check_bank(bank)
    c = check_case(conn, case_id)
    c.pop("victim_account", None)          # the victim's details stay off the bank's screens
    c["check"] = one(conn, "SELECT notice_status, problems FROM PH.COMPLAINT_CHECK WHERE case_id = {c}",
                     {"c": case_id})
    c["ticket"] = one(conn, "SELECT received_at, ticket_status, picked_up_at, run_id FROM PH.TICKET "
                            "WHERE case_id = {c}", {"c": case_id})
    run_id = (c["ticket"] or {}).get("run_id")
    c["run"] = one(conn, "SELECT * FROM PH.AGENT_RUN WHERE run_id = {r}", {"r": run_id}) if run_id else None
    c["checks"] = rows(conn, "SELECT check_name, check_passed, detail, stmt_id FROM PH.CHECK_RESULT "
                             "WHERE case_id = {c} AND viewing_bank_id = {b} ORDER BY check_name",
                       {"c": case_id, "b": bank})
    c["paid_inr"] = number(conn.execute("SELECT SUM(t.amount_inr) FROM PH.TXN t JOIN PH.COMPLAINT c "
                                        "ON c.utr = t.utr WHERE c.case_id = {c}", {"c": case_id}).fetchval(), 2)
    c["after_hours"] = after_hours((c["ticket"] or {}).get("received_at"))
    return c


@app.get("/api/cases/{case_id}/trail")
def trail(case_id: str, bank: str = Query("BANK_X"), conn=Depends(get_conn)):
    """The money trail as nodes and edges, with the victim money each edge carried (LIBR) and what was done."""
    check_bank(bank)
    c = check_case(conn, case_id)
    edges = rows(conn, """
        SELECT e.hop, e.txn_id, e.from_account, e.to_account, e.amount_inr, e.ts, e.is_exit, e.is_cycle,
               e.stmt_id, t.utr, t.channel, t.exit_identifier, COALESCE(f.victim_inr, 0) AS victim_inr
        FROM PH.TRACE_EDGE e
        JOIN PH.TXN t ON t.txn_id = e.txn_id
        LEFT JOIN PH.ATTRIBUTION_FLOW f ON f.case_id = e.case_id AND f.txn_id = e.txn_id
                                       AND f.trace_rule = 'LIBR' AND f.victim_tag = {v}
        WHERE e.case_id = {c} ORDER BY e.hop, e.ts, e.txn_id""", {"c": case_id, "v": c["victim_tag"]})
    if not edges:
        raise HTTPException(409, f"The agent has not worked {case_id} yet: POST /api/tickets/{case_id}/work")
    facts = {f["account_id"]: f for f in rows(conn, """
        SELECT ca.account_id, ca.bank_id, ca.hop, ca.is_exit, ca.is_own_bank, ca.victim_amount_inr,
               ca.balance_now_inr, ca.band, acc.holder_name, acc.holder_type
        FROM PH.CASE_ACCOUNT ca LEFT JOIN PH.ACCOUNT acc ON acc.account_id = ca.account_id
        WHERE ca.case_id = {c} AND ca.viewing_bank_id = {b}""", {"c": case_id, "b": bank})}
    acts: dict[str, list[dict]] = {}
    for a in rows(conn, ACTION_SQL, {"c": case_id, "b": bank}):
        acts.setdefault(a["account_id"], []).append(a)
    receivers = {e["to_account"] for e in edges}
    nodes = []
    for acc in sorted({e["from_account"] for e in edges} | receivers):
        f = facts.get(acc, {})
        mine = acts.get(acc, [])
        auto = next((a for a in mine if a["act_mode"] == "AUTOMATIC"), None)
        if acc not in receivers:
            nodes.append({"id": acc, "kind": "victim", "label": "Victim", "hop": 0})
            continue
        kind = "exit" if f.get("is_exit") else ("own" if f.get("is_own_bank") else "other")
        exit_label = next((e["exit_identifier"] for e in edges if e["to_account"] == acc), None)
        nodes.append({
            "id": acc, "kind": kind, "hop": f.get("hop"),
            "label": exit_label if kind == "exit" else (f.get("holder_name") or acc),
            "bank_id": f.get("bank_id"), "bank_name": bank_name(f.get("bank_id")),
            "holder_type": f.get("holder_type"), "victim_amount_inr": f.get("victim_amount_inr"),
            "balance_now_inr": f.get("balance_now_inr"), "evidence": f.get("band"),
            "done": headline(auto) if auto else None,
            "waiting": [headline(a) for a in mine if a["act_mode"] == "OFFICER"
                        and a["action_status"] in ("PROPOSED", "WAITING")],
        })
    return {"case_id": case_id, "bank": bank, "nodes": nodes, "edges": edges}


@app.get("/api/cases/{case_id}/actions")
def case_actions(case_id: str, bank: str = Query("BANK_X"), conn=Depends(get_conn)):
    check_bank(bank)
    check_case(conn, case_id)
    names = holder_names(conn)
    return [present(a, names) for a in rows(conn, ACTION_SQL + " ORDER BY hop, account_id, act_mode, action_code",
                                            {"c": case_id, "b": bank})]


@app.get("/api/cases/{case_id}/accounts/{account_id}")
def account_panel(case_id: str, account_id: str, bank: str = Query("BANK_X"), conn=Depends(get_conn)):
    """The account panel: the action first, then the split of the money, then the evidence behind the reasons."""
    check_bank(bank)
    check_case(conn, case_id)
    split = one(conn, """
        SELECT victim_amount_inr, other_victims_inr, own_funds_inr, balance_now_inr, as_of, stmt_id
        FROM PH.ATTRIBUTION WHERE case_id = {c} AND account_id = {a} AND trace_rule = 'LIBR'""",
                {"c": case_id, "a": account_id})
    if split is None:
        raise HTTPException(404, f"{account_id} is not on the trail of {case_id} (or the agent has not worked it)")
    account = one(conn, "SELECT account_id, bank_id, holder_name, opened_on, account_type, holder_type, "
                        "profile_band FROM PH.ACCOUNT WHERE account_id = {a}", {"a": account_id})
    if account:
        account["bank_name"] = bank_name(account["bank_id"])
    facts = one(conn, "SELECT * FROM PH.CASE_ACCOUNT WHERE case_id = {c} AND viewing_bank_id = {b} "
                      "AND account_id = {a}", {"c": case_id, "b": bank, "a": account_id})
    score = one(conn, "SELECT score, band, stmt_id FROM PH.ACCOUNT_SCORE WHERE case_id = {c} AND account_id = {a}",
                {"c": case_id, "a": account_id})
    signals = rows(conn, """
        SELECT signal_name, signal_value, weight, mule_likeness, contribution, sentence, mule_rank, honest_rank
        FROM PH.ACCOUNT_SIGNALS WHERE case_id = {c} AND account_id = {a} ORDER BY mule_rank""",
                   {"c": case_id, "a": account_id})
    top_key = "mule_rank" if score and score["band"] in ("MULE", "REVIEW") else "honest_rank"
    reasons = [s["sentence"] for s in sorted(signals, key=lambda s: s[top_key])][: SCORING["top_reasons"]]
    names = holder_names(conn)
    acts = [present(a, names) for a in rows(conn, ACTION_SQL + " AND account_id = {a} ORDER BY act_mode DESC",
                                           {"c": case_id, "b": bank, "a": account_id})]
    holds = rows(conn, "SELECT hold_id, hold_kind, amount_inr, placed_at, placed_by, hold_status, replaced_by, "
                       "action_code, reason_text, stmt_id FROM PH.HOLD_LEDGER WHERE case_id = {c} "
                       "AND viewing_bank_id = {b} AND account_id = {a} ORDER BY hold_id",
                 {"c": case_id, "b": bank, "a": account_id})
    decisions = rows(conn, "SELECT decision_id, action_code, decision_outcome, amount_inr, proposed_amount, "
                           "decided_by, decided_at, reason_text, statement_ids FROM PH.DECISION WHERE case_id = {c} "
                           "AND account_id = {a} AND viewing_bank_id = {b} ORDER BY decision_id",
                     {"c": case_id, "a": account_id, "b": bank})
    return {"case_id": case_id, "account_id": account_id, "bank": bank, "account": account, "split": split,
            "facts": facts, "evidence": score, "reasons": reasons, "signals": signals, "actions": acts,
            "holds": holds, "decisions": decisions}


@app.get("/api/cases/{case_id}/log")
def agent_log(case_id: str, conn=Depends(get_conn)):
    """Every run of the agent on this ticket, newest first, with each tool call and its Exasol statements."""
    check_case(conn, case_id)
    runs = rows(conn, "SELECT * FROM PH.AGENT_RUN WHERE case_id = {c} ORDER BY run_id DESC", {"c": case_id})
    for r in runs:
        r["steps"] = rows(conn, "SELECT step_no, tool_name, chosen_by, can_change, result_text, stmt_ids, wall_ms "
                                "FROM PH.AGENT_STEP WHERE run_id = {r} ORDER BY step_no", {"r": r["run_id"]})
    return runs


@app.get("/api/cases/{case_id}/paperwork")
def paperwork(case_id: str, bank: str = Query("BANK_X"), conn=Depends(get_conn)):
    """Onward entries, exits and KYC packets: what the officer used to type by hand."""
    check_bank(bank)
    check_case(conn, case_id)
    out = rows(conn, "SELECT * FROM PH.PORTAL_ENTRY WHERE case_id = {c} AND viewing_bank_id = {b} "
                     "ORDER BY entry_kind DESC, entry_id", {"c": case_id, "b": bank})
    for e in out:
        e["bank_name"] = bank_name(e.get("bank_id"))
    return out


@app.get("/api/cases/{case_id}/compare")
def compare_case(case_id: str, bank: str = Query("BANK_X"), refresh: bool = False, conn=Depends(get_conn)):
    """Today's desk vs the agent on the same ticket and the same transactions (computed in Exasol)."""
    from pipeline.today import compare, run_today

    check_bank(bank)
    check_case(conn, case_id)
    try:
        if refresh:
            run_today(conn, case_id, bank)
        return compare(conn, case_id, bank)
    except steps.StepFailed as exc:
        raise HTTPException(409, str(exc)) from exc


# ---------------------------------------------------------------- officer decisions

class Approve(BaseModel):
    action_code: str = Field(..., description="PROPOSE_NARROW, PROPOSE_FULL_FREEZE or CONFIRM_HOLD")
    officer: str = Field(..., description="Officer deciding; required")
    comment: str | None = Field(None, description="Required for a full freeze and for an edited amount")
    amount: float | None = Field(None, description="Edited narrowing amount")


class Reject(BaseModel):
    action_code: str
    officer: str
    reason: str = Field(..., description="Required")


class Send(BaseModel):
    officer: str


@app.post("/api/cases/{case_id}/accounts/{account_id}/approve")
def approve(case_id: str, account_id: str, body: Approve, bank: str = Query("BANK_X"), conn=Depends(get_conn)):
    check_bank(bank)
    try:
        decision_id = officer.approve(conn, case_id, bank, account_id, body.action_code, body.officer, body.comment,
                                      body.amount)
    except officer.DecisionError as exc:
        raise HTTPException(400, str(exc)) from exc
    return {"decision_id": decision_id}


@app.post("/api/cases/{case_id}/accounts/{account_id}/reject")
def reject(case_id: str, account_id: str, body: Reject, bank: str = Query("BANK_X"), conn=Depends(get_conn)):
    check_bank(bank)
    try:
        decision_id = officer.reject(conn, case_id, bank, account_id, body.action_code, body.officer, body.reason)
    except officer.DecisionError as exc:
        raise HTTPException(400, str(exc)) from exc
    return {"decision_id": decision_id}


@app.post("/api/cases/{case_id}/paperwork/{entry_id}/send")
def send_kyc(case_id: str, entry_id: int, body: Send, bank: str = Query("BANK_X"), conn=Depends(get_conn)):
    check_bank(bank)
    try:
        decision_id = officer.send_kyc(conn, case_id, bank, entry_id, body.officer)
    except officer.DecisionError as exc:
        raise HTTPException(400, str(exc)) from exc
    return {"decision_id": decision_id}


@app.get("/api/holds")
def holds(bank: str = Query("BANK_X"), conn=Depends(get_conn)):
    check_bank(bank)
    out = rows(conn, "SELECT h.*, a.holder_name FROM PH.HOLD_LEDGER h LEFT JOIN PH.ACCOUNT a "
                     "ON a.account_id = h.account_id WHERE h.viewing_bank_id = {b} ORDER BY h.hold_id DESC",
               {"b": bank})
    return out


@app.get("/api/decisions")
def decisions(bank: str = Query("BANK_X"), conn=Depends(get_conn)):
    check_bank(bank)
    return rows(conn, "SELECT * FROM PH.DECISION WHERE viewing_bank_id = {b} ORDER BY decision_id DESC", {"b": bank})


@app.get("/api/audit")
def audit(case_id: str | None = None, limit: int = Query(200, le=1000), conn=Depends(get_conn)):
    """Every pipeline step and officer action with its Exasol session and statement ID (NFR-06)."""
    where = "WHERE case_id = {c}" if case_id else ""
    return rows(conn, f"SELECT * FROM PH.AUDIT_LOG {where} ORDER BY event_id DESC LIMIT {int(limit)}",
                {"c": case_id})


@app.post("/api/demo/reset")
def demo_reset(conn=Depends(get_conn)):
    """Reload the cases and empty every work table: the night starts again (same as `make seed`)."""
    from pipeline.cases import load_case
    from pipeline.load_cases import CASE_FILES, load, rows_for

    started = time.perf_counter()
    load(conn, rows_for({source: load_case(path) for source, path in CASE_FILES.items()}))
    return {"reset": True, "seconds": round(time.perf_counter() - started, 3)}