"""Proportionate Holds API (FastAPI). The officer console talks only to this.

Every number comes from Exasol. The API holds no rules of its own and connects as the right database user for each
request (pipeline/db.py): screens read as the bank's maker (read-only on its schema, no privilege on holds), the
officer's decisions go through the checker (pipeline/checker.py: apply_decision), the demo controls and the
narrator's view run as the admin. Only four things ever wait for the officer; no endpoint can place a full freeze
without an officer's name on a proposal the checker already validated.

Start it:   python -m app.api            (http://127.0.0.1:8000, interactive docs at /docs)
"""
from __future__ import annotations

import json
import time
from collections.abc import Iterator
from decimal import Decimal
from pathlib import Path

import yaml
from fastapi import Depends, FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import PlainTextResponse, RedirectResponse
from pydantic import BaseModel, Field

from pipeline import steps
from pipeline.checker import DecisionError
from pipeline.explain import explain, headline, ticket_label, ticket_labels

ROOT = Path(__file__).resolve().parents[2]
BANKS = yaml.safe_load((ROOT / "config" / "banks.yaml").read_text(encoding="utf-8"))
RULES = yaml.safe_load((ROOT / "config" / "rules.yaml").read_text(encoding="utf-8"))
SCORING = yaml.safe_load((ROOT / "config" / "scoring.yaml").read_text(encoding="utf-8"))
DECISIONS = tuple(RULES["officer"])

app = FastAPI(title="Proportionate Holds API", version="0.3.0",
              description="A bank-side agent that works every cyber-fraud ticket the moment it lands, holds what the "
                          "rules require, and leaves the officer only the judgment calls.")
app.add_middleware(CORSMiddleware, allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
                   allow_methods=["*"], allow_headers=["*"])


@app.get("/", include_in_schema=False)
def root() -> RedirectResponse:
    """The bare address opens the interactive docs. 307 (temporary) so browsers never cache it."""
    return RedirectResponse("/docs", status_code=307)


# ---------------------------------------------------------------- helpers

def check_bank(bank: str) -> str:
    if bank not in BANKS:
        raise HTTPException(400, f"Unknown bank {bank!r}; known: {sorted(BANKS)}")
    return bank


def _open(role: str, bank: str | None = None):
    from pipeline.db import connect

    try:
        return connect(role, bank)
    except SystemExit as exc:          # missing .env settings or users: say so instead of a stack trace
        raise HTTPException(503, str(exc)) from exc


def get_conn(bank: str = Query("BANK_X")) -> Iterator:
    """The bank's maker connection (reads). One session per request, so CURRENT_STATEMENT stays consistent."""
    conn = _open("maker", check_bank(bank))
    try:
        yield conn
    finally:
        conn.close()


def get_checker(bank: str = Query("BANK_X")) -> Iterator:
    conn = _open("checker", check_bank(bank))
    try:
        yield conn
    finally:
        conn.close()


def get_admin() -> Iterator:
    conn = _open("admin")
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


def check_case(conn, case_id: str) -> dict:
    c = one(conn, "SELECT * FROM COMPLAINT WHERE case_id = {c}", {"c": case_id})
    if c is None:
        raise HTTPException(404, f"No case {case_id!r} at this bank")
    return c


def bank_name(bank_id: str | None) -> str | None:
    return BANKS.get(bank_id or "", {}).get("name") if bank_id else None


def after_hours(ts: str | None) -> bool:
    from pipeline.today import after_hours as outside_desk_hours

    return outside_desk_hours(ts)


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
            for r in rows(conn, "SELECT account_id, holder_name FROM ACCOUNT WHERE account_id IN "
                                "(SELECT account_id FROM CASE_ACCOUNT UNION SELECT account_id FROM PROPOSAL "
                                "UNION SELECT account_id FROM WATCH_EVENT)")}


def sim_clock(conn) -> str | None:
    try:
        v = conn.execute("SELECT MAX(clock_at) FROM PH_PORTAL.SIM_CLOCK").fetchval()
    except Exception:  # a bank user cannot read the portal schema
        return None
    return str(v)[:19] if v else None


def clock_now() -> str | None:
    conn = _open("admin")
    try:
        return sim_clock(conn)
    finally:
        conn.close()


ACTION_SQL = """
    SELECT account_id, bank_id, hop, action_code, act_mode, amount_inr, current_hold_inr, victim_amount_inr,
           other_victims_inr, balance_now_inr, trail_amount_inr, band, score, reason_code, legal_basis,
           action_status, stmt_id
    FROM CASE_ACTION WHERE case_id = {c} AND viewing_bank_id = {b}"""


# ---------------------------------------------------------------- reference data and the clock

@app.get("/api/health")
def health(conn=Depends(get_conn)):
    version = conn.execute(
        "SELECT PARAM_VALUE FROM EXA_METADATA WHERE PARAM_NAME = 'databaseProductVersion'").fetchval()
    return {"ok": True, "exasol": version, "session": conn.session_id()}


@app.get("/api/config")
def config():
    from pipeline.llm import settings as llm_settings

    try:
        from pipeline.db import own_bank

        own = own_bank()
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
        "desk_hours": [10, 18],
    }


@app.get("/api/clock")
def clock(admin=Depends(get_admin)):
    """How far the simulated night has run."""
    return {"clock": sim_clock(admin)}


# ---------------------------------------------------------------- the demo: the night, reset, the narrator

class NightRun(BaseModel):
    until: str = Field("2026-10-18 09:00:00", description="Simulated time to run to")
    mode: str = Field("pipeline", pattern="^(pipeline|llm)$")
    reset: bool = Field(True, description="Reload the data and start the night again first")
    banks: list[str] | None = Field(None, description="Only these banks (e.g. BANK_Z to open its desk)")


@app.post("/api/night/run")
def night_run(body: NightRun):
    """Run the simulated night across every bank, in time order (pipeline/night.py)."""
    from pipeline.night import run_night

    for b in body.banks or []:
        check_bank(b)
    started = time.perf_counter()
    try:
        r = run_night(body.until, body.mode, body.reset, body.banks, quiet=True)
    except steps.StepFailed as exc:
        raise HTTPException(500, str(exc)) from exc
    return {"from": r["from"], "until": r["until"], "events": r["events"], "lines": r["lines"],
            "morning": r["morning"], "seconds": round(time.perf_counter() - started, 2)}


