"""Run the simulated night across every bank (the demo; Module 8). One clock, three banks, one portal.

Events happen in time order, as they would:
  * the portal delivers each ticket when the clock reaches it (pipeline/portal.py)
  * a bank that runs the product (Bank X, Bank Y) works a ticket the minute it lands: the maker proposes, the checker
    carries out (pipeline/agent.py, pipeline/checker.py); its filings are collected by the portal, and an onward
    entry becomes the next bank's ticket a minute later
  * a bank without it (Bank Z) works the same ticket when its desk opens (config/banks.yaml), with the same code:
    "same agent, different settings" answers what happens when the next bank doesn't use the product
  * when a ticket's trail shares an account with an earlier case, only those cases are recalculated (pipeline/recalc.py)
  * payments on held or watched accounts go through the simulated core banking (pipeline/core.py): a debit a hold
    covers is refused into the declined-attempts feed, and the agent's watch reacts (pipeline/watch.py)
  * at the end, each product bank's morning log is written (pipeline/morning.py)

No bank calls another: the portal is the only link, and each bank's agent and checker use that bank's database
users, which cannot read any other bank's schema.

    python -m pipeline.night                       # reset, then run the demo night to 09:00
    python -m pipeline.night --until "2026-10-18 10:30:00" --no-reset    # continue (Bank Z's desk opens at 10)
    python -m pipeline.night --mode llm            # the local model orchestrates the product banks' agents
"""
from __future__ import annotations

import argparse
import sys
from datetime import datetime, timedelta
from decimal import Decimal

from pipeline.db import BANKS, PORTAL_SCHEMA, schema_for

NIGHT_START = "2026-10-18 00:00:00"
MORNING = "2026-10-18 09:00:00"
FMT = "%Y-%m-%d %H:%M:%S"


def _ts(v) -> str:
    return str(v)[:19]


def is_desk(bank: str) -> bool:
    return BANKS[bank].get("works_tickets") == "desk"


def desk_pickup(bank: str, received: str) -> str:
    """When a desk bank gets to a ticket: its next opening, plus the first lookup."""
    from pipeline.today import desk_opens_after

    b = BANKS[bank]
    opens = desk_opens_after(received, str(b.get("desk_opens", "10:00")))
    return (opens + timedelta(minutes=int(b.get("first_lookup_minutes", 20)))).strftime(FMT)


