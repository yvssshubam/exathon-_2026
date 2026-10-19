"""Proportionate Holds API (FastAPI). The officer console talks only to this.

All analysis runs in Exasol (pipeline/steps.py runs the SQL in sql/steps/); officer actions go through
pipeline/officer.py, which enforces the approval rules. This layer only routes requests and shapes JSON.
No endpoint places a hold without an officer's approval (FR-10), and there is no auto-approve route.

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
from pipeline.explain import explain, headline, inr

ROOT = Path(__file__).resolve().parents[2]
BANKS = yaml.safe_load((ROOT / "config" / "banks.yaml").read_text(encoding="utf-8"))
LANES = yaml.safe_load((ROOT / "config" / "lanes.yaml").read_text(encoding="utf-8"))
SCORING = yaml.safe_load((ROOT / "config" / "scoring.yaml").read_text(encoding="utf-8"))

app = FastAPI(title="Proportionate Holds API", version="0.1.0",
              description="Trace, attribute, score and recommend in Exasol; an officer approves every hold.")
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


def decision_error(exc: officer.DecisionError) -> HTTPException:
    return HTTPException(400, str(exc))


def present(rec: dict) -> dict:
    """A recommendation row plus its plain-language headline and sentences."""
    out = {k: clean(v) for k, v in rec.items()}
    out["headline"] = headline(rec)
    out["explanation"] = explain(rec)
    out["bank_name"] = BANKS.get(rec.get("bank_id") or "", {}).get("name")
    return out


# ---------------------------------------------------------------- reference data

@app.get("/api/health")
def health(conn=Depends(get_conn)):
    version = conn.execute(
        "SELECT PARAM_VALUE FROM EXA_METADATA WHERE PARAM_NAME = 'databaseProductVersion'").fetchval()
    return {"ok": True, "exasol": version, "session": conn.session_id()}


@app.get("/api/config")
def config():
    return {
        "banks": [{"bank_id": k, **v} for k, v in BANKS.items()],
        "bands": SCORING["bands"],
        "warn_when_due_within_days": LANES.get("warn_when_due_within_days", 3),
        "lanes": {k: {"name": v["name"], "in_force": v["in_force"], "legal_basis": v["legal_basis"]}
                  for k, v in LANES["lanes"].items()},
        "rules": ["LIBR", "FIFO", "PRO_RATA"],
    }


# ---------------------------------------------------------------- inbox and tickets

@app.get("/api/cases")
def cases(bank: str = Query("BANK_X"), conn=Depends(get_conn)):
    """Case inbox: every complaint, whether its notice is complete, and the nearest open deadline at this bank."""
    check_bank(bank)
    return rows(conn, """
        SELECT c.case_id, c.ack_no, c.complaint_source, c.lane, c.utr, c.amount_inr, c.txn_at, c.reported_at,
               c.beneficiary_account, k.notice_status, k.problems,
               (SELECT COUNT(*) FROM PH.RECOMMENDATION r
                 WHERE r.case_id = c.case_id AND r.viewing_bank_id = {b}) AS recommendations,
               (SELECT COUNT(*) FROM PH.DECISION d
                 WHERE d.case_id = c.case_id AND d.viewing_bank_id = {b}) AS decisions,
               (SELECT MIN(dl.due_at) FROM PH.DEADLINE dl JOIN PH.DECISION d ON d.decision_id = dl.decision_id
                 WHERE d.case_id = c.case_id AND d.viewing_bank_id = {b} AND dl.deadline_status = 'OPEN')
                 AS next_due_at
        FROM PH.COMPLAINT c JOIN PH.COMPLAINT_CHECK k ON k.case_id = c.case_id
        ORDER BY c.reported_at DESC, c.case_id""", {"b": bank})


@app.get("/api/tickets")
def tickets(conn=Depends(get_conn)):
    """FR-02: which police notices are incomplete, and why."""
    return rows(conn, "SELECT * FROM PH.COMPLAINT_CHECK ORDER BY case_id")


# ---------------------------------------------------------------- running a case

@app.post("/api/cases/{case_id}/run")
def run_case(case_id: str, bank: str = Query("BANK_X"), as_of: str | None = None, rule: str | None = None,
             conn=Depends(get_conn)):
    """Run trace -> attribute -> score -> recommend in Exasol, as seen by `bank`. Places no holds."""
    check_bank(bank)
    check_case(conn, case_id)
    started = time.perf_counter()
    timings: dict[str, float] = {}
    used_as_of = None
    for name, fn in (("trace", lambda: steps.trace(conn, case_id, bank, as_of)),
                     ("attribute", lambda: steps.attribute(conn, case_id, bank, as_of, rule)),
                     ("score", lambda: steps.score(conn, case_id, as_of)),
                     ("recommend", lambda: steps.recommend(conn, case_id, bank, rule))):
        t0 = time.perf_counter()
        info = fn()
        timings[name] = round(time.perf_counter() - t0, 3)
        if name == "trace":
            used_as_of = info["as_of"]
    return {"case_id": case_id, "bank": bank, "as_of": used_as_of, "seconds": timings,
            "total_seconds": round(time.perf_counter() - started, 3)}


@app.get("/api/cases/{case_id}")
def case_detail(case_id: str, bank: str = Query("BANK_X"), conn=Depends(get_conn)):
    check_bank(bank)
    c = check_case(conn, case_id)
    c["check"] = one(conn, "SELECT notice_status, problems FROM PH.COMPLAINT_CHECK WHERE case_id = {c}",
                     {"c": case_id})
    c["as_of"] = conn.execute("SELECT MAX(as_of) FROM PH.ATTRIBUTION WHERE case_id = {c}", {"c": case_id}).fetchval()
    c["as_of"] = clean(c["as_of"])
    c.pop("victim_account", None)          # shown only on the victim's own view
    return c


@app.get("/api/cases/{case_id}/trail")
def trail(case_id: str, bank: str = Query("BANK_X"), rule: str = Query("LIBR"), conn=Depends(get_conn)):
    """The money trail as nodes and edges, with the victim money each edge carried under `rule`."""
    check_bank(bank)
    c = check_case(conn, case_id)
    edges = rows(conn, """
        SELECT e.hop, e.txn_id, e.from_account, e.to_account, e.amount_inr, e.ts, e.is_exit, e.is_cycle,
               e.trail_path, e.stmt_id, t.utr, t.channel, t.exit_identifier,
               COALESCE(f.victim_inr, 0) AS victim_inr
        FROM PH.TRACE_EDGE e
        JOIN PH.TXN t ON t.txn_id = e.txn_id
        LEFT JOIN PH.ATTRIBUTION_FLOW f ON f.case_id = e.case_id AND f.txn_id = e.txn_id
                                       AND f.trace_rule = {r} AND f.victim_tag = {v}
        WHERE e.case_id = {c} ORDER BY e.hop, e.ts, e.txn_id""",
                 {"c": case_id, "r": rule, "v": c["victim_tag"]})
    if not edges:
        raise HTTPException(409, f"Case {case_id} has not been run yet: POST /api/cases/{case_id}/run")
    accounts = {e["from_account"] for e in edges} | {e["to_account"] for e in edges}
    info = {a["account_id"]: a for a in rows(conn, """
        SELECT a.account_id, a.bank_id, a.holder_name, a.account_type, a.holder_type,
               s.band, s.score, att.victim_amount_inr, att.balance_now_inr
        FROM PH.ACCOUNT a
        LEFT JOIN PH.ACCOUNT_SCORE s ON s.case_id = {c} AND s.account_id = a.account_id
        LEFT JOIN PH.ATTRIBUTION att ON att.case_id = {c} AND att.account_id = a.account_id AND att.trace_rule = {r}
        WHERE a.account_id IN (SELECT to_account FROM PH.TRACE_EDGE WHERE case_id = {c})
           OR a.account_id IN (SELECT from_account FROM PH.TRACE_EDGE WHERE case_id = {c})""",
        {"c": case_id, "r": rule})}
    exits = {e["to_account"]: e for e in edges if e["is_exit"]}
    nodes = []
    for acc in sorted(accounts):
        if acc in exits:
            nodes.append({"id": acc, "kind": "exit", "label": exits[acc]["exit_identifier"] or acc})
            continue
        a = info.get(acc, {})
        is_victim = acc not in {e["to_account"] for e in edges}
        nodes.append({
            "id": acc, "kind": "victim" if is_victim else ("own" if a.get("bank_id") == bank else "other"),
            "bank_id": a.get("bank_id"), "bank_name": BANKS.get(a.get("bank_id") or "", {}).get("name"),
            "label": "Victim" if is_victim else (a.get("holder_name") or acc),
            "band": None if is_victim else a.get("band"), "score": None if is_victim else a.get("score"),
            "victim_amount_inr": None if is_victim else a.get("victim_amount_inr"),
            "balance_now_inr": None if is_victim else a.get("balance_now_inr"),
        })
    return {"case_id": case_id, "bank": bank, "rule": rule, "nodes": nodes, "edges": edges}


@app.get("/api/cases/{case_id}/recommendations")
def case_recommendations(case_id: str, bank: str = Query("BANK_X"), conn=Depends(get_conn)):
    check_bank(bank)
    check_case(conn, case_id)
    return [present(r) for r in steps.recommendations(conn, case_id, bank)]


@app.get("/api/cases/{case_id}/accounts/{account_id}")
def account_panel(case_id: str, account_id: str, bank: str = Query("BANK_X"), conn=Depends(get_conn)):
    """Everything the account panel shows: share under each rule, score with reasons, recommendation, decisions."""
    check_bank(bank)
    check_case(conn, case_id)
    attribution = rows(conn, """
        SELECT trace_rule, victim_amount_inr, other_victims_inr, own_funds_inr, balance_now_inr, as_of, stmt_id
        FROM PH.ATTRIBUTION WHERE case_id = {c} AND account_id = {a} ORDER BY trace_rule""",
                       {"c": case_id, "a": account_id})
    if not attribution:
        raise HTTPException(404, f"{account_id} is not on the trail of {case_id} (or the case has not been run)")
    account = one(conn, "SELECT * FROM PH.ACCOUNT WHERE account_id = {a}", {"a": account_id})
    score = one(conn, "SELECT score, band, never_account_hold, stmt_id FROM PH.ACCOUNT_SCORE "
                      "WHERE case_id = {c} AND account_id = {a}", {"c": case_id, "a": account_id})
    signals = rows(conn, """
        SELECT signal_name, signal_value, weight, mule_likeness, contribution, sentence, mule_rank, honest_rank
        FROM PH.ACCOUNT_SIGNALS WHERE case_id = {c} AND account_id = {a} ORDER BY mule_rank""",
                   {"c": case_id, "a": account_id})
    top_key = "mule_rank" if score and score["band"] in ("MULE", "REVIEW") else "honest_rank"
    reasons = [s["sentence"] for s in sorted(signals, key=lambda s: s[top_key])][: SCORING["top_reasons"]]
    rec = next((r for r in steps.recommendations(conn, case_id, bank) if r["account_id"] == account_id), None)
    decisions = rows(conn, """
        SELECT decision_id, action_code, amount_inr, decided_by, decided_at, reason_text, statement_ids
        FROM PH.DECISION WHERE case_id = {c} AND account_id = {a} AND viewing_bank_id = {b}
        ORDER BY decision_id""", {"c": case_id, "a": account_id, "b": bank})
    if account:
        account["bank_name"] = BANKS.get(account["bank_id"], {}).get("name")
    return {"case_id": case_id, "account_id": account_id, "bank": bank, "account": account,
            "attribution": attribution, "score": score, "reasons": reasons, "signals": signals,
            "recommendation": present(rec) if rec else None, "decisions": decisions}


# ---------------------------------------------------------------- officer actions (FR-10)

class Approve(BaseModel):
    officer: str = Field(..., description="Officer approving; required")
    comment: str | None = Field(None, description="Required for edits, whole-account holds and REVIEW band")
    amount: float | None = Field(None, description="Edited amount; must not exceed the balance")


class Reject(BaseModel):
    officer: str
    reason: str = Field(..., description="Required")


class Grievance(BaseModel):
    received_at: str = Field(..., description='"YYYY-MM-DD HH:MM:SS"')


@app.post("/api/cases/{case_id}/accounts/{account_id}/approve")
def approve(case_id: str, account_id: str, body: Approve, bank: str = Query("BANK_X"), conn=Depends(get_conn)):
    check_bank(bank)
    try:
        decision_id = officer.approve(conn, case_id, bank, account_id, body.officer, body.comment, body.amount)
    except officer.DecisionError as exc:
        raise decision_error(exc) from exc
    return {"decision_id": decision_id, "hold_ref": f"PH-{decision_id}"}


@app.post("/api/cases/{case_id}/accounts/{account_id}/reject")
def reject(case_id: str, account_id: str, body: Reject, bank: str = Query("BANK_X"), conn=Depends(get_conn)):
    check_bank(bank)
    try:
        decision_id = officer.reject(conn, case_id, bank, account_id, body.officer, body.reason)
    except officer.DecisionError as exc:
        raise decision_error(exc) from exc
    return {"decision_id": decision_id}


@app.post("/api/decisions/{decision_id}/grievance")
def grievance(decision_id: int, body: Grievance, conn=Depends(get_conn)):
    try:
        officer.grievance(conn, decision_id, body.received_at)
    except officer.DecisionError as exc:
        raise decision_error(exc) from exc
    return {"decision_id": decision_id, "clocks": "started"}


@app.get("/api/decisions")
def decisions(bank: str = Query("BANK_X"), conn=Depends(get_conn)):
    check_bank(bank)
    return rows(conn, """
        SELECT d.decision_id, d.case_id, d.account_id, d.action_code, d.amount_inr, d.recommended_action,
               d.recommended_amount, d.lane, d.decided_by, d.decided_at, d.reason_text, d.statement_ids
        FROM PH.DECISION d WHERE d.viewing_bank_id = {b} ORDER BY d.decision_id DESC""", {"b": bank})


@app.get("/api/decisions/{decision_id}/notice")
def notice(decision_id: int, lang: str = Query("en", pattern="^(en|hi|ta)$"), conn=Depends(get_conn)):
    body = conn.execute("SELECT notice_body FROM PH.NOTICE WHERE decision_id = {d} AND lang = {l}",
                        {"d": decision_id, "l": lang}).fetchval()
    if body is None:
        raise HTTPException(404, f"No {lang} notice for decision {decision_id} (only approved holds get one)")
    return {"decision_id": decision_id, "lang": lang, "body": body}


@app.get("/api/deadlines")
def deadlines(bank: str | None = None, conn=Depends(get_conn)):
    out = officer.deadlines(conn)
    return [d for d in out if bank is None or d["bank"] == bank]


# ---------------------------------------------------------------- victim view (FR-15) and audit

@app.get("/api/cases/{case_id}/victim")
def victim_status(case_id: str, conn=Depends(get_conn)):
    """What the victim is told: where her money is, how much is held (approved, at any bank), the next step."""
    c = check_case(conn, case_id)
    places = rows(conn, """
        SELECT att.account_id, a.bank_id, att.victim_amount_inr,
               (SELECT MAX(d.amount_inr) FROM PH.DECISION d
                 WHERE d.case_id = att.case_id AND d.account_id = att.account_id
                   AND d.action_code IN ('AMOUNT_HOLD', 'ACCOUNT_HOLD')) AS held_inr
        FROM PH.ATTRIBUTION att LEFT JOIN PH.ACCOUNT a ON a.account_id = att.account_id
        WHERE att.case_id = {c} AND att.trace_rule = 'LIBR' AND att.victim_amount_inr > 0
        ORDER BY att.account_id""", {"c": case_id})
    reported = float(c["amount_inr"] or 0)
    exited = sum(p["victim_amount_inr"] for p in places if p["account_id"].startswith("EXIT:"))
    secured = sum(min(p["victim_amount_inr"], p["held_inr"] or 0) for p in places
                  if not p["account_id"].startswith("EXIT:"))
    traceable = sum(p["victim_amount_inr"] for p in places if not p["account_id"].startswith("EXIT:"))
    threshold = LANES["lanes"]["A"]["interim_custody_threshold_inr"]
    if secured == 0:
        next_step = "Banks are acting on the accounts your money reached. Holds will appear here as they are placed."
    elif secured < threshold:
        next_step = (f"{inr(secured)} is held. Ask the police for its release to you: below {inr(threshold)} "
                     f"the magistrate can order interim custody under S.106(3) BNSS.")
    else:
        next_step = (f"{inr(secured)} is held. Above {inr(threshold)}, release needs an FIR; the police will "
                     f"guide you. You can also apply through the Money Restoration Module.")
    return {
        "case_id": case_id, "reported_inr": reported, "secured_inr": secured,
        "traceable_not_yet_held_inr": round(traceable - secured, 2), "lost_to_exits_inr": exited,
        "places": [{"where": "cash or crypto withdrawal" if p["account_id"].startswith("EXIT:")
                    else BANKS.get(p["bank_id"] or "", {}).get("name", p["bank_id"]),
                    "account_id": p["account_id"], "your_money_inr": p["victim_amount_inr"],
                    "held_inr": min(p["victim_amount_inr"], p["held_inr"] or 0)} for p in places],
        "next_step": next_step,
    }


@app.get("/api/audit")
def audit(case_id: str | None = None, limit: int = Query(200, le=1000), conn=Depends(get_conn)):
    """Every pipeline step and officer action with its Exasol session and statement ID (NFR-06)."""
    where = "WHERE case_id = {c}" if case_id else ""
    return rows(conn, f"SELECT * FROM PH.AUDIT_LOG {where} ORDER BY event_id DESC LIMIT {int(limit)}",
                {"c": case_id})