@app.post("/api/demo/reset")
def demo_reset(admin=Depends(get_admin)):
    """Reload the data and empty every work table: every ticket waits on the portal again (same as `make seed`)."""
    from pipeline.load_cases import reset

    started = time.perf_counter()
    reset(admin)
    return {"reset": True, "seconds": round(time.perf_counter() - started, 2)}


@app.get("/api/narrator")
def narrator(victim: str = Query("V_LAKSHMI"), admin=Depends(get_admin)):
    """The whole trail across every bank. No single bank sees this: it reads every schema as the admin."""
    from pipeline import chain
    from pipeline.narrator import CAPTION, timeline, victim_trail

    return {"caption": CAPTION, "clock": sim_clock(admin), "trail": victim_trail(admin, victim),
            "timeline": timeline(admin), "chain": chain.verify(chain.tickets(admin, victim))}


# ---------------------------------------------------------------- the ticket queue and the agent

@app.get("/api/queue")
def queue(bank: str = Query("BANK_X"), conn=Depends(get_conn)):
    """This bank's CFCFRMS inbox: every ticket that has reached it, when it landed and what happened to it."""
    out = rows(conn, """
        SELECT t.case_id, t.received_at, t.ticket_status, t.run_id, t.origin, c.amount_inr, c.utr,
               c.complaint_source, c.ticket_kind, c.victim_tag, c.from_bank_id, k.match_status, k.mismatched,
               k.problems, k.related, k.related_case_id, k.near_amount_inr, r.run_status, r.run_mode, r.wall_seconds,
               r.sim_holds_at, r.summary_text,
               (SELECT SUM(h.amount_inr) FROM HOLD_LEDGER h WHERE h.case_id = t.case_id AND h.hold_kind = 'LIEN'
                  AND h.hold_status = 'ACTIVE') AS held_inr,
               (SELECT COUNT(DISTINCT h.account_id) FROM HOLD_LEDGER h WHERE h.case_id = t.case_id
                  AND h.hold_kind = 'LIEN' AND h.hold_status = 'ACTIVE') AS held_accounts,
               (SELECT MIN(h.placed_at) FROM HOLD_LEDGER h WHERE h.case_id = t.case_id AND h.hold_kind = 'LIEN')
                  AS first_hold_at,
               (SELECT MIN(h.placed_at) FROM HOLD_LEDGER h WHERE h.case_id = t.case_id AND h.hold_kind = 'MARKER')
                  AS marker_at,
               (SELECT MIN(w.event_at) FROM WATCH_EVENT w WHERE w.case_id = t.case_id
                  AND w.event_kind = 'LINKED_TO_VICTIM') AS linked_at,
               (SELECT SUM(p.victim_amount_inr) FROM PORTAL_ENTRY p WHERE p.case_id = t.case_id
                  AND p.entry_kind = 'ONWARD') AS onward_inr,
               (SELECT MIN(p.bank_id) FROM PORTAL_ENTRY p WHERE p.case_id = t.case_id
                  AND p.entry_kind = 'ONWARD') AS onward_bank_id,
               (SELECT SUM(p.victim_amount_inr) FROM PORTAL_ENTRY p WHERE p.case_id = t.case_id
                  AND p.entry_kind = 'EXIT') AS exits_inr,
               (SELECT MIN(p.filed_at) FROM PORTAL_ENTRY p WHERE p.case_id = t.case_id
                  AND p.entry_kind = 'SEND_BACK') AS sent_back_at,
               (SELECT COUNT(*) FROM PROPOSAL p WHERE p.case_id = t.case_id
                  AND p.proposal_status = 'WAITING') AS waiting,
               (SELECT MIN(p.action_code) FROM PROPOSAL p WHERE p.case_id = t.case_id
                  AND p.proposal_status = 'WAITING') AS waiting_code
        FROM TICKET t
        JOIN COMPLAINT c ON c.case_id = t.case_id
        LEFT JOIN INTAKE_CHECK k ON k.case_id = t.case_id
        LEFT JOIN AGENT_RUN r ON r.run_id = t.run_id
        ORDER BY t.received_at, t.case_id""")
    labels = ticket_labels(conn)
    for t in out:
        t["after_hours"] = after_hours(t["received_at"])
        t["label"] = labels.get(t["case_id"], t["case_id"])
        t["onward_bank"] = bank_name(t.get("onward_bank_id"))
        t["from_bank"] = bank_name(t.get("from_bank_id"))
    return out


@app.post("/api/tickets/{case_id}/work")
def work_one(case_id: str, bank: str = Query("BANK_X"), mode: str = Query("pipeline", pattern="^(pipeline|llm)$")):
    """Deliver the ticket if it is due and work it now: maker, then checker, then the portal collects the filings."""
    from pipeline import portal
    from pipeline.agent import work_ticket

    check_bank(bank)
    pconn, maker, checker = _open("portal"), _open("maker", bank), _open("checker", bank)
    try:
        portal.deliver_one(pconn, case_id)
        check_case(maker, case_id)
        out = work_ticket(maker, checker, case_id, bank, mode, now=clock_now())
        out["routed"] = portal.collect(pconn, bank)
        return out
    except steps.StepFailed as exc:
        raise HTTPException(500, str(exc)) from exc
    finally:
        for c in (pconn, maker, checker):
            c.close()


# ---------------------------------------------------------------- the officer's morning

