"""The case stays live (Module 5; idea 1 with a smaller claim, idea 9's tag, idea 3's exit). No model here: a trigger,
a lookup and three branches, every time.

After the first pass, every own account a case touched is on the watch list with its first-pass decision
(mule-like, or an honest receiver). The simulated clock checks the watched accounts as payments happen
(pipeline/night.py; a query a minute is enough, because the hold already protects the money):

  new money, eligible account    (Layer 1 or Suspect Registry, typed Pipe for fraud or Fast mover): the payment checks
                                 on arrival, then a proactive hold of that payment through the checker and its
                                 pre-alert (pipeline/proactive.py, D-60 to D-64). A later ticket for that payment
                                 links the money to its victim
  new money, any other account   let it through and say why: the score can stop a hold, never start one
  refused cash-out, mule-like    record it as evidence and propose a full freeze (the officer decides)
  refused transfer               the recipient is at this bank: check its evidence, put it on the watch, no hold
                                 (no victim money reached it); a business recipient is noted, never labelled; at
                                 another bank: recorded in the case for the police
  day 75, no police direction    the account leaves the watch and the officer gets one release flag

Every action goes through the checker (pipeline/checker.py). The watch never holds above the reported amount and
never alerts anyone at night: the morning log is the notification.
"""
from __future__ import annotations

from datetime import datetime, timedelta
from decimal import Decimal

import yaml

from pipeline.db import ROOT
from pipeline.explain import inr
from pipeline.recalc import propose

RULES = yaml.safe_load((ROOT / "config" / "rules.yaml").read_text(encoding="utf-8"))
CASH_OUT = tuple(RULES["full_freeze_evidence"]["watch_cash_out_channels"])
BUSINESS = tuple(RULES["watch"]["business_holder_types"])
RELEASE_DAYS = int(RULES["watch"]["release_review_days"])
FMT = "%Y-%m-%d %H:%M:%S"


def _d(v) -> Decimal:
    return Decimal(str(v)) if v is not None else Decimal(0)


def _ts(v) -> str:
    return str(v)[:19]


