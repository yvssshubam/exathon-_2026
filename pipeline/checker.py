"""The checker (Module 6, "Separation of duties"): the only component that changes anything. Deterministic code, never
a model, connected as the bank's checker database user.

For every proposal the maker (or the watch, or a recalculation) wrote to PROPOSAL, the checker:
  1. validates it: the action is on the right list (automatic or the officer's four), it is exactly what the rules
     engine produced for these facts (same account, same action, same amount), and the case's checks passed
     (075_check.sql: conservation to the paisa, holds within balance, trail and the reported amount, own accounts
     only). Anything else is REFUSED, with the reason written down
  2. carries out the automatic ones: holds up to the reported amount, the filings (exits, onward entries, KYC
     packets, send-backs), links, the split record, the watch list
  3. routes the four judgment calls (narrow, widen, an unmatched notice, a release) to the officer (WAITING)
and, once the officer decides, applies the decision (apply_decision): the officer approves, the checker acts.

There is no full-freeze action an automatic proposal can reach: a freeze is only ever placed by apply_decision,
with an officer's name on it.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta
from decimal import ROUND_DOWN, Decimal
from pathlib import Path

import yaml

from pipeline.explain import inr

ROOT = Path(__file__).resolve().parents[1]
RULES = yaml.safe_load((ROOT / "config" / "rules.yaml").read_text(encoding="utf-8"))
AUTOMATIC = tuple(RULES["automatic"])
OFFICER = tuple(RULES["officer"])
ENGINE_AUTOMATIC = ("MANDATORY_HOLD", "HOLD_MARKER", "RECORD_EXIT", "ONWARD_ENTRY")
FILES = {"MANDATORY_HOLD": "081_hold.sql", "HOLD_MARKER": "081_hold.sql", "RECORD_EXIT": "082_exits.sql",
         "ONWARD_ENTRY": "083_onward.sql", "KYC_PACKET": "084_kyc.sql"}
# Order the checker carries out a case's automatic proposals in: holds before the paperwork that cites them.
ORDER = ["LINK_DUPLICATE", "SEND_BACK", "MANDATORY_HOLD", "HOLD_MARKER", "WATCH_HOLD", "RECORD_EXIT", "ONWARD_ENTRY",
         "KYC_PACKET", "RECORD_SPLIT", "WATCH_ADD"]
CASH_OUT = tuple(RULES["full_freeze_evidence"]["watch_cash_out_channels"])
MULE_LIKE_BANDS = tuple(RULES["full_freeze_evidence"]["watch_bands"])
FMT = "%Y-%m-%d %H:%M:%S"
PROPOSAL_COLUMNS = ("proposal_id", "case_id", "account_id", "action_code", "act_mode", "amount_inr", "proposed_by",
                    "proposed_at", "reasons_json", "draft_text", "proposal_status")


class DecisionError(ValueError):
    """A decision the rules do not allow; the message says why in plain words."""


def _d(v) -> Decimal:
    return Decimal(str(v)) if v is not None else Decimal(0)


def _ts(v) -> str:
    return str(v)[:19]


def split_paise(total: int, weights: dict[str, int]) -> dict[str, int]:
    """Pro rata integer split that adds up exactly (largest remainder; ties by name)."""
    live = {k: w for k, w in weights.items() if w > 0}
    whole = sum(live.values())
    if total <= 0 or whole <= 0:
        return dict.fromkeys(weights, 0)
    out, rest = {}, []
    for k, w in live.items():
        out[k] = total * w // whole
        rest.append(((total * w) % whole, k))
    for _, k in sorted(rest, key=lambda x: (-x[0], x[1]))[: total - sum(out.values())]:
        out[k] += 1
    return {k: out.get(k, 0) for k in weights}


class Checker:
    def __init__(self, conn, bank: str):
        self.conn, self.bank = conn, bank

    # ------------------------------------------------------------ helpers
    def _q(self, sql: str, **params):
        return self.conn.execute(sql, {"b": self.bank, **params})

    def _audit(self, event: str, case_id: str | None, detail: str, now: str | None, account: str | None = None,
               actor: str = "checker") -> None:
        from pipeline.db import audit

        audit(self.conn, event, case_id, detail, actor=actor, sim_at=now, account_id=account)

    def _last(self) -> int:
        return int(self.conn.execute("SELECT CURRENT_STATEMENT").fetchval()) - 1

    def proposals(self, case_id: str | None = None, status: str = "NEW", proposal_id: int | None = None) -> list[dict]:
        where = ["proposal_status = {s}"]
        if case_id:
            where.append("case_id = {c}")
        if proposal_id:
            where = ["proposal_id = {p!d}"]
        rows = self._q(f"SELECT {', '.join(PROPOSAL_COLUMNS)} FROM PROPOSAL WHERE {' AND '.join(where)} "
                       "ORDER BY proposal_id", s=status, c=case_id, p=str(proposal_id or 0)).fetchall()
        out = []
        for r in rows:
            p = dict(zip(PROPOSAL_COLUMNS, r, strict=True))
            try:
                p["reasons"] = json.loads(p["reasons_json"] or "{}")
            except ValueError:
                p["reasons"] = {}
            out.append(p)
        return out

    def _set(self, p: dict, status: str, note: str, now: str) -> None:
        self._q("UPDATE PROPOSAL SET proposal_status = {s}, checker_note = {n}, "
                "checked_at = TO_TIMESTAMP({t}, 'YYYY-MM-DD HH24:MI:SS') WHERE proposal_id = {p!d}",
                s=status, n=note[:1000], t=now, p=str(int(p["proposal_id"])))

    def complaint(self, case_id: str) -> dict:
        from pipeline.steps import complaint

        return complaint(self.conn, case_id)

    def intake(self, case_id: str) -> dict:
        from pipeline.steps import intake_result

        return intake_result(self.conn, case_id) or {}

    def balance_at(self, account: str, ts: str) -> Decimal:
        """Balance of an own account at ts: the last snapshot plus every transaction that went through after it."""
        snap = self._q("SELECT ts, balance_inr FROM BALANCE_SNAPSHOT WHERE account_id = {a} "
                       "AND ts <= TO_TIMESTAMP({t}, 'YYYY-MM-DD HH24:MI:SS') ORDER BY ts DESC LIMIT 1",
                       a=account, t=ts).fetchone()
        start, opening = (_ts(snap[0]), _d(snap[1])) if snap else ("1900-01-01 00:00:00", Decimal(0))
        moved = self._q(
            "SELECT SUM(CASE WHEN to_account = {a} THEN amount_inr ELSE 0 END) - "
            "SUM(CASE WHEN from_account = {a} THEN amount_inr ELSE 0 END) FROM TXN_LIVE "
            "WHERE (from_account = {a} OR to_account = {a}) "
            "AND ts > TO_TIMESTAMP({s}, 'YYYY-MM-DD HH24:MI:SS') AND ts <= TO_TIMESTAMP({t}, 'YYYY-MM-DD HH24:MI:SS')",
            a=account, s=start, t=ts).fetchval()
        return opening + _d(moved)

    def held(self, account: str, case_id: str | None = None) -> Decimal:
        extra = " AND case_id = {c}" if case_id else ""
        return _d(self._q("SELECT SUM(amount_inr) FROM HOLD_LEDGER WHERE account_id = {a} AND hold_kind = 'LIEN' "
                          f"AND hold_status = 'ACTIVE'{extra}", a=account, c=case_id).fetchval())

    def _engine_row(self, p: dict, mode: str) -> tuple | None:
        return self._q("SELECT amount_inr, action_status FROM CASE_ACTION WHERE case_id = {c} "
                       "AND viewing_bank_id = {b} "
                       "AND account_id = {a} AND action_code = {k} AND act_mode = {m}",
                       c=p["case_id"], a=p["account_id"], k=p["action_code"], m=mode).fetchone()

    def _holder_message(self, case_id: str, account: str, amount, kind: str, hold_id, now: str) -> None:
        from pipeline.reasons import holder_message

        if kind == "MARKER":
            return
        text = holder_message(self.complaint(case_id), account, amount, kind, now)
        self._q("INSERT INTO HOLDER_MESSAGE (case_id, account_id, hold_id, message_text, written_at, stmt_id) "
                "VALUES ({c}, {a}, {h}, {t}, TO_TIMESTAMP({n}, 'YYYY-MM-DD HH24:MI:SS'), CURRENT_STATEMENT)",
                c=case_id, a=account, h=None if hold_id is None else str(int(hold_id)), t=text, n=now)

    # ------------------------------------------------------------ validation
    def validate(self, p: dict, checks_ok: bool | None) -> str | None:
        """Why the proposal cannot be carried out or routed, or None when it is valid."""
        code, mode = p["action_code"], p["act_mode"]
        if mode == "AUTOMATIC" and code not in AUTOMATIC:
            return f"{code} is not an automatic action (config/rules.yaml)"
        if mode == "OFFICER" and code not in OFFICER:
            return f"{code} is not one of the officer's four decisions"
        if mode not in ("AUTOMATIC", "OFFICER"):
            return f"unknown mode {mode}"
        acc, case = p["account_id"], p["case_id"]
        own = self._q("SELECT account_type FROM ACCOUNT WHERE account_id = {a} AND bank_id = {b}", a=acc).fetchone()
        if code in ENGINE_AUTOMATIC:
            row = self._engine_row(p, "AUTOMATIC")
            if row is None:
                return "the rules engine did not produce this action for these facts"
            if _d(row[0]) != _d(p["amount_inr"]):
                return f"amount {inr(p['amount_inr'])} differs from the rules engine's {inr(row[0])}"
            if checks_ok is False:
                return "the case's checks failed (CHECK_RESULT)"
            return None
        if code == "PROPOSE_NARROW":
            row = self._engine_row(p, "OFFICER")
            if row is None:
                return "the rules engine did not propose narrowing this account"
            return None if _d(row[0]) == _d(p["amount_inr"]) else "amount differs from the rules engine's"
        if code == "PROPOSE_FULL_FREEZE":
            if own is None:
                return "not this bank's account"
            if own[0] in ("nodal", "pool", "escrow"):
                return "never a full freeze on a nodal, pool or escrow account"
            open_ = self._q("SELECT COUNT(*) FROM PROPOSAL WHERE account_id = {a} "
                            "AND action_code = 'PROPOSE_FULL_FREEZE' "
                            "AND proposal_status = 'WAITING'", a=acc).fetchval()
            if int(open_ or 0):
                return "a full-freeze proposal on this account is already waiting for the officer"
            if p["reasons"].get("trigger") == "watch":
                mule = self._q("SELECT COUNT(*) FROM WATCH_LIST WHERE account_id = {a} AND first_pass = 'MULE_LIKE' "
                               "AND left_at IS NULL", a=acc).fetchval()
                cash = self._q("SELECT COUNT(*) FROM DECLINED_ATTEMPT WHERE account_id = {a} AND channel IN ({ch})",
                               a=acc, ch=list(CASH_OUT)).fetchval()
                if not int(mule or 0) or not int(cash or 0):
                    return "the watch rule needs a mule-like watched account and a refused cash-out"
                return None
            return None if self._engine_row(p, "OFFICER") else "the rules engine did not propose this freeze"
        if code == "UNMATCHED_NOTICE":
            k = self.intake(case)
            return None if k.get("match_status") == "NEAR_MATCH" and k.get("near_txn_id") else "no near match"
        if code == "SEND_BACK":
            k = self.intake(case)
            return None if k.get("match_status") not in ("EXACT", "ACCEPTED", None) else \
                "the ticket matches a transaction: nothing to send back"
        if code == "LINK_DUPLICATE":
            return None if self.intake(case).get("related") == "DUPLICATE" else "not a duplicate"
        if code == "KYC_PACKET":
            n = self._q("SELECT COUNT(*) FROM HOLD_LEDGER WHERE case_id = {c} AND account_id = {a} "
                        "AND placed_by = 'agent' AND hold_status = 'ACTIVE'", c=case, a=acc).fetchval()
            return None if int(n or 0) else "no hold on this account for the case"
        if code in ("RECORD_SPLIT", "WATCH_ADD"):
            return None if own is not None else "not this bank's account"
        if code == "WATCH_HOLD":
            return self._validate_watch_hold(p, own)
        if code == "PROPOSE_RELEASE":
            w = self._q("SELECT added_at FROM WATCH_LIST WHERE account_id = {a} AND case_id = {c} AND left_at IS NULL",
                        a=acc, c=case).fetchone()
            if not w:
                return "the account is not on the watch for this case"
            days = (datetime.strptime(_ts(p["proposed_at"]), FMT) - datetime.strptime(_ts(w[0]), FMT)).days
            return None if days >= RULES["watch"]["release_review_days"] else f"only {days} days on the watch"
        return f"no rule for {code}"

    def _validate_watch_hold(self, p: dict, own) -> str | None:
        acc, case, r = p["account_id"], p["case_id"], p["reasons"]
        if own is None:
            return "not this bank's account"
        w = self._q("SELECT first_pass, reported_inr, added_at FROM WATCH_LIST WHERE account_id = {a} "
                    "AND case_id = {c} "
                    "AND left_at IS NULL", a=acc, c=case).fetchone()
        if not w or w[0] != "MULE_LIKE":
            return "new money is held only on a mule-like watched account"
        credit = self._q("SELECT amount_inr, ts FROM TXN_LIVE WHERE txn_id = {t} AND to_account = {a}",
                         t=r.get("source_txn_id"), a=acc).fetchone()
        if credit is None or _ts(credit[1]) <= _ts(w[2]):
            return "no new credit on the account after it was put on the watch"
        room = _d(w[1]) - self.held(acc, case)
        amount = _d(p["amount_inr"])
        if amount <= 0 or amount > room or amount > _d(credit[0]):
            return f"{inr(amount)} is above what the notice still covers ({inr(room)}) or the credit"
        return None

    # ------------------------------------------------------------ carrying out
    def _carry_out(self, p: dict, now: str) -> str:
        """Do one validated automatic proposal. Returns what was done, in a few words."""
        from pipeline import steps

        code, case, acc, pid = p["action_code"], p["case_id"], p["account_id"], int(p["proposal_id"])
        if code == "MANDATORY_HOLD":
            linked = self._link_not_linked(p, now)
            if linked:
                return linked
        if code in FILES:
            steps.execute(self.conn, FILES[code], case, self.bank, now, acc, pid)
            if code in ("MANDATORY_HOLD", "HOLD_MARKER"):
                h = self._q("SELECT hold_id, amount_inr, hold_kind FROM HOLD_LEDGER WHERE proposal_id = {p!d}",
                            p=str(pid)).fetchone()
                if h is None:
                    return "no hold written (already held, or the checks did not pass)"
                self._holder_message(case, acc, h[1], h[2], h[0], now)
                return f"held {inr(h[1])} ({h[2].lower()}), hold PH-H{int(h[0])}"
            n = self._q("SELECT COUNT(*) FROM PORTAL_ENTRY WHERE proposal_id = {p!d}", p=str(pid)).fetchval()
            return f"filed ({int(n or 0)} entry)" if n else "nothing to file (already filed)"
        if code == "SEND_BACK":
            k = self.intake(case)
            self._q("INSERT INTO PORTAL_ENTRY (case_id, viewing_bank_id, entry_kind, account_id, utr, amount_inr, "
                    "detail, entry_status, filed_at, filed_by, proposal_id, stmt_id) "
                    "SELECT {c}, {b}, 'SEND_BACK', {a}, "
                    "c.utr, c.amount_inr, {d}, 'FILED', TO_TIMESTAMP({t}, 'YYYY-MM-DD HH24:MI:SS'), 'agent', {p!d}, "
                    "CURRENT_STATEMENT FROM COMPLAINT c WHERE c.case_id = {c}",
                    c=case, a=acc, d=(p["draft_text"] or "")[:4000], t=now, p=str(pid))
            return f"sent back ({(k.get('match_status') or '').lower().replace('_', ' ')})"
        if code == "LINK_DUPLICATE":
            return self._link_duplicate(p, now)
        if code == "RECORD_SPLIT":
            return self.record_split(acc, now, p["reasons"].get("trigger") or "first_pass", case,
                                     p["reasons"].get("reason"))
        if code == "WATCH_ADD":
            return self._watch_add(p, now)
        if code == "WATCH_HOLD":
            self._q("INSERT INTO HOLD_LEDGER (case_id, viewing_bank_id, account_id, hold_kind, amount_inr, hold_tag, "
                    "source_txn_id, placed_at, placed_by, hold_status, action_code, reason_text, proposal_id, stmt_id) "
                    "VALUES ({c}, {b}, {a}, 'LIEN', {amt!d}, 'NOT_LINKED', {src}, "
                    "TO_TIMESTAMP({t}, 'YYYY-MM-DD HH24:MI:SS'), 'agent', 'ACTIVE', 'WATCH_HOLD', {why}, {p!d}, "
                    "CURRENT_STATEMENT)", c=case, a=acc, amt=str(p["amount_inr"]),
                    src=p["reasons"].get("source_txn_id"), t=now, p=str(pid),
                    why="New money on a mule-like watched account, held under the existing notice; not linked to a "
                        "reported victim yet")
            hid = self._q("SELECT MAX(hold_id) FROM HOLD_LEDGER WHERE proposal_id = {p!d}", p=str(pid)).fetchval()
            self._holder_message(case, acc, p["amount_inr"], "LIEN", hid, now)
            return f"held {inr(p['amount_inr'])} of new money, tagged not linked to a reported victim yet"
        return "nothing to do"

    def _link_not_linked(self, p: dict, now: str) -> str | None:
        """A ticket for money the watch already caught: link the caught money to this victim instead of holding it
        twice. The total held on the account stays the same."""
        case, acc = p["case_id"], p["account_id"]
        matched = self.intake(case).get("matched_txn_id")
        if not matched:
            return None
        rows = self._q("SELECT hold_id, amount_inr, case_id FROM HOLD_LEDGER WHERE account_id = {a} AND hold_status = "
                       "'ACTIVE' AND hold_tag = 'NOT_LINKED' AND source_txn_id = {t} ORDER BY hold_id",
                       a=acc, t=matched).fetchall()
        if not rows:
            return None
        victim = self.complaint(case)["victim_tag"]
        need, linked = _d(p["amount_inr"]), Decimal(0)
        for hold_id, amount, old_case in rows:
            take = min(_d(amount), need - linked)
            if take <= 0:
                break
            self._q("INSERT INTO HOLD_LEDGER (case_id, viewing_bank_id, account_id, hold_kind, amount_inr, hold_tag, "
                    "source_txn_id, placed_at, placed_by, hold_status, action_code, reason_text, proposal_id, stmt_id) "
                    "VALUES ({c}, {b}, {a}, 'LIEN', {amt!d}, {v}, {t}, TO_TIMESTAMP({n}, 'YYYY-MM-DD HH24:MI:SS'), "
                    "'agent', 'ACTIVE', 'LINKED_HOLD', {why}, {p!d}, CURRENT_STATEMENT)",
                    c=case, a=acc, amt=str(take), v=victim, t=matched, n=now, p=str(int(p["proposal_id"])),
                    why=f"Money caught at {old_case}'s watch (hold PH-H{int(hold_id)}) linked to this ticket's victim")
            new_id = self._q("SELECT MAX(hold_id) FROM HOLD_LEDGER WHERE account_id = {a} AND case_id = {c}",
                             a=acc, c=case).fetchval()
            self._q("UPDATE HOLD_LEDGER SET hold_status = 'REPLACED', replaced_by = {n!d} WHERE hold_id = {h!d}",
                    n=str(int(new_id)), h=str(int(hold_id)))
            if _d(amount) > take:      # the rest stays caught, still not linked
                self._q("INSERT INTO HOLD_LEDGER (case_id, viewing_bank_id, account_id, hold_kind, amount_inr, "
                        "hold_tag, source_txn_id, placed_at, placed_by, hold_status, action_code, reason_text, "
                        "stmt_id) "
                        "VALUES ({c}, {b}, {a}, 'LIEN', {amt!d}, 'NOT_LINKED', {t}, "
                        "TO_TIMESTAMP({n}, 'YYYY-MM-DD HH24:MI:SS'), 'agent', 'ACTIVE', 'WATCH_HOLD', {why}, "
                        "CURRENT_STATEMENT)", c=old_case, a=acc, amt=str(_d(amount) - take), t=matched, n=now,
                        why="Rest of a caught credit, still not linked to a reported victim")
            linked += take
            self._audit("hold_linked", case, f"hold PH-H{int(hold_id)} ({inr(take)}) linked to {victim}", now, acc)
            self._q("INSERT INTO WATCH_EVENT (viewing_bank_id, account_id, case_id, event_at, event_kind, amount_inr, "
                    "txn_ref, hold_id, proposal_id, sentence, stmt_id) VALUES ({b}, {a}, {c}, "
                    "TO_TIMESTAMP({n}, 'YYYY-MM-DD HH24:MI:SS'), 'LINKED_TO_VICTIM', {amt!d}, {t}, {h!d}, {p!d}, {s}, "
                    "CURRENT_STATEMENT)", a=acc, c=case, n=now, amt=str(take), t=matched, h=str(int(new_id)),
                    p=str(int(p["proposal_id"])),
                    s=f"{now[11:16]}: a new complaint names the payment the watch had caught: {inr(take)} already "
                      f"held is now linked to that victim. The total held is unchanged.")
        rest = need - linked
        if rest > 0:
            self._q("INSERT INTO HOLD_LEDGER (case_id, viewing_bank_id, account_id, hold_kind, amount_inr, hold_tag, "
                    "placed_at, placed_by, hold_status, action_code, reason_text, proposal_id, stmt_id) VALUES "
                    "({c}, {b}, {a}, 'LIEN', {amt!d}, {v}, TO_TIMESTAMP({n}, 'YYYY-MM-DD HH24:MI:SS'), 'agent', "
                    "'ACTIVE', 'MANDATORY_HOLD', 'MONEY_STILL_THERE', {p!d}, CURRENT_STATEMENT)",
                    c=case, a=acc, amt=str(rest), v=victim, n=now, p=str(int(p["proposal_id"])))
        self._q("UPDATE CASE_ACTION SET action_status = 'DONE' WHERE case_id = {c} AND viewing_bank_id = {b} "
                "AND account_id = {a} AND action_code = 'MANDATORY_HOLD'", c=case, a=acc)
        self._holder_message(case, acc, need, "LIEN", None, now)
        return f"linked {inr(linked)} already held (not linked to a reported victim yet) to this victim" + \
               (f"; held {inr(rest)} more" if rest > 0 else "; the total held is unchanged")

    def _link_duplicate(self, p: dict, now: str) -> str:
        case = p["case_id"]
        k, c = self.intake(case), self.complaint(case)
        other = k.get("related_case_id")
        self._q("INSERT INTO CASE_LINK (case_id, linked_case_id, link_kind, detail, linked_at, stmt_id) VALUES "
                "({c}, {o}, 'DUPLICATE', {d}, TO_TIMESTAMP({t}, 'YYYY-MM-DD HH24:MI:SS'), CURRENT_STATEMENT)",
                c=case, o=other, d="Same transaction reached the bank twice: one case, one hold", t=now)
        out = f"linked to {other}; no second hold"
        if c.get("fir_reference"):
            self._q("INSERT INTO CASE_LINK (case_id, linked_case_id, link_kind, detail, linked_at, stmt_id) VALUES "
                    "({o}, {c}, 'POLICE_CONFIRMED', {d}, TO_TIMESTAMP({t}, 'YYYY-MM-DD HH24:MI:SS'), "
                    "CURRENT_STATEMENT)", o=other, c=case,
                    d=f"Notice after {c['fir_reference']}: the police are pursuing the case, the hold is confirmed",
                    t=now)
            n = self._q("SELECT COUNT(*) FROM WATCH_LIST WHERE case_id = {o} AND left_at IS NULL", o=other).fetchval()
            self._q("UPDATE WATCH_LIST SET left_at = TO_TIMESTAMP({t}, 'YYYY-MM-DD HH24:MI:SS'), "
                    "left_why = 'POLICE_CONFIRMED' WHERE case_id = {o} AND left_at IS NULL", t=now, o=other)
            out += f"; the police confirmed the hold ({c['fir_reference']}), {int(n or 0)} accounts leave the watch"
        return out

    def _watch_add(self, p: dict, now: str) -> str:
        case, acc, r = p["case_id"], p["account_id"], p["reasons"]
        if r.get("trigger") == "watch":
            first = "CHECKED"
        else:
            first = "MULE_LIKE" if r.get("band") in MULE_LIKE_BANDS else "HONEST"
            if self._q("SELECT COUNT(*) FROM HOLD_LEDGER WHERE account_id = {a} AND case_id = {c} "
                       "AND hold_kind = 'MARKER' AND hold_status = 'ACTIVE'", a=acc, c=case).fetchval():
                first = "MULE_LIKE"       # a first-layer marker catches later credits up to the trail amount
        exists = self._q("SELECT COUNT(*) FROM WATCH_LIST WHERE account_id = {a} AND case_id = {c} AND left_at IS NULL",
                         a=acc, c=case).fetchval()
        if int(exists or 0):
            return "already on the watch"
        reported = self._q("SELECT amount_inr FROM COMPLAINT WHERE case_id = {c}", c=case).fetchval()
        why = r.get("why") or {"MULE_LIKE": "Mule-like at the first pass: new money is held up to the reported amount",
                               "HONEST": "Honest receiver: new money is the holder's own and is not held",
                               "CHECKED": "Named by a refused transfer; no victim money reached it, so nothing is held"
                               }[first]
        self._q("INSERT INTO WATCH_LIST (account_id, case_id, first_pass, reported_inr, added_at, added_why, stmt_id) "
                "VALUES ({a}, {c}, {f}, {r}, TO_TIMESTAMP({t}, 'YYYY-MM-DD HH24:MI:SS'), {w}, CURRENT_STATEMENT)",
                a=acc, c=case, f=first, r=None if reported is None else str(reported), t=now, w=why)
        return f"on the watch ({first.lower().replace('_', '-')})"

    def record_split(self, account: str, now: str, trigger: str, trigger_case: str | None = None,
                     reason: str | None = None) -> str:
        """Record how the money held on an account splits between the victims whose money is in it (pro rata of what
        is held, by each victim's LIBR money). Moves no money. Writes a new version only when something changed."""
        # The latest attribution that covered the account knows every victim whose ticket had arrived by then.
        latest = self._q("SELECT case_id FROM ATTRIBUTION WHERE account_id = {a} AND trace_rule = 'LIBR' "
                         "ORDER BY as_of DESC, stmt_id DESC LIMIT 1", a=account).fetchval()
        rows = self._q("SELECT r.component, SUM(r.paise), MAX(r.stmt_id) FROM ATTRIBUTION_RAW r "
                       "WHERE r.case_id = {c} AND r.trace_rule = 'LIBR' AND r.row_kind = 'HOLD' AND r.ref_id = {a} "
                       "AND r.component <> 'clean' GROUP BY r.component", a=account, c=latest).fetchall()
        victims = {tag: int(paise) for tag, paise, _ in rows if int(paise) > 0}
        stmt = max((int(s) for _, _, s in rows if s is not None), default=None)
        if not victims:
            return "no reported victim's money in the account"
        held_p = int(self.held(account) * 100)
        shares = split_paise(held_p, victims)
        all_p = sum(victims.values())
        cases = dict(self._q("SELECT victim_tag, MIN(case_id) FROM ROOT_PAYMENT GROUP BY victim_tag").fetchall())
        current = {t: (int(_d(v) * 100), int(_d(h) * 100)) for t, v, h in self._q(
            "SELECT victim_tag, victim_inr, held_inr FROM VICTIM_SPLIT WHERE account_id = {a} "
            "AND superseded_at IS NULL",
            a=account).fetchall()}
        if current and set(current) == set(victims) and all(
                current[t] == (victims[t], held_p) for t in victims):
            return "split unchanged"
        version = int(self._q("SELECT COALESCE(MAX(version_no), 0) FROM VICTIM_SPLIT WHERE account_id = {a}",
                              a=account).fetchval() or 0) + 1
        self._q("UPDATE VICTIM_SPLIT SET superseded_at = TO_TIMESTAMP({t}, 'YYYY-MM-DD HH24:MI:SS') "
                "WHERE account_id = {a} AND superseded_at IS NULL", t=now, a=account)
        why = reason or {"first_pass": "First pass", "new_victim": "Another victim's ticket named this account",
                         "corrected_ticket": "A corrected ticket arrived",
                         "new_money": "New money arrived on the account"}.get(trigger, trigger)
        for tag, paise in sorted(victims.items()):
            self._q("INSERT INTO VICTIM_SPLIT (account_id, victim_tag, case_id, victim_inr, all_victims_inr, held_inr, "
                    "share_of_held_inr, version_no, valid_from, trigger_kind, trigger_case_id, reason_text, stmt_id) "
                    "VALUES ({a}, {v}, {c}, {vi!d}, {al!d}, {h!d}, {s!d}, {n!d}, "
                    "TO_TIMESTAMP({t}, 'YYYY-MM-DD HH24:MI:SS'), {k}, {tc}, {why}, {st})",
                    a=account, v=tag, c=cases.get(tag, trigger_case or "-"), vi=str(Decimal(paise) / 100),
                    al=str(Decimal(all_p) / 100), h=str(Decimal(held_p) / 100), s=str(Decimal(shares[tag]) / 100),
                    n=str(version), t=now, k=trigger, tc=trigger_case, why=why,
                    st=None if stmt is None else str(stmt))
        listed = ", ".join(f"{t} {inr(Decimal(shares[t]) / 100)}" for t in sorted(victims))
        return f"split v{version} of {inr(Decimal(held_p) / 100)} held: {listed}"

    # ------------------------------------------------------------ the main loop
    def process(self, case_id: str | None, now: str, proposal_ids: list[int] | None = None) -> dict:
        """Handle the NEW proposals of a case (or the given ones). Returns a summary for the run log."""
        from pipeline import steps

        todo = ([p for pid in proposal_ids for p in self.proposals(proposal_id=pid) if p["proposal_status"] == "NEW"]
                if proposal_ids else self.proposals(case_id))
        if not todo:
            return {"text": "nothing to check", "done": 0, "waiting": 0, "refused": 0}
        checks_ok, failed = None, []
        cases = sorted({p["case_id"] for p in todo})
        if any(p["action_code"] in ENGINE_AUTOMATIC for p in todo):
            for case in cases:
                if any(p["case_id"] == case and p["action_code"] in ENGINE_AUTOMATIC for p in todo):
                    results = steps.check(self.conn, case, self.bank)
                    failed += [f"{r['check_name']}: {r['detail']}" for r in results if not r["check_passed"]]
            checks_ok = not failed
        done = waiting = refused = 0
        notes = []
        rank = {code: i for i, code in enumerate(ORDER)}
        for p in sorted(todo, key=lambda p: (p["act_mode"] != "AUTOMATIC", rank.get(p["action_code"], 99),
                                             p["proposal_id"])):
            why = self.validate(p, checks_ok)
            if why:
                self._set(p, "REFUSED", why, now)
                self._audit("refused", p["case_id"], f"{p['action_code']} on {p['account_id']}: {why}", now,
                            p["account_id"])
                refused += 1
                notes.append(f"refused {p['action_code']} on {p['account_id']} ({why})")
                continue
            if p["act_mode"] == "OFFICER":
                self._set(p, "WAITING", "valid; waiting for the officer", now)
                if p["action_code"] == "PROPOSE_RELEASE":      # idea 3's exit: one flag, and the watch ends
                    self._q("UPDATE WATCH_LIST SET left_at = TO_TIMESTAMP({t}, 'YYYY-MM-DD HH24:MI:SS'), "
                            "left_why = 'DAY_75' WHERE account_id = {a} AND case_id = {c} AND left_at IS NULL",
                            t=now, a=p["account_id"], c=p["case_id"])
                self._audit("to_officer", p["case_id"], f"{p['action_code']} on {p['account_id']}", now,
                            p["account_id"])
                waiting += 1
                continue
            what = self._carry_out(p, now)
            self._set(p, "DONE", what, now)
            self._audit(p["action_code"].lower(), p["case_id"], f"{p['account_id']}: {what}", now, p["account_id"])
            done += 1
        for case in cases:
            self._finish_ticket(case, now, bool(failed))
        status = None
        if case_id:
            status = self._q("SELECT ticket_status FROM TICKET WHERE case_id = {c}", c=case_id).fetchval()
        text = f"checker: {done} carried out, {waiting} to the officer, {refused} refused"
        if failed:
            text += "; checks failed: " + "; ".join(failed)
        if notes:
            text += "; " + "; ".join(notes[:3])
        return {"text": text[:2000], "done": done, "waiting": waiting, "refused": refused, "checks_failed": failed,
                "ticket_status": status}

    def _finish_ticket(self, case_id: str, now: str, checks_failed: bool) -> None:
        kinds = {k for (k,) in self._q("SELECT DISTINCT action_code FROM PROPOSAL WHERE case_id = {c} "
                                       "AND proposal_status IN ('DONE', 'WAITING') AND run_id = "
                                       "(SELECT MAX(run_id) FROM AGENT_RUN WHERE case_id = {c})", c=case_id).fetchall()}
        if "LINK_DUPLICATE" in kinds:
            status, run_status = "LINKED", "LINKED"
        elif "SEND_BACK" in kinds:
            status, run_status = "SENT_BACK", "SENT_BACK"
        else:
            status, run_status = "DONE", "CHECK_FAILED" if checks_failed else "DONE"
        self._q("UPDATE TICKET SET ticket_status = {s} WHERE case_id = {c} AND ticket_status = 'WORKING'",
                s=status, c=case_id)
        first = self._q("SELECT MIN(placed_at) FROM HOLD_LEDGER WHERE case_id = {c} AND placed_by = 'agent'",
                        c=case_id).fetchval()
        self._q("UPDATE AGENT_RUN SET run_status = {s}, sim_holds_at = TO_TIMESTAMP({h}, 'YYYY-MM-DD HH24:MI:SS') "
                "WHERE run_id = (SELECT MAX(run_id) FROM AGENT_RUN WHERE case_id = {c}) AND run_status = 'PROPOSED'",
                s=run_status, h=None if first is None else _ts(first), c=case_id)

    # ------------------------------------------------------------ the officer's decisions
    def apply_decision(self, proposal_id: int, outcome: str, officer: str, now: str, comment: str | None = None,
                       amount=None, signed_text: str | None = None) -> int:
        """The officer approves or rejects one of the four decisions; the checker carries it out."""
        officer, comment = (officer or "").strip(), (comment or "").strip() or None
        if not officer:
            raise DecisionError("An officer name is required: nothing is decided anonymously.")
        found = self.proposals(status="WAITING", proposal_id=proposal_id)
        if not found:
            raise DecisionError(f"No proposal {proposal_id} waiting for a decision (already decided, or never valid).")
        p = found[0]
        if p["proposal_status"] != "WAITING":
            raise DecisionError(f"This proposal is {p['proposal_status'].lower()}, not waiting for a decision.")
        code, case, acc = p["action_code"], p["case_id"], p["account_id"]
        if outcome == "REJECTED":
            if not comment:
                raise DecisionError("A rejection needs a written reason.")
            decision = self._decision(p, "REJECTED", None, officer, comment, signed_text, now)
            self._set(p, "REJECTED", f"rejected by {officer}: {comment}", now)
            self._audit("officer_rejected", case, f"{code} on {acc}, decision {decision}", now, acc, actor=officer)
            return decision
        proposed = _d(p["amount_inr"])
        final = proposed
        if amount is not None:
            if code != "PROPOSE_NARROW":
                raise DecisionError("Only a narrowing amount can be edited.")
            if not comment:
                raise DecisionError("Write a reason when you change the proposed amount.")
            final = _d(amount)
        outcome = "EDITED" if final != proposed else "APPROVED"
        if code == "PROPOSE_NARROW":
            liens = self._q("SELECT hold_id, amount_inr FROM HOLD_LEDGER WHERE case_id = {c} AND account_id = {a} "
                            "AND hold_kind = 'LIEN' AND hold_status = 'ACTIVE' ORDER BY hold_id", c=case,
                            a=acc).fetchall()
            if not liens:
                raise DecisionError("There is no active hold on this account to narrow.")
            held = sum((_d(a) for _, a in liens), Decimal(0))
            floor = _d(self._q("SELECT victim_amount_inr + other_victims_inr FROM CASE_ACTION WHERE case_id = {c} "
                               "AND account_id = {a} AND action_code = 'PROPOSE_NARROW'", c=case, a=acc).fetchval())
            if not (floor <= final < held):
                raise DecisionError(f"A narrowed hold must be at least the victims' money ({inr(floor)}) and less "
                                    f"than the current hold ({inr(held)}).")
        decision = self._decision(p, outcome, final, officer, comment, signed_text, now)
        if code == "PROPOSE_NARROW":
            new_id = self._officer_hold(case, acc, "LIEN", final, officer, code, comment or "Narrowed to the victims' "
                                        "money", now, int(p["proposal_id"]))
            for hold_id, _ in liens:
                self._q("UPDATE HOLD_LEDGER SET hold_status = 'REPLACED', replaced_by = {n!d} WHERE hold_id = {h!d}",
                        n=str(new_id), h=str(int(hold_id)))
            self._holder_message(case, acc, final, "LIEN", new_id, now)
            self._q("UPDATE WATCH_LIST SET first_pass = 'HONEST' WHERE account_id = {a} AND case_id = {c} "
                    "AND left_at IS NULL", a=acc, c=case)
        elif code == "PROPOSE_FULL_FREEZE":
            balance = self.balance_at(acc, now)
            new_id = self._officer_hold(case, acc, "ACCOUNT_FREEZE", balance, officer, code,
                                        comment or "Whole-account freeze approved on the written reasons", now,
                                        int(p["proposal_id"]))
            self._holder_message(case, acc, balance, "ACCOUNT_FREEZE", new_id, now)
        elif code == "UNMATCHED_NOTICE":
            self._q("UPDATE TICKET SET ticket_status = 'QUEUED' WHERE case_id = {c}", c=case)
        elif code == "PROPOSE_RELEASE":
            self._q("UPDATE HOLD_LEDGER SET hold_status = 'RELEASED' WHERE case_id = {c} AND account_id = {a} "
                    "AND hold_status = 'ACTIVE'", c=case, a=acc)
            self._q("UPDATE WATCH_LIST SET left_at = TO_TIMESTAMP({t}, 'YYYY-MM-DD HH24:MI:SS'), left_why = 'DAY_75' "
                    "WHERE account_id = {a} AND case_id = {c} AND left_at IS NULL", t=now, a=acc, c=case)
            self._holder_message(case, acc, 0, "RELEASED", None, now)
        self._set(p, outcome, f"{outcome.lower()} by {officer}", now)
        self._audit(f"officer_{outcome.lower()}", case, f"{code} on {acc}, decision {decision}, amount {final}", now,
                    acc, actor=officer)
        return decision

    # What "undo" means for each kind of decision, after the officer has decided (each needs a written reason and
    # is its own DECISION row, so the audit shows the decision and its reversal side by side):
    #   smaller hold  -> HOLD_AGAIN: released money may already be spent, so this places a new hold on what is
    #                    still there, up to what was held before the reduction
    #   full freeze   -> UNFREEZE:   nothing left the account while it was frozen, a true reversal
    #   near match    -> RELEASE:    removes the holds the accepted match placed
    UNDO = {"PROPOSE_NARROW": "HOLD_AGAIN", "PROPOSE_FULL_FREEZE": "UNFREEZE", "UNMATCHED_NOTICE": "RELEASE"}

    def undo_decision(self, decision_id: int, officer: str, reason: str, now: str) -> int:
        officer, reason = (officer or "").strip(), (reason or "").strip()
        if not officer:
            raise DecisionError("An officer name is required: nothing is decided anonymously.")
        if not reason:
            raise DecisionError("Write a reason: an undo goes into the audit log and the hold record.")
        rows = self._q("SELECT decision_id, case_id, account_id, action_code, decision_outcome, proposal_id, "
                       "amount_inr FROM DECISION WHERE decision_id = {d!d}", d=str(int(decision_id))).fetchall()
        if not rows:
            raise DecisionError(f"No decision {decision_id}.")
        _, case, acc, code, outcome, proposal_id, _amount = rows[0]
        if code not in self.UNDO or outcome not in ("APPROVED", "EDITED"):
            raise DecisionError("Only an approved smaller hold, freeze or near match can be undone.")
        undo = self.UNDO[code]
        done = self._q("SELECT COUNT(*) FROM DECISION WHERE proposal_id = {p!d} AND action_code = {u}",
                       p=str(int(proposal_id)), u=undo).fetchval()
        if done:
            raise DecisionError("This decision has already been undone.")
        amount = None
        if undo == "HOLD_AGAIN":
            cur = self._q("SELECT hold_id, amount_inr FROM HOLD_LEDGER WHERE proposal_id = {p!d} AND account_id = {a} "
                          "AND hold_kind = 'LIEN' AND hold_status = 'ACTIVE'", p=str(int(proposal_id)),
                          a=acc).fetchall()
            if not cur:
                raise DecisionError("The reduced hold is no longer active, so there is nothing to hold again.")
            hold_id, now_held = int(cur[0][0]), _d(cur[0][1])
            before = _d(self._q("SELECT SUM(amount_inr) FROM HOLD_LEDGER WHERE replaced_by = {h!d}",
                                h=str(hold_id)).fetchval())
            others = self.held(acc) - now_held
            amount = min(before, max(self.balance_at(acc, now) - others, now_held))
            new_id = self._officer_hold(case, acc, "LIEN", amount, officer, undo, reason, now, int(proposal_id))
            self._q("UPDATE HOLD_LEDGER SET hold_status = 'REPLACED', replaced_by = {n!d} WHERE hold_id = {h!d}",
                    n=str(new_id), h=str(hold_id))
            self._holder_message(case, acc, amount, "LIEN", new_id, now)
        elif undo == "UNFREEZE":
            n = self._q("UPDATE HOLD_LEDGER SET hold_status = 'RELEASED' WHERE proposal_id = {p!d} "
                        "AND account_id = {a} "
                        "AND hold_kind = 'ACCOUNT_FREEZE' AND hold_status = 'ACTIVE'", p=str(int(proposal_id)),
                        a=acc).rowcount()
            if not n:
                raise DecisionError("The freeze is no longer active.")
            self._holder_message(case, acc, 0, "UNFROZEN", None, now)
        else:  # RELEASE: the holds the accepted near match placed
            n = self._q("UPDATE HOLD_LEDGER SET hold_status = 'RELEASED' WHERE case_id = {c} AND hold_status = "
                        "'ACTIVE'", c=case).rowcount()
            if not n:
                raise DecisionError("The accepted match holds nothing now.")
            self._holder_message(case, acc, 0, "RELEASED", None, now)
        self._q("INSERT INTO DECISION (case_id, viewing_bank_id, account_id, action_code, proposal_id, "
                "decision_outcome, amount_inr, decided_by, decided_at, reason_text) VALUES ({c}, {b}, {a}, {k}, "
                "{p!d}, 'APPROVED', {amt}, {by}, TO_TIMESTAMP({t}, 'YYYY-MM-DD HH24:MI:SS'), {r})",
                c=case, a=acc, k=undo, p=str(int(proposal_id)), amt=None if amount is None else str(amount), by=officer,
                t=now, r=reason)
        new = int(self._q("SELECT MAX(decision_id) FROM DECISION WHERE proposal_id = {p!d} AND action_code = {k}",
                          p=str(int(proposal_id)), k=undo).fetchval())
        self._audit(f"officer_{undo.lower()}", case, f"{undo} of decision {decision_id} on {acc}, decision {new}: "
                                                    f"{reason}", now, acc, actor=officer)
        return new

    def _decision(self, p: dict, outcome: str, amount, officer: str, comment: str | None, signed: str | None,
                  now: str) -> int:
        draft = p["draft_text"]
        self._q("INSERT INTO DECISION (case_id, viewing_bank_id, account_id, action_code, proposal_id, "
                "decision_outcome, amount_inr, proposed_amount, decided_by, decided_at, reason_text, draft_text, "
                "signed_text, statement_ids) VALUES ({c}, {b}, {a}, {k}, {p!d}, {o}, {amt}, {pa}, {by}, "
                "TO_TIMESTAMP({t}, 'YYYY-MM-DD HH24:MI:SS'), {r}, {d}, {s}, {ids})",
                c=p["case_id"], a=p["account_id"], k=p["action_code"], p=str(int(p["proposal_id"])), o=outcome,
                amt=None if amount is None else str(amount),
                pa=None if p["amount_inr"] is None else str(p["amount_inr"]),
                by=officer, t=now, r=comment, d=draft, s=(signed or draft) if outcome != "REJECTED" else signed,
                ids=self._statement_ids(p))
        return int(self._q("SELECT MAX(decision_id) FROM DECISION WHERE proposal_id = {p!d}",
                           p=str(int(p["proposal_id"]))).fetchval())

    def _statement_ids(self, p: dict) -> str:
        att = self._q("SELECT MAX(stmt_id) FROM ATTRIBUTION WHERE case_id = {c} AND account_id = {a}",
                      c=p["case_id"], a=p["account_id"]).fetchval()
        sc = self._q("SELECT MAX(stmt_id) FROM ACCOUNT_SCORE WHERE case_id = {c} AND account_id = {a}",
                     c=p["case_id"], a=p["account_id"]).fetchval()
        act = self._q("SELECT MAX(stmt_id) FROM CASE_ACTION WHERE case_id = {c} AND account_id = {a}",
                      c=p["case_id"], a=p["account_id"]).fetchval()
        return f"actions:{act} attribute:{att} evidence:{sc}"

    def _officer_hold(self, case: str, acc: str, kind: str, amount, officer: str, code: str, why: str, now: str,
                      proposal_id: int) -> int:
        victim = self.complaint(case)["victim_tag"]
        self._q("INSERT INTO HOLD_LEDGER (case_id, viewing_bank_id, account_id, hold_kind, amount_inr, hold_tag, "
                "placed_at, placed_by, hold_status, action_code, reason_text, proposal_id, stmt_id) VALUES "
                "({c}, {b}, {a}, {k}, {amt!d}, {v}, TO_TIMESTAMP({t}, 'YYYY-MM-DD HH24:MI:SS'), {o}, 'ACTIVE', {code}, "
                "{why}, {p!d}, CURRENT_STATEMENT)", c=case, a=acc, k=kind, amt=str(amount), v=victim, t=now, o=officer,
                code=code, why=why[:2000], p=str(proposal_id))
        return int(self._q("SELECT MAX(hold_id) FROM HOLD_LEDGER WHERE case_id = {c} AND account_id = {a}",
                           c=case, a=acc).fetchval())


def next_minute(ts: str) -> str:
    return (datetime.strptime(ts[:19], FMT) + timedelta(minutes=1)).strftime(FMT)


def rupees(paise: int) -> Decimal:
    return (Decimal(paise) / 100).quantize(Decimal("0.01"), rounding=ROUND_DOWN)