def proposal_rows(conn, where: str, params: dict) -> list[dict]:
    names = holder_names(conn)
    labels = ticket_labels(conn)
    out = rows(conn, f"""
        SELECT p.proposal_id, p.case_id, p.account_id, p.action_code, p.act_mode, p.amount_inr, p.proposed_by,
               p.proposed_at, p.reasons_json, p.draft_text, p.draft_by, p.claims_check, p.proposal_status,
               p.checker_note, p.checked_at, p.stmt_id, t.received_at, acc.opened_on, acc.account_type,
               acc.holder_type, ca.balance_now_inr, ca.victim_amount_inr, ca.other_victims_inr, ca.band,
               ca.pass_through, ca.cash_exits, ca.complaint_links,
               (SELECT SUM(h.amount_inr) FROM HOLD_LEDGER h WHERE h.account_id = p.account_id AND h.case_id = p.case_id
                  AND h.hold_status = 'ACTIVE' AND h.hold_kind = 'LIEN') AS current_hold_inr,
               (SELECT MIN(h.placed_at) FROM HOLD_LEDGER h WHERE h.account_id = p.account_id AND h.case_id = p.case_id
                  AND h.hold_status = 'ACTIVE' AND h.hold_kind = 'LIEN') AS held_since,
               acc.bank_id AS account_bank_id, d.decision_id, d.decided_by, d.decided_at, d.decision_outcome,
               d.amount_inr AS decided_amount_inr, d.reason_text AS decided_reason, d.signed_text AS signed_text,
               co.amount_inr AS complaint_amount_inr, co.victim_tag, co.utr AS notice_utr, co.txn_at AS notice_at,
               co.beneficiary_account AS notice_account, k.near_utr, k.near_at, k.near_amount_inr, k.near_txn_id,
               u.action_code AS undo_code, u.decided_by AS undone_by, u.decided_at AS undone_at,
               u.reason_text AS undo_reason, u.amount_inr AS undo_amount_inr
        FROM PROPOSAL p
        LEFT JOIN DECISION d ON d.proposal_id = p.proposal_id AND d.action_code = p.action_code
        LEFT JOIN DECISION u ON u.proposal_id = p.proposal_id AND u.action_code IN ('HOLD_AGAIN', 'UNFREEZE', 'RELEASE')
        LEFT JOIN INTAKE_CHECK k ON k.case_id = p.case_id
        LEFT JOIN COMPLAINT co ON co.case_id = p.case_id
        LEFT JOIN TICKET t ON t.case_id = p.case_id
        LEFT JOIN ACCOUNT acc ON acc.account_id = p.account_id
        LEFT JOIN CASE_ACCOUNT ca ON ca.case_id = p.case_id AND ca.account_id = p.account_id
        WHERE {where} ORDER BY p.proposal_id""", params)
    for p in out:
        try:
            p["reasons"] = json.loads(p.pop("reasons_json") or "{}")
        except ValueError:
            p["reasons"] = {}
        p["name"] = names.get(p["account_id"], p["account_id"])
        p["headline"] = headline({"action_code": p["action_code"], "amount_inr": p["amount_inr"],
                                  "account_id": p["account_id"]})
        p["case_against"] = [a["text"] for a in p["reasons"].get("against", [])]
        p["reasons_for"] = [a["text"] for a in p["reasons"].get("support", [])]
        p["citation"] = p["reasons"].get("citation")
        p["record"] = f"/api/accounts/{p['account_id']}/record"
        p["label"] = labels.get(p["case_id"], p["case_id"])
        p["bank_name"] = bank_name(p.get("account_bank_id"))
    now = clock_now() if any(p["proposal_status"] == "WAITING" for p in out) else None
    if now:
        from pipeline.checker import Checker

        for p in out:
            if p["proposal_status"] == "WAITING" and p.get("account_bank_id") in BANKS:
                # the balance now: when it differs from the balance the proposal was made on, the card says so
                p["balance_today_inr"] = float(Checker(conn, p["account_bank_id"]).balance_at(p["account_id"], now))
    return out


STEP_WORDS = {"030": "checking the complaint against the bank's payments", "040": "tracing the money",
              "050": "working out the victim's share", "055": "choosing the accounts to assess",
              "060": "reading the accounts' history", "070": "applying the rules", "075": "running the checks",
              "081": "placing the holds", "082": "reporting the cash withdrawals", "083": "informing the other banks",
              "084": "filing the KYC packets"}


def plain_error(text: str | None) -> str:
    """The reason a complaint could not be completed, in plain words (the full error stays in the step log)."""
    import re

    if not text:
        return "The checks did not pass, so nothing was held automatically."
    m = re.search(r"FAILED (\d{3})_\w+\.sql", text)
    if m:
        return f"The system stopped while {STEP_WORDS.get(m.group(1), 'working on it')}. Nothing more was done."
    if "No ticket" in text:
        return "The complaint did not reach this bank's inbox."
    return "The system stopped before it finished. The step-by-step log on the complaint has the details."