class Watch:
    def __init__(self, maker, checker, bank: str):
        from pipeline.checker import Checker

        self.maker, self.bank = maker, bank
        self.checker = Checker(checker, bank)

    def _q(self, sql: str, **p):
        return self.maker.execute(sql, {"b": self.bank, **p})

    def name(self, account: str) -> str:
        return self._q("SELECT COALESCE(holder_name, account_id) FROM ACCOUNT WHERE account_id = {a}",
                       a=account).fetchval() or account

    def entries(self, account: str) -> list[dict]:
        rows = self._q("SELECT case_id, first_pass, reported_inr, added_at FROM WATCH_LIST WHERE account_id = {a} "
                       "AND left_at IS NULL ORDER BY added_at, case_id", a=account).fetchall()
        return [{"case_id": c, "first_pass": f, "reported": _d(r), "added_at": _ts(t)} for c, f, r, t in rows]

    def event(self, account: str, case_id: str | None, at: str, kind: str, sentence: str, amount=None,
              counterparty: str | None = None, channel: str | None = None, ref: str | None = None,
              attempt_id: int | None = None, proposal_id: int | None = None) -> None:
        hold_id = None
        if proposal_id:
            hold_id = self._q("SELECT MAX(hold_id) FROM HOLD_LEDGER WHERE proposal_id = {p!d}",
                              p=str(proposal_id)).fetchval()
        self._q("INSERT INTO WATCH_EVENT (viewing_bank_id, account_id, case_id, event_at, event_kind, amount_inr, "
                "counterparty, channel, txn_ref, attempt_id, hold_id, proposal_id, sentence, stmt_id) VALUES "
                "({b}, {a}, {c}, TO_TIMESTAMP({t}, 'YYYY-MM-DD HH24:MI:SS'), {k}, {amt}, {cp}, {ch}, {ref}, {att}, "
                "{h}, {p}, {s}, CURRENT_STATEMENT)",
                a=account, c=case_id, t=at, k=kind, amt=None if amount is None else str(amount),
                cp=(counterparty or "")[:200] or None, ch=channel, ref=ref,
                att=None if attempt_id is None else str(int(attempt_id)),
                h=None if hold_id is None else str(int(hold_id)),
                p=None if proposal_id is None else str(int(proposal_id)), s=sentence[:1000])

    def counterparty(self, account: str | None) -> str:
        if not account:
            return "another account"
        row = self._q("SELECT a.bank_id, b.bank_name, b.is_own_bank FROM ACCOUNT a LEFT JOIN BANK b "
                      "ON b.bank_id = a.bank_id WHERE a.account_id = {a}", a=account).fetchone()
        if not row:
            return "an account at another bank"
        return f"{self.name(account)} (this bank)" if row[2] else f"an account at {row[1] or row[0]}"

    # ------------------------------------------------------------ new money
    def on_credit(self, account: str, txn_id: str, at: str, amount: Decimal, payer: str) -> list[str]:
        """New money on a watched account (D-60 to D-64): an eligible account (Layer 1 or Suspect Registry, typed
        Pipe for fraud or Fast mover) runs the payment checks and, if they pass, gets a proactive hold through the
        checker and its pre-alert. Any other account: one line saying why nothing is held."""
        from pipeline import proactive

        return proactive.on_new_money(self, account, txn_id, at, amount, payer)

    def on_debit(self, account: str, txn_id: str, at: str, amount: Decimal, to_account: str, channel: str,
                 exit_identifier: str | None) -> list[str]:
        """A debit that went through (the money above the hold was free): evidence on a mule-like account."""
        mule = [w for w in self.entries(account) if w["first_pass"] == "MULE_LIKE" and at > w["added_at"]]
        if not mule:
            return []
        where = exit_identifier or self.counterparty(to_account)
        sentence = f"{at[11:16]}: {self.name(account)} moved {inr(amount)} that was not held, to {where}."
        self.event(account, mule[0]["case_id"], at, "MOVED", sentence, amount, where, channel, txn_id)
        return [sentence]

    # ------------------------------------------------------------ refused attempts
    def on_declined(self, a: dict) -> list[str]:
        account, at = a["account_id"], a["at"]
        ws = [w for w in self.entries(account) if at > w["added_at"]]
        if ws and (a["channel"] in CASH_OUT or a.get("is_exit")):
            # a watch event: rescore the account so the type behind a Strong suggestion is today's (D-57)
            from pipeline import proactive

            if any(w["first_pass"] != "CHECKED" for w in ws):
                snap = proactive.rescore(self.maker, self.bank, account, at)
                self.checker.refresh_eligibility(account, at,
                                                 proactive.decide_eligibility(self.maker, account, at, snap))
                ws = [w for w in self.entries(account) if at > w["added_at"]]
        name, when = self.name(account), at[11:16]
        case = ws[0]["case_id"] if ws else None
        mule = next((w for w in ws if w["first_pass"] == "MULE_LIKE"), None)
        out = []
        if a["channel"] in CASH_OUT or a["is_exit"]:
            where = f"at {a['exit_identifier']}" if a.get("exit_identifier") else f"by {a['channel']}"
            sentence = f"{when}: {name} tried to withdraw {inr(a['amount'])} {where}; the hold refused it."
            pid = None
            if mule:
                from pipeline import reasons

                balance = self.checker.balance_at(account, at)
                held = self.checker.held(account)
                r = reasons.structured(self.maker, mule["case_id"], account, "PROPOSE_FULL_FREEZE",
                                       {"balance": balance, "held": held})
                pid = propose(self.maker, mule["case_id"], account, "PROPOSE_FULL_FREEZE", "OFFICER", balance,
                              "watch", at, {**r, "trigger": "watch", "attempt_id": a["attempt_id"],
                                            "ladder": "STRONG", "why_strong": "a refused withdrawal on a Pipe for "
                                                                              "fraud or Fast mover goes straight "
                                                                              "to Strong"},
                              reasons.template(r))
                res = self.checker.process(None, at, [pid])
                sentence += (" A full freeze is proposed: the officer decides." if res["waiting"] else
                             f" No new freeze proposal ({res['text']}).")
            self.event(account, case, at, "CASHOUT_BLOCKED", sentence, a["amount"], a.get("exit_identifier"),
                       a["channel"], a["ref"], a["attempt_id"], pid)
            return [sentence]
        to, to_bank = a.get("to_account"), a.get("to_bank_id")
        sentence = (f"{when}: {name} tried to send {inr(a['amount'])} by {a['channel']} to "
                    f"{self.counterparty(to)}; the hold refused it.")
        self.event(account, case, at, "TRANSFER_BLOCKED", sentence, a["amount"], self.counterparty(to), a["channel"],
                   a["ref"], a["attempt_id"])
        out.append(sentence)
        if to_bank == self.bank and to:
            out += self.check_recipient(to, case, at, a)
        elif to:
            bank = self._q("SELECT bank_name FROM BANK WHERE bank_id = {t}", t=to_bank).fetchval() or "another bank"
            s2 = (f"The account it tried to pay is at {bank}: recorded in the case for the police (this bank cannot "
                  f"look at another bank's accounts).")
            self.event(account, case, at, "RECIPIENT_OTHER_BANK", s2, a["amount"], f"an account at {bank}",
                       a["channel"], a["ref"], a["attempt_id"])
            out.append(s2)
        return out

    def check_recipient(self, recipient: str, case: str | None, at: str, a: dict) -> list[str]:
        """The agent decides by itself to look at an account no ticket named: its evidence, then the watch, no hold."""
        from pipeline import steps
        from pipeline.reasons import cross_ticket

        acc = self._q("SELECT holder_type, account_type FROM ACCOUNT WHERE account_id = {a} AND bank_id = {b}",
                      a=recipient).fetchone()
        name = self.name(recipient)
        if acc and (acc[0] in BUSINESS or acc[1] in ("nodal", "pool", "escrow")):
            s = (f"{name} is a business account at this bank: noted in the case, not labelled and not watched "
                 f"(businesses receive from many payers).")
            self.event(recipient, case, at, "RECIPIENT_BUSINESS", s, a["amount"], self.name(a["account_id"]),
                       a["channel"], a["ref"], a["attempt_id"])
            return [s]
        key = f"WATCH:{recipient}"
        self._q("DELETE FROM SCORE_TARGET WHERE case_id = {k}", k=key)
        self._q("INSERT INTO SCORE_TARGET (case_id, account_id) VALUES ({k}, {a})", k=key, a=recipient)
        steps.score(self.maker, key, self.bank, at, targets=False)
        from pipeline.proactive import type_name

        pattern = self._q("SELECT account_pattern FROM ACCOUNT_SCORE WHERE case_id = {k} AND account_id = {a}", k=key,
                          a=recipient).fetchval()
        lines = cross_ticket(self.maker, recipient)
        words = f"the pattern of a {type_name(pattern)}" if pattern else "no history to read"
        evidence = f"its 90-day history shows {words}." + ((" " + " ".join(lines)) if lines else "")
        pid = propose(self.maker, case or key, recipient, "WATCH_ADD", "AUTOMATIC", None, "watch", at,
                      {"trigger": "watch", "pattern": pattern, "why": f"Named by a refused transfer from "
                                                                 f"{self.name(a['account_id'])}; " + evidence})
        res = self.checker.process(None, at, [pid])
        s = (f"The account it tried to pay, {name}, is at this bank: {evidence} "
             + ("It is on the watch now, with no hold: no victim money reached it." if res["done"] else
                f"Not added to the watch ({res['text']})."))
        self.event(recipient, case, at, "RECIPIENT_WATCHED", s, a["amount"], self.name(a["account_id"]),
                   a["channel"], a["ref"], a["attempt_id"], pid)
        return [s]

    # ------------------------------------------------------------ day 75
    def tick(self, now: str) -> list[str]:
        """Accounts on the watch for 75 days with no police direction: leave the watch, one release flag each."""
        cutoff = (datetime.strptime(now[:19], FMT) - timedelta(days=RELEASE_DAYS)).strftime(FMT)
        out = []
        for acc, case, added in self._q(
                "SELECT w.account_id, w.case_id, w.added_at FROM WATCH_LIST w WHERE w.left_at IS NULL "
                "AND w.added_at <= TO_TIMESTAMP({c}, 'YYYY-MM-DD HH24:MI:SS') "
                "AND NOT EXISTS (SELECT 1 FROM CASE_LINK l WHERE l.case_id = w.case_id "
                "AND l.link_kind = 'POLICE_CONFIRMED') ORDER BY w.added_at", c=cutoff).fetchall():
            held = self.checker.held(acc, case)
            if held <= 0:
                continue
            from pipeline import reasons

            lines = [f"The account has been on the watch since {_ts(added)[:10]} and no police direction has "
                     f"confirmed the hold."]
            r = reasons.structured(self.maker, case, acc, "PROPOSE_RELEASE", {"held": held, "lines": lines})
            pid = propose(self.maker, case, acc, "PROPOSE_RELEASE", "OFFICER", held, "watch", now,
                          {**r, "trigger": "day_75"}, reasons.template(r))
            self.checker.process(None, now, [pid])
            s = (f"{self.name(acc)}: {RELEASE_DAYS} days on the watch with no police direction; it leaves the watch "
                 f"and a release decision waits for the officer.")
            self.event(acc, case, now, "RELEASE_FLAGGED", s, held, proposal_id=pid)
            out.append(s)
        return out