class Night:
    def __init__(self, mode: str = "pipeline", llm=None, banks: list[str] | None = None, quiet: bool = False):
        from pipeline.db import connect

        self.mode, self.quiet = mode, quiet
        if mode == "llm" and llm is None:
            from pipeline.llm import client

            llm = client()
        self.llm = llm
        self.banks = banks or list(BANKS)
        self.admin = connect()
        self.portal = connect("portal")
        self.makers = {b: connect("maker", b) for b in BANKS}
        self.checkers = {b: connect("checker", b) for b in BANKS}
        self.lines: list[dict] = []
        self.processed: set[tuple[str, str, str]] = set()
        self._load_processed()

    def close(self) -> None:
        for c in [self.admin, self.portal, *self.makers.values(), *self.checkers.values()]:
            try:
                c.close()
            except Exception:  # noqa: S110 - closing best-effort
                pass

    # ------------------------------------------------------------ the clock
    def clock(self) -> str | None:
        v = self.admin.execute(f"SELECT MAX(clock_at) FROM {PORTAL_SCHEMA}.SIM_CLOCK").fetchval()
        return _ts(v) if v else None

    def set_clock(self, t: str) -> None:
        start = self.admin.execute(f"SELECT MIN(started_at) FROM {PORTAL_SCHEMA}.SIM_CLOCK").fetchval()
        self.admin.execute(f"DELETE FROM {PORTAL_SCHEMA}.SIM_CLOCK")
        self.admin.execute(f"INSERT INTO {PORTAL_SCHEMA}.SIM_CLOCK (clock_at, started_at) VALUES "
                           "(TO_TIMESTAMP({t}, 'YYYY-MM-DD HH24:MI:SS'), TO_TIMESTAMP({s}, 'YYYY-MM-DD HH24:MI:SS'))",
                           {"t": t, "s": _ts(start) if start else NIGHT_START})

    def _load_processed(self) -> None:
        """Payments already handled in an earlier run (so a continued night does not handle them twice)."""
        for bank in BANKS:
            s = schema_for(bank)
            for (ref,) in self.admin.execute(f"SELECT txn_ref FROM {s}.DECLINED_ATTEMPT UNION "
                                             f"SELECT txn_ref FROM {s}.UNDELIVERED_CREDIT UNION "
                                             f"SELECT txn_ref FROM {s}.WATCH_EVENT "
                                             "WHERE txn_ref IS NOT NULL").fetchall():
                for kind in ("TXN", "REQUEST"):
                    self.processed.add((bank, kind, ref))

    def say(self, at: str, bank: str | None, text: str) -> None:
        self.lines.append({"at": at, "bank": bank, "text": text})
        if not self.quiet:
            who = BANKS[bank]["name"] if bank else "Portal"
            print(f"{at[11:16]}  {who:7} {text}")

    # ------------------------------------------------------------ what happens next
    def candidates(self, until: str) -> list[tuple]:
        from pipeline import core, portal

        out = []
        nt = portal.next_ticket_at(self.portal)
        if nt and nt <= until:
            out.append((nt, 0, "deliver", None))
        for bank in self.banks:
            for case_id, received in self.makers[bank].execute(
                    "SELECT case_id, received_at FROM TICKET WHERE ticket_status = 'QUEUED' "
                    "ORDER BY received_at, case_id").fetchall():
                pickup = desk_pickup(bank, _ts(received)) if is_desk(bank) else _ts(received)
                pickup = max(pickup, self.clock() or pickup)      # a re-queued ticket is worked now, not in the past
                if pickup <= until:
                    out.append((pickup, 1, "ticket", (bank, case_id)))
            if not is_desk(bank):
                from pipeline.notices import pending as notices_pending

                for nid, at in notices_pending(self.makers[bank], until)[:1]:   # e-mailed notices, one at a time
                    out.append((max(at, self.clock() or at), 1, "notice", (bank, nid)))
            for p in core.pending(self.admin, bank, NIGHT_START, until):
                if (bank, p.kind, p.ref) not in self.processed:
                    out.append((p.ts, 2, "payment", (bank, p)))
                    break
        return out

    def run(self, until: str = MORNING) -> dict:
        from pipeline import portal

        start = self.clock() or NIGHT_START
        if not self.clock():
            self.set_clock(NIGHT_START)
        steps = 0
        logs = {}
        # the handover note for the morning desk is written as the clock passes 09:00, so it is the night as it
        # stood then, even when the run goes on through the day
        handover = start < MORNING <= until

        def write_logs(frm: str, to: str) -> None:
            from pipeline.morning import write

            for bank in self.banks:
                if not is_desk(bank):
                    logs[bank] = write(self.makers[bank], bank, frm, to, self.llm)

        while True:
            cand = self.candidates(until)
            if not cand:
                break
            t, _, kind, obj = min(cand, key=lambda c: (c[0], c[1]))
            if handover and t > MORNING:
                write_logs(start, MORNING)
                handover = False
            steps += 1
            if steps > 5000:
                raise RuntimeError("the night did not settle (more than 5000 events)")
            if kind == "deliver":
                for bank, case_id, at in portal.deliver(self.portal, t):
                    self.say(at, None, f"ticket {case_id} reaches {BANKS[bank]['name']}")
            elif kind == "ticket":
                self.work(obj[0], obj[1], t)
            elif kind == "notice":
                self.notice(obj[0], obj[1], t)
            else:
                self.payment(obj[0], obj[1])
            if t > (self.clock() or NIGHT_START):
                self.set_clock(t)
        for bank in self.banks:
            if not is_desk(bank):
                from pipeline.watch import Watch

                for line in Watch(self.makers[bank], self.checkers[bank], bank).tick(until):
                    self.say(until, bank, line)
        self.set_clock(max(until, self.clock() or until))
        if handover:
            write_logs(start, MORNING)
        if until > MORNING or start >= MORNING:
            write_logs(max(start, MORNING) if start < MORNING else start, until)
        return {"from": start, "until": until, "events": steps, "lines": self.lines, "morning": logs}

    # ------------------------------------------------------------ handlers
    def notice(self, bank: str, notice_id: str, at: str) -> None:
        """A police notice in the bank's e-mail: the agent reads it into a ticket, which is then worked as any other."""
        from pipeline.notices import register

        r = register(self.makers[bank], bank, notice_id, at, self.llm)
        if r["status"] != "READ":
            self.say(at, bank, f"could not read notice {notice_id}: {r['text']}. It waits for the officer.")
            return
        f = r["fields"]
        got = ", ".join(x for x in (f["utr"] and f"UTR {f['utr']}", f["amount_inr"] is not None and
                                    f"Rs {Decimal(str(f['amount_inr'])):,.2f}", f["legal_basis"]) if x)
        self.say(at, bank, f"read the notice {notice_id} ({'by the model' if r['read_by'] == 'model' else 'parser'}): "
                           f"{got or 'no UTR, amount or provision found'}; it joins the queue as a ticket")

    def work(self, bank: str, case_id: str, at: str) -> None:
        from pipeline import portal, recalc
        from pipeline.agent import work_ticket

        desk = is_desk(bank)
        mode = "pipeline" if desk else self.mode
        r = work_ticket(self.makers[bank], self.checkers[bank], case_id, bank, mode, now=at,
                        llm=None if desk else self.llm, label="desk" if desk else None)
        summary = self.makers[bank].execute("SELECT summary_text FROM AGENT_RUN WHERE run_id = {r}",
                                            {"r": r["run_id"]}).fetchval() or r["status"]
        who = "the desk" if desk else "the agent"
        if r["status"] == "FAILED":                 # say why: the run log holds the error in full
            err = self.makers[bank].execute(
                "SELECT result_text FROM AGENT_STEP WHERE run_id = {r} AND tool_name = 'error' ORDER BY step_no DESC",
                {"r": r["run_id"]}).fetchval()
            step = self.makers[bank].execute(
                "SELECT tool_name FROM AGENT_STEP WHERE run_id = {r} AND tool_name NOT IN ('error', 'model') "
                "ORDER BY step_no DESC", {"r": r["run_id"]}).fetchval()
            summary = f"FAILED after {step or 'the start'}: {err or 'no error recorded'}"
        self.say(at, bank, f"{who} works {case_id}: {summary}")
        if r.get("checker"):
            self.say(r["placed_at"], bank, r["checker"]["text"])
        for ticket_id in portal.collect(self.portal, bank):
            row = self.portal.execute("SELECT case_id, to_bank_id, created_at FROM TICKET WHERE ticket_id = {t}",
                                      {"t": ticket_id}).fetchone()
            self.say(_ts(row[2]), None, f"onward entry becomes ticket {row[0]} for {BANKS[row[1]]['name']}")
        if r["status"] in ("DONE", "CHECK_FAILED") and recalc.related_cases(self.makers[bank], case_id, bank):
            res = recalc.recalculate(self.makers[bank], self.checkers[bank], bank, case_id, r["placed_at"])
            self.say(r["placed_at"], bank, res["text"])

    def refused_at_sender(self, bank: str, p) -> bool:
        """A credit from an account at another bank: that bank's core banking decides first, and if its hold refused
        the payment, the money never arrives here (no watch, no hold, no pre-alert). True when it was refused."""
        sender = self.makers[bank].execute("SELECT bank_id FROM ACCOUNT WHERE account_id = {a}",
                                           {"a": p.from_account}).fetchval()
        if not sender or sender == bank or sender not in BANKS:
            return False
        if (sender, p.kind, p.ref) not in self.processed and sender in self.banks:
            from pipeline import core

            for q in core.pending(self.admin, sender, NIGHT_START, p.ts):   # the payment as the sending bank sees it
                if q.key == p.key:
                    self.payment(sender, q)                  # the sending bank handles its side first
                    break
        if not self.admin.execute(f"SELECT COUNT(*) FROM {schema_for(bank)}.UNDELIVERED_CREDIT WHERE txn_ref = {{r}}",
                                  {"r": p.ref}).fetchval():
            return False
        self.say(p.ts, bank, f"{p.from_account}'s payment of Rs {p.amount:,.2f} never arrived: "
                             f"{BANKS[sender]['name']} refused it (held)")
        return True

    def payment(self, bank: str, p) -> None:
        from pipeline import core
        from pipeline.watch import Watch

        self.processed.add((bank, p.kind, p.ref))
        if p.kind == "TXN" and self.refused_at_sender(bank, p):
            return
        attempt = core.apply(self.admin, bank, p)
        desk = is_desk(bank)
        w = None if desk else Watch(self.makers[bank], self.checkers[bank], bank)
        if attempt:
            self.say(p.ts, bank, f"core banking refused {p.from_account}'s {p.channel} payment of "
                                 f"Rs {p.amount:,.2f} (held)")
            for line in (w.on_declined(attempt) if w else []):
                self.say(p.ts, bank, line)
            return
        if w is None or p.kind != "TXN":
            return
        own = {a for (a,) in self.makers[bank].execute(
            "SELECT account_id FROM ACCOUNT WHERE account_id IN ({a}) AND bank_id = {b}",
            {"a": [p.from_account, p.to_account], "b": bank}).fetchall()}
        if p.to_account in own:
            for line in w.on_credit(p.to_account, p.ref, p.ts, p.amount, p.from_account):
                self.say(p.ts, bank, line)
        if p.from_account in own:
            for line in w.on_debit(p.from_account, p.ref, p.ts, p.amount, p.to_account, p.channel, p.exit_identifier):
                self.say(p.ts, bank, line)