@app.get("/api/inbox")
def inbox(bank: str = Query("BANK_X"), conn=Depends(get_conn)):
    """The officer's morning: what was done automatically (read only), and the decisions waiting (the four only)."""
    from pipeline.morning import latest

    waiting = proposal_rows(conn, "p.proposal_status = 'WAITING'", {})
    done = rows(conn, """
        SELECT t.case_id, t.received_at, t.ticket_status, c.amount_inr, c.ticket_kind, c.victim_tag, c.utr,
               c.ordering_authority, r.run_id, r.run_status,
               r.run_mode, r.sim_holds_at, r.wall_seconds, r.summary_text,
               (SELECT COUNT(*) FROM HOLD_LEDGER h WHERE h.case_id = t.case_id AND h.placed_by = 'agent'
                  AND h.hold_kind = 'LIEN') AS holds,
               (SELECT SUM(h.amount_inr) FROM HOLD_LEDGER h WHERE h.case_id = t.case_id AND h.placed_by = 'agent'
                  AND h.hold_kind = 'LIEN' AND h.hold_status <> 'REPLACED') AS held_inr,
               (SELECT COUNT(*) FROM PORTAL_ENTRY p WHERE p.case_id = t.case_id) AS filed,
               (SELECT COUNT(*) FROM PORTAL_ENTRY p WHERE p.case_id = t.case_id AND p.entry_kind = 'EXIT') AS exits,
               (SELECT COUNT(*) FROM PORTAL_ENTRY p WHERE p.case_id = t.case_id AND p.entry_kind = 'ONWARD') AS onward,
               (SELECT COUNT(*) FROM PORTAL_ENTRY p WHERE p.case_id = t.case_id AND p.entry_kind = 'KYC') AS kyc,
               (SELECT MAX(p.filed_at) FROM PORTAL_ENTRY p WHERE p.case_id = t.case_id AND p.entry_kind = 'KYC')
                  AS kyc_filed_at,
               (SELECT SUM(p.victim_amount_inr) FROM PORTAL_ENTRY p WHERE p.case_id = t.case_id
                  AND p.entry_kind = 'ONWARD') AS onward_inr,
               (SELECT SUM(p.victim_amount_inr) FROM PORTAL_ENTRY p WHERE p.case_id = t.case_id
                  AND p.entry_kind = 'EXIT') AS exits_inr,
               (SELECT MIN(p.bank_id) FROM PORTAL_ENTRY p WHERE p.case_id = t.case_id
                  AND p.entry_kind = 'ONWARD') AS onward_bank_id,
               (SELECT MIN(p.moved_at) FROM PORTAL_ENTRY p WHERE p.case_id = t.case_id
                  AND p.entry_kind = 'EXIT') AS exit_at,
               (SELECT COUNT(DISTINCT h.account_id) FROM HOLD_LEDGER h WHERE h.case_id = t.case_id
                  AND h.placed_by = 'agent' AND h.hold_kind = 'LIEN' AND h.hold_status <> 'REPLACED') AS held_accounts,
               (SELECT MIN(w.event_at) FROM WATCH_EVENT w WHERE w.case_id = t.case_id
                  AND w.event_kind = 'LINKED_TO_VICTIM') AS linked_at,
               (SELECT MIN(h.placed_at) FROM HOLD_LEDGER h WHERE h.case_id = t.case_id AND h.hold_kind = 'MARKER')
                  AS marker_at,
               (SELECT COUNT(*) FROM PROPOSAL p WHERE p.case_id = t.case_id
                  AND p.proposal_status = 'WAITING') AS waiting,
               (SELECT MAX(d.decided_at) FROM DECISION d WHERE d.case_id = t.case_id) AS decided_at,
               (SELECT MAX(d.decided_by) FROM DECISION d WHERE d.case_id = t.case_id) AS decided_by,
               (SELECT MAX(s.result_text) FROM AGENT_STEP s WHERE s.run_id = r.run_id
                  AND s.tool_name = 'error') AS error_text
        FROM TICKET t
        JOIN COMPLAINT c ON c.case_id = t.case_id
        JOIN AGENT_RUN r ON r.run_id = t.run_id
        WHERE t.ticket_status NOT IN ('QUEUED', 'WORKING')
        ORDER BY t.received_at, t.case_id""")
    labels = ticket_labels(conn)
    for d in done:
        d["after_hours"] = after_hours(d["received_at"])
        d["onward_bank"] = bank_name(d.get("onward_bank_id"))
        d["label"] = labels.get(d["case_id"], d["case_id"])
        d["problem"] = plain_error(d.pop("error_text", None)) if d["run_status"] in ("FAILED", "CHECK_FAILED") else None
    watch = watch_rows(conn, bank)
    queued = conn.execute("SELECT COUNT(*) FROM TICKET WHERE ticket_status = 'QUEUED'").fetchval()
    decided = proposal_rows(conn, "p.proposal_status IN ('APPROVED', 'EDITED', 'REJECTED')", {})
    return {"waiting": waiting, "decided": decided, "done": done, "watch": watch, "morning_log": latest(conn, bank),
            "queued": int(queued or 0), "kyc_filed": sum(int(d.get("kyc") or 0) for d in done)}


def watch_rows(conn, bank: str, account_id: str | None = None) -> list[dict]:
    sql = ("SELECT w.event_id, w.account_id, COALESCE(a.holder_name, w.account_id) AS name, w.case_id, w.event_at, "
           "w.event_kind, w.amount_inr, w.counterparty, w.channel, w.txn_ref, w.attempt_id, w.hold_id, w.proposal_id, "
           "w.sentence, w.stmt_id FROM WATCH_EVENT w LEFT JOIN ACCOUNT a ON a.account_id = w.account_id "
           "WHERE w.viewing_bank_id = {b}" + (" AND w.account_id = {a}" if account_id else "")
           + " ORDER BY w.event_at, w.event_id")
    out = rows(conn, sql, {"b": bank, "a": account_id})
    for r in out:
        r["out_of_hours"] = after_hours(str(r["event_at"]))
    return out


@app.get("/api/watch")
def watch(bank: str = Query("BANK_X"), conn=Depends(get_conn)):
    """What happened on watched accounts after the first pass, and what the agent did."""
    return {"events": watch_rows(conn, bank),
            "watch_list": rows(conn, "SELECT w.account_id, COALESCE(a.holder_name, w.account_id) AS name, w.case_id, "
                                     "w.first_pass, w.reported_inr, w.added_at, w.added_why, w.left_at, w.left_why "
                                     "FROM WATCH_LIST w LEFT JOIN ACCOUNT a ON a.account_id = w.account_id "
                                     "ORDER BY w.added_at")}


class Decide(BaseModel):
    officer: str = Field(..., description="Officer deciding; required")
    comment: str | None = Field(None, description="Required to reject, or to change a narrowing amount")
    amount: float | None = Field(None, description="Changed narrowing amount (optional)")
    signed_text: str | None = Field(None, description="The reasons as the officer signs them (optional edit of the "
                                                      "draft)")


@app.post("/api/proposals/{proposal_id}/approve")
def approve(proposal_id: int, body: Decide, bank: str = Query("BANK_X"), checker=Depends(get_checker)):
    """The officer approves one of the four decisions; the checker carries it out."""
    from pipeline import officer

    try:
        decision_id = officer.approve(checker, bank, proposal_id, body.officer, body.comment, body.amount,
                                      body.signed_text, now=clock_now())
    except DecisionError as exc:
        raise HTTPException(400, str(exc)) from exc
    rerun = None
    code = checker.execute("SELECT action_code, case_id FROM PROPOSAL WHERE proposal_id = {p}",
                           {"p": proposal_id}).fetchone()
    if code and code[0] == "UNMATCHED_NOTICE":
        rerun = work_one(code[1], bank, "pipeline")   # the officer accepted the near match: work the ticket now
    return {"decision_id": decision_id, "rerun": rerun}


@app.post("/api/proposals/{proposal_id}/reject")
def reject(proposal_id: int, body: Decide, bank: str = Query("BANK_X"), checker=Depends(get_checker)):
    from pipeline import officer

    try:
        return {"decision_id": officer.reject(checker, bank, proposal_id, body.officer, body.comment,
                                              now=clock_now())}
    except DecisionError as exc:
        raise HTTPException(400, str(exc)) from exc


