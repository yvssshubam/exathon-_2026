"""The golden-hour agent: works a cyber-fraud ticket the moment it lands, at any hour (docs/DESIGN.md).

    the agent orchestrates, the database calculates, the rules permit, the officer decides

Maker, checker, approver, as banks already run:
  * maker    this agent: works the ticket end to end through tools and drafts the case file
  * checker  sql/steps/075_check.sql, deterministic: amounts reconcile to the paisa and every automatic action is on
             the allowed list. It runs on its own after the rules engine; if anything fails, no tool that changes
             anything is offered and the case goes to the officer
  * approver the officer: every judgment call (narrow, full freeze, borderline) waits in the console

Two ways to choose the next tool, same tools, same guards:
  * pipeline  a fixed order (the default, and the fallback)
  * llm       a local model (pipeline/llm.py) picks the next tool from the ones the runtime allows right now and
              writes the summary; any failure hands control back to the fixed order for the rest of the run

The only tool that can hold money is place_mandatory_hold, which writes the holds the rules require. There is no
full-freeze tool: the agent can only propose one.

    python -m pipeline.agent CASE_DEMO                  # work one ticket (fixed order)
    python -m pipeline.agent CASE_DEMO --mode llm       # let the local model orchestrate
    python -m pipeline.agent --queue                    # work every QUEUED ticket in the order they arrived
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from dataclasses import dataclass, field

from pipeline.explain import inr

MAX_STEPS = 20


@dataclass
class ToolResult:
    text: str                          # one line: what the tool found or did
    stmt_ids: str = ""                 # Exasol statements behind it
    data: dict = field(default_factory=dict)


@dataclass(frozen=True)
class Tool:
    name: str
    description: str
    can_change: bool = False


TOOLS = {t.name: t for t in (
    Tool("read_ticket", "Read the ticket and the police notice; flag a missing amount, UTR or legal basis."),
    Tool("send_back_for_correction", "The notice is incomplete: draft a note asking the police to correct it, "
                                     "instead of freezing anything on bad data.", can_change=False),
    Tool("trace_money", "Follow the payment forward through every account it reached (Exasol, CONNECT BY)."),
    Tool("attribute_victim_share", "Work out the victim's rupees in each account (Exasol Python UDF, LIBR)."),
    Tool("get_account_evidence", "Read each account's 90-day history: eight signals with reasons (Exasol SQL)."),
    Tool("allowed_actions", "Ask the rules engine what is allowed per account: automatic, officer, or nothing."),
    Tool("place_mandatory_hold", "Place the holds the rules require (amount only), and first-layer markers.",
         can_change=True),
    Tool("record_exit", "File every exit (ATM, crypto, purchase) with its identifiers.", can_change=True),
    Tool("draft_onward_entry", "File an onward entry for every transfer to another bank, with the victim's share.",
         can_change=True),
    Tool("draft_kyc_packet", "Draft the KYC packet for every account held, for the officer to send.",
         can_change=True),
    Tool("escalate_to_officer", "Queue every judgment call (narrow, full freeze, borderline) for the officer."),
    Tool("write_case_summary", "Write the case summary for the officer's morning review. Ends the run."),
)}
CHANGE_TOOLS = ("place_mandatory_hold", "record_exit", "draft_onward_entry", "draft_kyc_packet")
ANALYSIS = ("read_ticket", "trace_money", "attribute_victim_share", "get_account_evidence", "allowed_actions")
PREREQ = {
    "send_back_for_correction": ("read_ticket",),
    "trace_money": ("read_ticket",),
    "attribute_victim_share": ("trace_money",),
    "get_account_evidence": ("attribute_victim_share",),
    "allowed_actions": ("get_account_evidence",),
    "place_mandatory_hold": ("allowed_actions",),
    "record_exit": ("allowed_actions",),
    "draft_onward_entry": ("allowed_actions",),
    "draft_kyc_packet": ("place_mandatory_hold",),
    "escalate_to_officer": ("allowed_actions",),
}

SYSTEM_PICK = (
    "You work cyber-fraud tickets for a bank's nodal officer, the moment they arrive, at any hour. "
    "Call exactly one of the offered tools: the one that should run next. Offered tools are the only ones allowed "
    "now; the runtime enforces the order and the checker. You never compute amounts and never decide holds: the "
    "database calculates, the rules permit and the officer decides. Finish the work before the summary.")
SYSTEM_WRITE = (
    "Write for a bank nodal officer starting the morning review. Plain words, no jargon, 3 to 6 short sentences. "
    "Say what was done automatically and what is waiting for a decision. Use only the facts given; copy every rupee "
    "figure exactly as written; do not add, total or round any amount.")


@dataclass
class State:
    ticket_ok: bool | None = None
    problems: str = ""
    check_passed: bool | None = None
    done: list[str] = field(default_factory=list)
    results: dict[str, str] = field(default_factory=dict)

    def required(self) -> list[str]:
        """Every tool that must run, in the fixed order, given what is known so far."""
        if self.ticket_ok is None:
            return ["read_ticket"]
        if self.ticket_ok is False:
            return ["read_ticket", "send_back_for_correction", "write_case_summary"]
        changes = [] if self.check_passed is False else list(CHANGE_TOOLS)
        return [*ANALYSIS, *changes, "escalate_to_officer", "write_case_summary"]

    def available(self) -> list[str]:
        out = []
        required = self.required()
        for name in required:
            if name in self.done:
                continue
            if name in CHANGE_TOOLS and self.check_passed is not True:
                continue
            if name == "write_case_summary":
                if all(n in self.done for n in required if n != "write_case_summary"):
                    out.append(name)
                continue
            if all(p in self.done for p in PREREQ.get(name, ())):
                out.append(name)
        return out

    @property
    def finished(self) -> bool:
        return "write_case_summary" in self.done


class Agent:
    def __init__(self, backend, mode: str = "pipeline", llm=None):
        if mode not in ("pipeline", "llm"):
            raise ValueError("mode must be pipeline or llm")
        self.backend, self.mode, self.llm = backend, mode, llm
        self.llm_ok = mode == "llm" and llm is not None
        self.step_no = 0

    # ------------------------------------------------------------ choosing
    def choose(self, state: State) -> tuple[str, str]:
        available = state.available()
        if not available:
            raise RuntimeError(f"no tool available after {state.done}")
        if not self.llm_ok:
            return available[0], "pipeline" if self.mode == "pipeline" else "fallback"
        offered = [{"type": "function", "function": {"name": n, "description": TOOLS[n].description,
                                                     "parameters": {"type": "object", "properties": {}}}}
                   for n in available]
        brief = json.dumps({"ticket_ok": state.ticket_ok, "done": [
            {"tool": n, "result": state.results.get(n, "")} for n in state.done], "offered": available})
        try:
            name = self.llm.pick_tool(SYSTEM_PICK, brief, offered)
        except Exception as exc:  # unreachable, slow or confused model: the fixed order takes over
            self.llm_ok = False
            self._log("model", "runtime", False, ToolResult(f"model unavailable, fixed order takes over: {exc}"[:500]))
            return available[0], "fallback"
        if name not in available:
            self._log("model", "runtime", False,
                      ToolResult(f"model asked for {name!r}, not allowed now; fixed order chose {available[0]}"))
            return available[0], "fallback"
        return name, "llm"

    # ------------------------------------------------------------ running
    def _log(self, tool: str, chosen_by: str, can_change: bool, result: ToolResult, ms: int = 0) -> None:
        self.step_no += 1
        self.backend.log_step(self.step_no, tool, chosen_by, can_change, result, ms)

    def _words(self, facts: str, fallback: str) -> str:
        """Model-written words if the model is up and its numbers are grounded in the facts; else the template."""
        if not self.llm_ok:
            return fallback
        from pipeline.llm import numbers_are_grounded

        try:
            text = self.llm.write(SYSTEM_WRITE, facts)
        except Exception:
            return fallback
        return text if numbers_are_grounded(text, facts) else fallback

    def call(self, name: str, state: State, chosen_by: str) -> ToolResult:
        b = self.backend
        t0 = time.perf_counter()
        if name == "write_case_summary":
            facts = b.summary_facts()
            template = send_back_template(facts) if state.ticket_ok is False else summary_template(facts)
            result = b.write_case_summary(self._words(facts["text"], template))
        elif name == "send_back_for_correction":
            facts = b.summary_facts()
            result = b.send_back_for_correction(self._words(facts["text"], send_back_template(facts)))
        else:
            result = getattr(b, name)()
        ms = int((time.perf_counter() - t0) * 1000)
        if name == "read_ticket":
            state.ticket_ok = bool(result.data.get("ok"))
            state.problems = result.data.get("problems", "")
        state.done.append(name)
        state.results[name] = result.text
        self._log(name, chosen_by, TOOLS[name].can_change, result, ms)
        if name == "allowed_actions":           # the checker always runs here, whatever chose the tools
            t0 = time.perf_counter()
            check = b.check()
            state.check_passed = bool(check.data.get("passed"))
            self._log("checker", "runtime", False, check, int((time.perf_counter() - t0) * 1000))
        return result

    def run(self) -> str:
        state = State()
        model = getattr(self.llm, "model", None) if self.mode == "llm" else None
        self.backend.start_run(self.mode, model)
        if self.mode == "llm" and self.llm is None:
            self._log("model", "runtime", False,
                      ToolResult("no local model configured (PH_LLM=ollama in .env); the fixed order runs instead"))
        try:
            while not state.finished:
                if self.step_no >= MAX_STEPS * 2:
                    raise RuntimeError("too many steps")
                name, chosen_by = self.choose(state)
                self.call(name, state, chosen_by)
        except Exception as exc:
            self._log("error", "runtime", False, ToolResult(str(exc)[:1900]))
            self.backend.finish("FAILED")
            return "FAILED"
        status = ("SENT_BACK" if state.ticket_ok is False
                  else "CHECK_FAILED" if state.check_passed is False else "DONE")
        self.backend.finish(status)
        return status


# ---------------------------------------------------------------- words (templates; the model may reword them)

def _list(items: list[str]) -> str:
    if len(items) <= 1:
        return "".join(items)
    return ", ".join(items[:-1]) + " and " + items[-1]


def summary_template(f: dict) -> str:
    """Deterministic case summary from the facts the backend gathered."""
    parts = [f"At {f['received_at'][11:16]} the ticket for {inr(f['paid'])} (UTR {f['utr']}) reached the bank "
             f"and the agent picked it up at once."]
    if f.get("check_failed"):
        parts.append("The checker found a problem (" + "; ".join(f["check_failed"]) + "), so nothing was held "
                     "automatically. The case is waiting for you.")
    if f.get("holds"):
        parts.append("Held, amount only: " + _list([f"{inr(h['amount'])} on {h['name']}" for h in f["holds"]]) + ".")
    if f.get("markers"):
        parts.append("Marked to catch later credits: " + _list([m["name"] for m in f["markers"]]) + ".")
    if f.get("exits"):
        parts.append("Exits filed: " + _list([f"{inr(e['victim'])} at {e['where']}" for e in f["exits"]]) + ".")
    if f.get("onward"):
        parts.append("Onward entries filed: " + _list([f"{inr(o['victim'])} to {o['bank']}" for o in f["onward"]])
                     + ".")
    if f.get("kyc"):
        parts.append(f"KYC packets drafted: {f['kyc']}, ready for you to send.")
    if f.get("waiting"):
        parts.append("Waiting for you: " + "; ".join(w["headline"] + f" ({w['name']})" for w in f["waiting"]) + ".")
    elif not f.get("check_failed"):
        parts.append("Nothing is waiting for you on this case.")
    return " ".join(parts)


def send_back_template(f: dict) -> str:
    return (f"The notice for ticket {f['case_id']} cannot be acted on as it stands: {f.get('problems') or 'incomplete'}"
            ". Please correct it on the portal; nothing was frozen on incomplete details.")


# ---------------------------------------------------------------- the Exasol backend

class ExasolBackend:
    """The tools, run against Exasol. Each one is a numbered SQL file or a read; each logs its statement IDs."""

    def __init__(self, conn, case_id: str, bank: str):
        from pipeline import steps

        self.conn, self.case_id, self.bank, self.steps = conn, case_id, bank, steps
        c = steps.complaint(conn, case_id)
        received = conn.execute("SELECT received_at FROM PH.TICKET WHERE case_id = {c}", {"c": case_id}).fetchval()
        self.received_at = str(received or c["reported_at"])[:19]
        self.placed_at = steps.default_as_of(self.received_at)
        self.run_id: int | None = None
        self.wall0 = time.perf_counter()

    # ---- bookkeeping
    def _q(self, sql: str, **params):
        return self.conn.execute(sql, {"c": self.case_id, "b": self.bank, **params})

    def _stmts(self, table: str, extra: str = "") -> str:
        where = "case_id = {c}" + (" AND viewing_bank_id = {b}" if extra == "bank" else "")
        ids = self._q(f"SELECT DISTINCT stmt_id FROM {table} WHERE {where} ORDER BY 1").fetchall()
        return ",".join(str(int(i)) for (i,) in ids if i is not None)

    def _last(self) -> str:
        return str(int(self.conn.execute("SELECT CURRENT_STATEMENT").fetchval()) - 1)

    def start_run(self, mode: str, model: str | None) -> None:
        self._q("INSERT INTO PH.AGENT_RUN (case_id, viewing_bank_id, run_mode, model_name, sim_started_at, "
                "wall_started_at) VALUES ({c}, {b}, {m}, {model}, TO_TIMESTAMP({t}, 'YYYY-MM-DD HH24:MI:SS'), "
                "CURRENT_TIMESTAMP)", m=mode, model=model, t=self.received_at)
        self.run_id = int(self._q("SELECT MAX(run_id) FROM PH.AGENT_RUN WHERE case_id = {c}").fetchval())
        self._q("UPDATE PH.TICKET SET ticket_status = 'WORKING', run_id = {r!d}, "
                "picked_up_at = TO_TIMESTAMP({t}, 'YYYY-MM-DD HH24:MI:SS') WHERE case_id = {c}",
                r=self.run_id, t=self.received_at)

    def log_step(self, step_no: int, tool: str, chosen_by: str, can_change: bool, result: ToolResult,
                 ms: int) -> None:
        self._q("INSERT INTO PH.AGENT_STEP (run_id, step_no, tool_name, chosen_by, can_change, result_text, stmt_ids, "
                "wall_ms) VALUES ({r!d}, {n!d}, {tool}, {by}, {chg!r}, {txt}, {ids}, {ms!d})",
                r=self.run_id, n=step_no, tool=tool, by=chosen_by, chg="TRUE" if can_change else "FALSE",
                txt=result.text[:2000],
                ids=result.stmt_ids[:400], ms=ms)

    def finish(self, status: str) -> None:
        ticket = {"DONE": "DONE", "CHECK_FAILED": "DONE", "SENT_BACK": "SENT_BACK"}.get(status, "FAILED")
        holds_at = self.placed_at if status in ("DONE",) else None
        self._q("UPDATE PH.AGENT_RUN SET run_status = {s}, wall_seconds = {w!d}, "
                "sim_holds_at = TO_TIMESTAMP({h}, 'YYYY-MM-DD HH24:MI:SS') WHERE run_id = {r!d}",
                s=status, w=round(time.perf_counter() - self.wall0, 3), h=holds_at, r=self.run_id)
        self._q("UPDATE PH.TICKET SET ticket_status = {s} WHERE case_id = {c}", s=ticket)

    # ---- analysis tools (read or compute; change nothing outside their own result tables)
    def read_ticket(self) -> ToolResult:
        status, problems = self._q("SELECT notice_status, problems FROM PH.COMPLAINT_CHECK WHERE case_id = {c}"
                                   ).fetchone()
        ids = self._last()
        ok = status == "VALID"
        text = "notice complete" if ok else f"notice incomplete: {problems}"
        return ToolResult(text, ids, {"ok": ok, "problems": problems or ""})

    def trace_money(self) -> ToolResult:
        self.steps.trace(self.conn, self.case_id, self.bank, self.placed_at)
        own, other, exits, hops = self._q(
            "SELECT SUM(CASE WHEN is_own_bank AND NOT is_exit THEN 1 ELSE 0 END), "
            "SUM(CASE WHEN NOT is_own_bank AND NOT is_exit THEN 1 ELSE 0 END), "
            "SUM(CASE WHEN is_exit THEN 1 ELSE 0 END), MAX(hop) FROM PH.TRACE_EDGE WHERE case_id = {c}").fetchone()
        return ToolResult(f"{int(own or 0)} hops into this bank's accounts, {int(other or 0)} to other banks, "
                          f"{int(exits or 0)} exits; {int(hops or 0)} hops deep",
                          self._stmts("PH.TRACE_EDGE"))

    def attribute_victim_share(self) -> ToolResult:
        self.steps.attribute(self.conn, self.case_id, self.bank, self.placed_at)
        rows = self._q("SELECT a.account_id, COALESCE(acc.holder_name, a.account_id), a.victim_amount_inr "
                       "FROM PH.ATTRIBUTION a LEFT JOIN PH.ACCOUNT acc ON acc.account_id = a.account_id "
                       "WHERE a.case_id = {c} AND a.trace_rule = 'LIBR' AND a.victim_amount_inr > 0 "
                       "ORDER BY a.victim_amount_inr DESC").fetchall()
        shares = ", ".join(f"{name} {inr(v)}" for _, name, v in rows[:6])
        return ToolResult(f"victim's money by account (LIBR): {shares}", self._stmts("PH.ATTRIBUTION"))

    def get_account_evidence(self) -> ToolResult:
        self.steps.score(self.conn, self.case_id, self.placed_at)
        rows = self._q("SELECT COALESCE(acc.holder_name, s.account_id), s.band, s.score FROM PH.ACCOUNT_SCORE s "
                       "JOIN PH.ACCOUNT acc ON acc.account_id = s.account_id "
                       "WHERE s.case_id = {c} AND acc.bank_id = {b} ORDER BY s.account_id").fetchall()
        words = {"MULE": "pass-through pattern", "REVIEW": "mixed pattern", "HONEST": "ordinary-customer pattern"}
        text = "; ".join(f"{n}: {words.get(band, band)} ({float(s):.2f})" for n, band, s in rows)
        return ToolResult(f"account evidence at this bank: {text or 'no own accounts on the trail'}",
                          self._stmts("PH.ACCOUNT_SCORE"))

    def allowed_actions(self) -> ToolResult:
        self.steps.actions(self.conn, self.case_id, self.bank)
        auto, officer = self._q(
            "SELECT SUM(CASE WHEN act_mode = 'AUTOMATIC' THEN 1 ELSE 0 END), "
            "SUM(CASE WHEN act_mode = 'OFFICER' THEN 1 ELSE 0 END) FROM PH.CASE_ACTION "
            "WHERE case_id = {c} AND viewing_bank_id = {b}").fetchone()
        return ToolResult(f"{int(auto or 0)} automatic actions allowed, {int(officer or 0)} judgment calls for the "
                          f"officer", self._stmts("PH.CASE_ACTION", "bank"))

    def check(self) -> ToolResult:
        results = self.steps.check(self.conn, self.case_id, self.bank)
        failed = [f"{c['check_name']}: {c['detail']}" for c in results if not c["check_passed"]]
        passed = bool(results) and not failed
        text = (f"all {len(results)} checks passed" if passed else "FAILED " + "; ".join(failed))
        return ToolResult(text, self._stmts("PH.CHECK_RESULT", "bank"), {"passed": passed})

    # ---- change-making tools (each SQL file refuses to act unless the checker passed)
    def place_mandatory_hold(self) -> ToolResult:
        self.steps.execute(self.conn, self.case_id, self.bank, "081_hold.sql", self.placed_at)
        rows = self._q("SELECT COALESCE(acc.holder_name, h.account_id), h.hold_kind, h.amount_inr "
                       "FROM PH.HOLD_LEDGER h JOIN PH.ACCOUNT acc ON acc.account_id = h.account_id "
                       "WHERE h.case_id = {c} AND h.viewing_bank_id = {b} AND h.placed_by = 'agent' "
                       "ORDER BY h.hold_id").fetchall()
        text = ", ".join(f"{n} {inr(a)}" if k == "LIEN" else f"{n} (marker)" for n, k, a in rows)
        return ToolResult(f"held at {self.placed_at[11:16]}: {text or 'nothing to hold'}",
                          self._stmts("PH.HOLD_LEDGER", "bank"))

    def _filed(self, kind: str, file_name: str, label: str) -> ToolResult:
        self.steps.execute(self.conn, self.case_id, self.bank, file_name, self.placed_at)
        n, victim = self._q("SELECT COUNT(*), SUM(victim_amount_inr) FROM PH.PORTAL_ENTRY WHERE case_id = {c} "
                            "AND viewing_bank_id = {b} AND entry_kind = {k}", k=kind).fetchone()
        ids = self._q("SELECT DISTINCT stmt_id FROM PH.PORTAL_ENTRY WHERE case_id = {c} AND viewing_bank_id = {b} "
                      "AND entry_kind = {k}", k=kind).fetchall()
        money = f", {inr(victim)} of the victim's money" if victim is not None and kind != "KYC" else ""
        return ToolResult(f"{int(n or 0)} {label}{money}", ",".join(str(int(i)) for (i,) in ids if i))

    def record_exit(self) -> ToolResult:
        return self._filed("EXIT", "082_exits.sql", "exits filed")

    def draft_onward_entry(self) -> ToolResult:
        return self._filed("ONWARD", "083_onward.sql", "onward entries filed")

    def draft_kyc_packet(self) -> ToolResult:
        return self._filed("KYC", "084_kyc.sql", "KYC packets drafted")

    def escalate_to_officer(self) -> ToolResult:
        self._q("UPDATE PH.CASE_ACTION SET action_status = 'WAITING' WHERE case_id = {c} AND viewing_bank_id = {b} "
                "AND act_mode = 'OFFICER' AND action_status = 'PROPOSED'")
        ids = self._last()
        n = self._q("SELECT COUNT(*) FROM PH.CASE_ACTION WHERE case_id = {c} AND viewing_bank_id = {b} "
                    "AND act_mode = 'OFFICER' AND action_status = 'WAITING'").fetchval()
        return ToolResult(f"{int(n or 0)} decisions waiting for the officer", ids)

    # ---- words
    def summary_facts(self) -> dict:
        from pipeline.explain import headline

        q = self._q
        c = q("SELECT c.utr, c.amount_inr, k.problems FROM PH.COMPLAINT c "
              "JOIN PH.COMPLAINT_CHECK k ON k.case_id = c.case_id WHERE c.case_id = {c}").fetchone()
        paid = q("SELECT SUM(t.amount_inr) FROM PH.TXN t JOIN PH.COMPLAINT c ON c.utr = t.utr "
                 "WHERE c.case_id = {c}").fetchval()
        names = dict(q("SELECT account_id, COALESCE(holder_name, account_id) FROM PH.ACCOUNT "
                   "WHERE account_id IN (SELECT to_account FROM PH.TRACE_EDGE WHERE case_id = {c})").fetchall())
        holds = [{"name": names.get(a, a), "amount": amt} for a, kind, amt in q(
            "SELECT account_id, hold_kind, amount_inr FROM PH.HOLD_LEDGER WHERE case_id = {c} AND viewing_bank_id = "
            "{b} AND placed_by = 'agent' AND hold_kind = 'LIEN' ORDER BY hold_id").fetchall()]
        markers = [{"name": names.get(a, a)} for (a,) in q(
            "SELECT account_id FROM PH.HOLD_LEDGER WHERE case_id = {c} AND viewing_bank_id = {b} "
            "AND placed_by = 'agent' AND hold_kind = 'MARKER' ORDER BY hold_id").fetchall()]
        exits = [{"victim": v, "where": d or a} for a, v, d in q(
            "SELECT account_id, victim_amount_inr, detail FROM PH.PORTAL_ENTRY WHERE case_id = {c} "
            "AND viewing_bank_id = {b} AND entry_kind = 'EXIT' ORDER BY entry_id").fetchall()]
        onward = [{"victim": v, "bank": bk} for v, bk in q(
            "SELECT p.victim_amount_inr, COALESCE(bk.bank_name, p.bank_id) FROM PH.PORTAL_ENTRY p "
            "LEFT JOIN PH.BANK bk ON bk.bank_id = p.bank_id WHERE p.case_id = {c} AND p.viewing_bank_id = {b} "
            "AND p.entry_kind = 'ONWARD' ORDER BY p.entry_id").fetchall()]
        kyc = q("SELECT COUNT(*) FROM PH.PORTAL_ENTRY WHERE case_id = {c} AND viewing_bank_id = {b} "
                "AND entry_kind = 'KYC'").fetchval()
        waiting = [{"name": names.get(a, a), "headline": headline({"action_code": code, "amount_inr": amt,
                                                                  "account_id": a})}
                   for a, code, amt in q("SELECT account_id, action_code, amount_inr FROM PH.CASE_ACTION "
                                         "WHERE case_id = {c} AND viewing_bank_id = {b} AND act_mode = 'OFFICER' "
                                         "AND action_status IN ('PROPOSED', 'WAITING') "
                                         "ORDER BY hop, account_id").fetchall()]
        failed = [f"{n}: {d}" for n, d in q("SELECT check_name, detail FROM PH.CHECK_RESULT WHERE case_id = {c} "
                                            "AND viewing_bank_id = {b} AND NOT check_passed").fetchall()]
        facts = {"case_id": self.case_id, "received_at": self.received_at, "utr": c[0] if c else "",
                 "paid": paid or (c[1] if c else 0), "problems": c[2] if c else "", "holds": holds,
                 "markers": markers, "exits": exits, "onward": onward, "kyc": int(kyc or 0), "waiting": waiting,
                 "check_failed": failed}
        facts["text"] = summary_template(facts) if not facts["problems"] else send_back_template(facts)
        return facts

    def write_case_summary(self, text: str) -> ToolResult:
        self._q("UPDATE PH.AGENT_RUN SET summary_text = {t} WHERE run_id = {r!d}", t=text[:4000], r=self.run_id)
        return ToolResult(text[:2000], self._last())

    def send_back_for_correction(self, text: str) -> ToolResult:
        self._q("UPDATE PH.AGENT_RUN SET summary_text = {t} WHERE run_id = {r!d}", t=text[:4000], r=self.run_id)
        return ToolResult("send-back note drafted: " + text[:1900], self._last())


# ---------------------------------------------------------------- entry points

def work_ticket(conn, case_id: str, bank: str, mode: str = "pipeline") -> dict:
    from pipeline.llm import client

    llm = client() if mode == "llm" else None
    backend = ExasolBackend(conn, case_id, bank)
    status = Agent(backend, mode, llm).run()
    return {"case_id": case_id, "run_id": backend.run_id, "status": status,
            "seconds": round(time.perf_counter() - backend.wall0, 3)}


def queued(conn) -> list[str]:
    return [c for (c,) in conn.execute(
        "SELECT case_id FROM PH.TICKET WHERE ticket_status = 'QUEUED' ORDER BY received_at, case_id").fetchall()]


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("case_id", nargs="?")
    ap.add_argument("--queue", action="store_true", help="work every QUEUED ticket, oldest first")
    ap.add_argument("--mode", choices=["pipeline", "llm"], default="pipeline")
    ap.add_argument("--bank", help="bank the agent works for (default: OWN_BANK_ID in .env)")
    args = ap.parse_args(argv)
    if not args.case_id and not args.queue:
        ap.error("give a case_id or --queue")

    from pipeline.db import connect, settings

    bank = args.bank or settings()["own_bank_id"]
    conn = connect()
    try:
        cases = queued(conn) if args.queue else [args.case_id]
        for case_id in cases:
            r = work_ticket(conn, case_id, bank, args.mode)
            print(f"{case_id:12} {r['status']:12} run {r['run_id']}  {r['seconds']:.2f}s")
            for step_no, tool, by, text, ids, ms in conn.execute(
                    "SELECT step_no, tool_name, chosen_by, result_text, stmt_ids, wall_ms FROM PH.AGENT_STEP "
                    "WHERE run_id = {r} ORDER BY step_no", {"r": r["run_id"]}).fetchall():
                shown = text if tool == "error" else text[:100]          # errors in full: they name the cause
                print(f"   {int(step_no):>2} {tool:26} {int(ms or 0):>6} ms [{by:8}] {shown}  (stmt {ids})")
        if not cases:
            print("no queued tickets")
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())