def run_night(until: str = MORNING, mode: str = "pipeline", reset: bool = True, banks: list[str] | None = None,
              quiet: bool = False, amlworld: bool = True) -> dict:
    """Reset (optional) and run the night to `until`. The console's Replay button calls this."""
    from pipeline.db import connect

    if reset:
        from pipeline.load_cases import reset as reload

        conn = connect()
        try:
            reload(conn, amlworld=amlworld)
        finally:
            conn.close()
    night = Night(mode, banks=banks, quiet=quiet)
    try:
        return night.run(until)
    finally:
        night.close()


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--until", default=MORNING, help=f'simulated time to stop at (default "{MORNING}")')
    ap.add_argument("--mode", choices=["pipeline", "llm"], default="pipeline")
    ap.add_argument("--no-reset", action="store_true", help="continue from where the clock stopped")
    ap.add_argument("--bank", action="append", help="only these banks (repeatable), e.g. --bank BANK_Z")
    ap.add_argument("--no-amlworld", action="store_true", help="on reset, leave the AMLworld background out")
    args = ap.parse_args(argv)
    try:
        datetime.strptime(args.until, FMT)
    except ValueError:
        ap.error('--until must look like "2026-10-18 09:00:00"')
    r = run_night(args.until, args.mode, not args.no_reset, args.bank, amlworld=not args.no_amlworld)
    print(f"\nran {r['events']} events from {r['from']} to {r['until']}")
    for bank, log in r["morning"].items():
        print(f"\nMorning log, {BANKS[bank]['name']} ({log['written_by']}):\n{log['text']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())