class Undo(BaseModel):
    officer: str = Field(..., description="Officer undoing; required")
    reason: str = Field(..., description="Why; required, it goes into the audit log and the hold record")


@app.post("/api/decisions/{decision_id}/undo")
def undo(decision_id: int, body: Undo, bank: str = Query("BANK_X"), checker=Depends(get_checker)):
    """Hold again after a smaller hold, unfreeze after a freeze, release after an accepted near match."""
    from pipeline import officer

    try:
        return {"decision_id": officer.undo(checker, bank, decision_id, body.officer, body.reason, now=clock_now())}
    except DecisionError as exc:
        raise HTTPException(400, str(exc)) from exc


@app.get("/api/accounts/{account_id}/record")
def record(account_id: str, bank: str = Query("BANK_X"), conn=Depends(get_conn)):
    """The hold record: the notice, the path, the LIBR steps, the rule, who decided, every change, statement IDs."""
    from pipeline.record import hold_record, to_json

    try:
        r = json.loads(to_json(hold_record(conn, bank, account_id)))
    except LookupError as exc:
        raise HTTPException(404, str(exc)) from exc
    now = clock_now()
    if now:
        from pipeline.checker import Checker

        r["balance_now_inr"] = float(Checker(conn, bank).balance_at(account_id, now))
    r["proposals_now"] = proposal_rows(conn, "p.account_id = {a} AND p.proposal_status IN ('WAITING', 'APPROVED', "
                                             "'EDITED', 'REJECTED')", {"a": account_id})
    return r


@app.get("/api/accounts/{account_id}/record.txt", response_class=PlainTextResponse)
def record_text(account_id: str, bank: str = Query("BANK_X"), conn=Depends(get_conn)):
    """The hold record as plain text, to attach to a grievance file."""
    from pipeline.record import as_text, hold_record

    try:
        return as_text(hold_record(conn, bank, account_id))
    except LookupError as exc:
        raise HTTPException(404, str(exc)) from exc


# ---------------------------------------------------------------- one case

@app.get("/api/cases/{case_id}")
def case_detail(case_id: str, bank: str = Query("BANK_X"), conn=Depends(get_conn)):
    c = check_case(conn, case_id)
    c.pop("victim_account", None)          # the victim's details stay off the bank's screens
    c["intake"] = one(conn, "SELECT * FROM INTAKE_CHECK WHERE case_id = {c}", {"c": case_id})
    c["ticket"] = one(conn, "SELECT received_at, ticket_status, picked_up_at, run_id, origin FROM TICKET "
                            "WHERE case_id = {c}", {"c": case_id})
    run_id = (c["ticket"] or {}).get("run_id")
    c["run"] = one(conn, "SELECT * FROM AGENT_RUN WHERE run_id = {r}", {"r": run_id}) if run_id else None
    c["checks"] = rows(conn, "SELECT check_name, check_passed, detail, stmt_id FROM CHECK_RESULT "
                             "WHERE case_id = {c} AND viewing_bank_id = {b} ORDER BY check_name",
                       {"c": case_id, "b": bank})
    c["links"] = rows(conn, "SELECT case_id, linked_case_id, link_kind, account_id, detail, linked_at FROM CASE_LINK "
                            "WHERE case_id = {c} OR linked_case_id = {c} ORDER BY linked_at", {"c": case_id})
    matched = (c["intake"] or {}).get("matched_txn_id")
    paid = one(conn, "SELECT amount_inr, channel FROM TXN WHERE txn_id = {t}", {"t": matched}) if matched else None
    c["paid_inr"] = (paid or {}).get("amount_inr")
    c["paid_channel"] = (paid or {}).get("channel")
    c["after_hours"] = after_hours((c["ticket"] or {}).get("received_at"))
    c["proposals"] = proposal_rows(conn, "p.case_id = {c}", {"c": case_id})
    c["label"] = ticket_label(c)
    c["problem"] = None
    if c["run"] and c["run"].get("run_status") in ("FAILED", "CHECK_FAILED"):
        err = conn.execute("SELECT MAX(result_text) FROM AGENT_STEP WHERE run_id = {r} AND tool_name = 'error'",
                           {"r": c["run"]["run_id"]}).fetchval()
        c["problem"] = plain_error(err)
    c["filed"] = dict(conn.execute("SELECT entry_kind, COUNT(*) FROM PORTAL_ENTRY WHERE case_id = {c} "
                                   "GROUP BY entry_kind", {"c": case_id}).fetchall())
    return c


@app.get("/api/cases/{case_id}/trail")
def trail(case_id: str, bank: str = Query("BANK_X"), conn=Depends(get_conn)):
    """The money trail at this bank as nodes and edges, with the victim money each edge carried (LIBR)."""
    c = check_case(conn, case_id)
    edges = rows(conn, """
        SELECT e.hop, e.txn_id, e.from_account, e.to_account, e.amount_inr, e.ts, e.is_exit, e.is_cycle,
               e.stmt_id, t.utr, t.channel, t.exit_identifier, COALESCE(f.victim_inr, 0) AS victim_inr
        FROM TRACE_EDGE e
        JOIN TXN t ON t.txn_id = e.txn_id
        LEFT JOIN ATTRIBUTION_FLOW f ON f.case_id = e.case_id AND f.txn_id = e.txn_id
                                    AND f.trace_rule = 'LIBR' AND f.victim_tag = {v}
        WHERE e.case_id = {c} ORDER BY e.hop, e.ts, e.txn_id""", {"c": case_id, "v": c["victim_tag"]})
    if not edges:
        raise HTTPException(409, f"Nothing traced for {case_id}: the agent has not worked it, or it matched no "
                                 f"transaction")
    facts = {f["account_id"]: f for f in rows(conn, """
        SELECT ca.account_id, ca.bank_id, ca.hop, ca.is_exit, ca.is_own_bank, ca.victim_amount_inr,
               ca.balance_now_inr, ca.band, acc.holder_name, acc.holder_type
        FROM CASE_ACCOUNT ca LEFT JOIN ACCOUNT acc ON acc.account_id = ca.account_id
        WHERE ca.case_id = {c} AND ca.viewing_bank_id = {b}""", {"c": case_id, "b": bank})}
    acts: dict[str, list[dict]] = {}
    for a in rows(conn, ACTION_SQL, {"c": case_id, "b": bank}):
        acts.setdefault(a["account_id"], []).append(a)
    waiting = {(p["account_id"], p["action_code"]) for p in rows(
        conn, "SELECT account_id, action_code FROM PROPOSAL WHERE case_id = {c} AND proposal_status = 'WAITING'",
        {"c": case_id})}
    receivers = {e["to_account"] for e in edges}
    nodes = []
    for acc in sorted({e["from_account"] for e in edges} | receivers):
        f = facts.get(acc, {})
        mine = acts.get(acc, [])
        auto = next((a for a in mine if a["act_mode"] == "AUTOMATIC"), None)
        if acc not in receivers:
            payer = "Victim" if c["ticket_kind"] != "ONWARD" else f"Paid from {bank_name(c.get('from_bank_id'))}"
            nodes.append({"id": acc, "kind": "victim", "label": payer, "hop": 0})
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
            "waiting": [headline(a) for a in mine if a["act_mode"] == "OFFICER" and (acc, a["action_code"]) in waiting],
        })
    return {"case_id": case_id, "bank": bank, "nodes": nodes, "edges": edges}


@app.get("/api/cases/{case_id}/actions")
def case_actions(case_id: str, bank: str = Query("BANK_X"), conn=Depends(get_conn)):
    check_case(conn, case_id)
    names = holder_names(conn)
    return [present(a, names) for a in rows(conn, ACTION_SQL + " ORDER BY hop, account_id, act_mode, action_code",
                                            {"c": case_id, "b": bank})]


@app.get("/api/cases/{case_id}/accounts/{account_id}")
def account_panel(case_id: str, account_id: str, bank: str = Query("BANK_X"), conn=Depends(get_conn)):
    """The account panel: the action first, then the split of the money, then the evidence behind the reasons."""
    from pipeline.reasons import cross_ticket, declined_lines

    check_case(conn, case_id)
    split = one(conn, """
        SELECT victim_amount_inr, other_victims_inr, own_funds_inr, balance_now_inr, as_of, stmt_id
        FROM ATTRIBUTION WHERE case_id = {c} AND account_id = {a} AND trace_rule = 'LIBR'""",
                {"c": case_id, "a": account_id})
    if split is None:
        raise HTTPException(404, f"{account_id} is not on the trail of {case_id} (or the agent has not worked it)")
    account = one(conn, "SELECT account_id, bank_id, holder_name, opened_on, account_type, holder_type, "
                        "profile_band FROM ACCOUNT WHERE account_id = {a}", {"a": account_id})
    if account:
        account["bank_name"] = bank_name(account["bank_id"])
    facts = one(conn, "SELECT * FROM CASE_ACCOUNT WHERE case_id = {c} AND viewing_bank_id = {b} "
                      "AND account_id = {a}", {"c": case_id, "b": bank, "a": account_id})
    score = one(conn, "SELECT score, band, stmt_id FROM ACCOUNT_SCORE WHERE case_id = {c} AND account_id = {a}",
                {"c": case_id, "a": account_id})
    signals = rows(conn, """
        SELECT signal_name, signal_value, weight, mule_likeness, contribution, sentence, mule_rank, honest_rank
        FROM ACCOUNT_SIGNALS WHERE case_id = {c} AND account_id = {a} ORDER BY mule_rank""",
                   {"c": case_id, "a": account_id})
    top_key = "mule_rank" if score and score["band"] in ("MULE", "REVIEW") else "honest_rank"
    reasons = [s["sentence"] for s in sorted(signals, key=lambda s: s[top_key])][: SCORING["top_reasons"]]
    names = holder_names(conn)
    acts = [present(a, names) for a in rows(conn, ACTION_SQL + " AND account_id = {a} ORDER BY act_mode DESC",
                                           {"c": case_id, "b": bank, "a": account_id})]
    holds = rows(conn, "SELECT hold_id, case_id, hold_kind, amount_inr, hold_tag, placed_at, placed_by, hold_status, "
                       "replaced_by, action_code, reason_text, stmt_id FROM HOLD_LEDGER WHERE account_id = {a} "
                       "ORDER BY hold_id", {"a": account_id})
    decisions = rows(conn, "SELECT decision_id, action_code, decision_outcome, amount_inr, proposed_amount, "
                           "decided_by, decided_at, reason_text, draft_text, signed_text, statement_ids FROM DECISION "
                           "WHERE account_id = {a} ORDER BY decision_id", {"a": account_id})
    flows = rows(conn, """
        SELECT e.from_account, e.to_account, e.amount_inr, e.ts, e.is_exit, t.exit_identifier,
               COALESCE(f.victim_inr, 0) AS victim_inr
        FROM TRACE_EDGE e JOIN TXN t ON t.txn_id = e.txn_id
        JOIN COMPLAINT c ON c.case_id = e.case_id
        LEFT JOIN ATTRIBUTION_FLOW f ON f.case_id = e.case_id AND f.txn_id = e.txn_id AND f.trace_rule = 'LIBR'
                                    AND f.victim_tag = c.victim_tag
        WHERE e.case_id = {c} AND (e.to_account = {a} OR e.from_account = {a}) ORDER BY e.ts""",
                 {"c": case_id, "a": account_id})
    victims = {v for (v,) in conn.execute("SELECT victim_account FROM COMPLAINT WHERE victim_account IS NOT NULL"
                                          ).fetchall()}
    for f in flows:
        # the victim's details stay off the bank's screens (SOP 9.1(ii))
        f["from_name"] = "the victim" if f["from_account"] in victims else names.get(f["from_account"],
                                                                                    f["from_account"])
        f["to_name"] = f["exit_identifier"] if f["is_exit"] else names.get(f["to_account"], f["to_account"])
    splits = rows(conn, "SELECT version_no, victim_tag, case_id, victim_inr, all_victims_inr, held_inr, "
                        "share_of_held_inr, valid_from, superseded_at, trigger_kind, reason_text, stmt_id FROM "
                        "VICTIM_SPLIT WHERE account_id = {a} ORDER BY version_no, victim_tag", {"a": account_id})
    return {"case_id": case_id, "account_id": account_id, "bank": bank, "account": account, "split": split,
            "facts": facts, "evidence": score, "reasons": reasons, "signals": signals, "actions": acts,
            "holds": holds, "decisions": decisions, "splits": splits,
            "cross_ticket": cross_ticket(conn, account_id, case_id), "declined": declined_lines(conn, account_id),
            "inflows": [f for f in flows if f["to_account"] == account_id],
            "outflows": [f for f in flows if f["from_account"] == account_id],
            "proposals": proposal_rows(conn, "p.case_id = {c} AND p.account_id = {a}",
                                       {"c": case_id, "a": account_id}),
            "watch": watch_rows(conn, bank, account_id)}


@app.get("/api/cases/{case_id}/log")
def agent_log(case_id: str, conn=Depends(get_conn)):
    """Every run on this ticket, newest first, with each tool call, the checker's step and the Exasol statements."""
    check_case(conn, case_id)
    runs = rows(conn, "SELECT * FROM AGENT_RUN WHERE case_id = {c} ORDER BY run_id DESC", {"c": case_id})
    for r in runs:
        r["steps"] = rows(conn, "SELECT step_no, tool_name, chosen_by, can_change, result_text, stmt_ids, wall_ms "
                                "FROM AGENT_STEP WHERE run_id = {r} ORDER BY step_no", {"r": r["run_id"]})
    return runs


@app.get("/api/cases/{case_id}/paperwork")
def paperwork(case_id: str, conn=Depends(get_conn)):
    """Onward entries, exits, KYC packets and send-backs: filed at once, what the officer used to type by hand."""
    check_case(conn, case_id)
    out = rows(conn, "SELECT * FROM PORTAL_ENTRY WHERE case_id = {c} ORDER BY entry_kind DESC, entry_id",
               {"c": case_id})
    names = holder_names(conn)
    for e in out:
        e["bank_name"] = bank_name(e.get("bank_id"))
        e["from_account_name"] = names.get(e.get("from_account"), e.get("from_account"))
        e["account_name"] = names.get(e.get("account_id"), e.get("account_id"))
    return out


@app.get("/api/cases/{case_id}/compare")
def compare_case(case_id: str, bank: str = Query("BANK_X"), refresh: bool = False, conn=Depends(get_conn)):
    """Today's desk vs the agent on the same ticket and the same transactions (computed in Exasol)."""
    from pipeline.today import compare, run_today

    check_case(conn, case_id)
    try:
        if refresh:
            run_today(conn, case_id, bank)
        return compare(conn, case_id, bank)
    except steps.StepFailed as exc:
        raise HTTPException(409, str(exc)) from exc


@app.get("/api/day")
def day(bank: str = Query("BANK_X"), conn=Depends(get_conn)):
    """Today's desk vs the agent, summed over every ticket the agent has worked (the Review band)."""
    from pipeline.today import day_summary

    try:
        return day_summary(conn, bank)
    except steps.StepFailed as exc:
        raise HTTPException(409, str(exc)) from exc


@app.get("/api/holds")
def holds(conn=Depends(get_conn)):
    labels = ticket_labels(conn)
    out = rows(conn, "SELECT h.*, a.holder_name FROM HOLD_LEDGER h LEFT JOIN ACCOUNT a "
                     "ON a.account_id = h.account_id ORDER BY h.account_id, h.placed_at DESC, h.hold_id DESC")
    for h in out:
        h["label"] = labels.get(h["case_id"], h["case_id"])
    return out


@app.get("/api/decisions")
def decisions(conn=Depends(get_conn)):
    return rows(conn, "SELECT * FROM DECISION ORDER BY decision_id DESC")


def _num(pattern: str, text: str) -> int | None:
    import re

    m = re.search(pattern, text or "")
    return int(m.group(1)) if m else None


def plain_step(tool: str, text: str | None, case: str, paid: dict, checks: dict, held: dict, names: dict) -> str:
    """One step of a run as the audit log says it: a plain sentence with names, no account or transaction IDs."""
    import re

    from pipeline.explain import inr

    t = text or ""
    if tool == "read_ticket":
        return "Read the police notice. Every field present." if not re.search(r"missing|odd|problem", t, re.I) \
            else f"Read the police notice: {t}"
    if tool == "check_notice":
        if "exact" in t.lower() and case in paid:
            amt, ts, ch = paid[case]
            return f"Matched the payment: {inr(amt)} at {str(ts)[11:16]}{f' by {ch}' if ch else ''}."
        if "near" in t.lower():
            return "The notice does not match a payment here. A payment that fits the rest of it did arrive."
        if "accepted" in t.lower() and case in paid:
            return f"Worked the near match the officer accepted: {inr(paid[case][0])}."
    if tool == "find_related_cases":
        if "duplicate" in t.lower():
            return "Same payment as an earlier complaint: linked to it."
        if "victim" in t.lower() and "new" in t.lower() and "transaction" not in t.lower():
            return "A new victim on an account another complaint already reached."
        return "Looked for earlier complaints: none for this payment."
    if tool == "trace_money":
        hops, other, exits = _num(r"(\d+) hops? into", t), _num(r"(\d+) to other bank", t), _num(r"(\d+) exits?", t)
        parts = [f"{hops} account{'s' if hops != 1 else ''} here" if hops else None,
                 f"{other} at other banks" if other else None,
                 f"{exits} cash or crypto withdrawal{'s' if exits != 1 else ''}" if exits else None]
        parts = [x for x in parts if x]
        return f"Traced the money: {', '.join(parts)}." if parts else "Traced the money."
    if tool == "attribute_victim_share":
        return "Worked out the victim's share in each account, own money first."
    if tool == "get_account_evidence":
        n = len([x for x in t.split(";") if x.strip()]) if ":" in t else None
        return (f"Read 90 days of history for {n} account{'s' if n != 1 else ''}." if n
                else "Read the accounts' history.")
    if tool == "allowed_actions":
        auto, officer = _num(r"(\d+) automatic", t), _num(r"(\d+) decisions?", t)
        if auto is not None:
            return (f"Checked the rules: {auto} automatic action{'s' if auto != 1 else ''}, "
                    f"{officer or 0} for the officer.")
    if tool == "word_reasons":
        return "Wrote the reasons the officer will read."
    if tool == "propose_action":
        auto, officer = _num(r"(\d+) automatic", t), _num(r"(\d+) for the officer", t)
        if auto is not None:
            return f"Proposed {auto} automatic action{'s' if auto != 1 else ''} and {officer or 0} for the officer."
    if tool == "checker":
        ok, total = checks.get(case, (None, None))
        head = f"Checker passed {ok} of {total}." if total else "Checker ran."
        return head + (f" Held {', '.join(held[case])}." if held.get(case) and (ok == total) else "")
    if tool == "write_case_summary":
        return "Wrote the summary for the handover."
    if tool == "error":
        return "Stopped: " + plain_error(t)
    for acc, name in sorted(names.items(), key=lambda kv: -len(kv[0])):   # otherwise: the tool's line, in names
        t = t.replace(acc, name)
    t = re.sub(r"EXIT:(ATM|CRYPTO):", lambda m: "ATM " if m.group(1) == "ATM" else "crypto wallet ", t)
    return t[:1].upper() + t[1:]


DECIDED_WORDS = {"PROPOSE_NARROW": "Reduced the hold on {name} to {amt}", "PROPOSE_FULL_FREEZE": "Froze {name}, {amt}",
                 "UNMATCHED_NOTICE": "Accepted the near match on {name}",
                 "PROPOSE_RELEASE": "Released the hold on {name}",
                 "HOLD_AGAIN": "Held {name} again, {amt}", "UNFREEZE": "Unfroze {name}",
                 "RELEASE": "Released the near-match hold on {name}"}


@app.get("/api/story")
def story(bank: str = Query("BANK_X"), conn=Depends(get_conn)):
    """The audit log in plain words: every system step, every event after a hold, every officer decision and the
    handover note, in time order, each with its reference (step, watch event, decision or note number)."""
    from pipeline.explain import inr
    from pipeline.morning import victim_name

    labels, names = ticket_labels(conn), holder_names(conn)
    victims = dict(conn.execute("SELECT case_id, victim_tag FROM COMPLAINT").fetchall())
    out = []

    def add(t, kind, who, what, case, ref):
        out.append({"t": str(t)[:19] if t else None, "kind": kind, "who": who, "what": what, "case_id": case,
                    "label": labels.get(case or "", case), "victim": victim_name(victims.get(case)) if case else None,
                    "ref": ref})

    paid = {c: (amt, ts, ch) for c, amt, ts, ch in conn.execute(
        "SELECT k.case_id, t.amount_inr, t.ts, t.channel FROM INTAKE_CHECK k JOIN TXN t ON t.txn_id = k.matched_txn_id"
    ).fetchall()}
    checks = {c: (int(ok or 0), int(total or 0)) for c, ok, total in conn.execute(
        "SELECT case_id, SUM(CASE WHEN check_passed THEN 1 ELSE 0 END), COUNT(*) FROM CHECK_RESULT "
        "WHERE viewing_bank_id = {b} GROUP BY case_id", {"b": bank}).fetchall()}
    held = {}
    for c, acc, amt in conn.execute("SELECT case_id, account_id, SUM(amount_inr) FROM HOLD_LEDGER WHERE placed_by = "
                                    "'agent' AND hold_kind = 'LIEN' GROUP BY case_id, account_id "
                                    "ORDER BY case_id, MIN(hold_id)").fetchall():
        held.setdefault(c, []).append(f"{inr(amt)} in {names.get(acc, acc)}")

    for run, case, started, holds_at, n, tool, text, ids in conn.execute(
            "SELECT r.run_id, r.case_id, r.sim_started_at, r.sim_holds_at, s.step_no, s.tool_name, s.result_text, "
            "s.stmt_ids FROM AGENT_STEP s JOIN AGENT_RUN r ON r.run_id = s.run_id "
            "ORDER BY r.run_id, s.step_no").fetchall():
        when = holds_at if tool in ("checker", "write_case_summary") and holds_at else started
        add(when, "system", "The system", plain_step(tool, text, case, paid, checks, held, names), case,
            f"step {int(run)}.{int(n)}" + (f" · stmt {ids}" if ids else ""))
    for w in watch_rows(conn, bank):
        text = w["sentence"] or ""
        text = text[7:] if text[:2].isdigit() and text[2:3] == ":" else text
        add(w["event_at"], "watch", "After the hold", text[:1].upper() + text[1:], w.get("case_id"),
            f"watch {int(w['event_id'])}")
    for d in rows(conn, "SELECT decision_id, case_id, account_id, action_code, decision_outcome, amount_inr, "
                        "decided_by, decided_at, reason_text FROM DECISION ORDER BY decision_id"):
        name = names.get(d["account_id"], d["account_id"])
        if d["decision_outcome"] == "REJECTED":
            what = f"Kept the hold on {name} as it was"
        else:
            what = DECIDED_WORDS.get(d["action_code"], d["action_code"]).format(
                name=name, amt=inr(d["amount_inr"]) if d["amount_inr"] else "")
        add(d["decided_at"], "officer", d["decided_by"], what + (f'. Reasons signed: {d["reason_text"]}'
                                                                if d["reason_text"] else "."), d["case_id"],
            f"decision D-{int(d['decision_id']):02d}")
    for log_id, to in conn.execute("SELECT log_id, period_to FROM MORNING_LOG WHERE viewing_bank_id = {b}",
                                   {"b": bank}).fetchall():
        add(to, "note", "The system", "Filed the handover note for the morning desk.", None, f"note {int(log_id)}")
    out.sort(key=lambda e: (e["t"] or "", e["ref"]))
    return out


@app.get("/api/audit")
def audit(case_id: str | None = None, limit: int = Query(200, le=1000), conn=Depends(get_conn)):
    """Every step and every action, with its Exasol session and statement ID. Append-only."""
    where = "WHERE case_id = {c}" if case_id else ""
    labels = ticket_labels(conn)
    out = rows(conn, f"SELECT * FROM AUDIT_LOG {where} ORDER BY event_id DESC LIMIT {int(limit)}", {"c": case_id})
    for e in out:
        e["label"] = labels.get(e.get("case_id") or "", e.get("case_id"))
    return out