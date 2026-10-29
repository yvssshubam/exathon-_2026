"""Simulated core banking for one bank (assumption, README): applies holds to payments as they happen.

It is not part of the product: it stands in for the bank's core system and its card and UPI switch. Holds work the
way the brief describes them: a lien blocks money going out above what is free; a full freeze blocks every debit;
money coming in is never blocked. A refused payment is not a transaction (no money moved): it goes to the
declined-attempts feed (DECLINED_ATTEMPT), which is where the agent's watch reads it (pipeline/watch.py). A refused
transfer to another bank never arrives there either: that bank gets an UNDELIVERED_CREDIT row (the payment network).

Payments come from two places in the synthetic data:
  TXN              what happens if nobody acts. Once a hold exists, a debit it covers is refused here, and every
                   later step reads TXN_LIVE, which leaves the refused debits out.
  PAYMENT_REQUEST  tries a holder makes only because the money is held (the UPI transfer after a refused ATM
                   withdrawal). A request that would go through is ignored: the demo never needs one.
Runs as the admin user, on the bank's schema by name (it is the bank's own system, not the agent).
"""
from __future__ import annotations

import zlib
from dataclasses import dataclass
from decimal import Decimal

from pipeline.db import BANKS, schema_for


@dataclass
class Payment:
    kind: str               # TXN or REQUEST
    ref: str                # txn_id or request_id
    ts: str
    from_account: str
    to_account: str
    amount: Decimal
    channel: str
    is_exit: bool
    exit_identifier: str | None
    to_bank_id: str | None = None

    @property
    def key(self) -> tuple[str, str]:
        return (self.kind, self.ref)


def _d(v) -> Decimal:
    return Decimal(str(v)) if v is not None else Decimal(0)


def _ts(v) -> str:
    return str(v)[:19]


def watched_or_held(conn, bank: str) -> list[str]:
    s = schema_for(bank)
    return [a for (a,) in conn.execute(
        f"SELECT account_id FROM {s}.HOLD_LEDGER WHERE hold_status = 'ACTIVE' "
        f"UNION SELECT account_id FROM {s}.WATCH_LIST WHERE left_at IS NULL").fetchall()]


def pending(conn, bank: str, after: str, until: str) -> list[Payment]:
    """Payments in (after, until] that touch an account with an active hold or on the watch, in time order."""
    s = schema_for(bank)
    accounts = watched_or_held(conn, bank)
    if not accounts:
        return []
    rows = conn.execute(f"""
        SELECT 'TXN', t.txn_id, t.ts, t.from_account, t.to_account, t.amount_inr, t.channel, t.is_exit,
               t.exit_identifier, a.bank_id
        FROM {s}.TXN t LEFT JOIN {s}.ACCOUNT a ON a.account_id = t.to_account
        WHERE t.ts > TO_TIMESTAMP({{after}}, 'YYYY-MM-DD HH24:MI:SS')
          AND t.ts <= TO_TIMESTAMP({{until}}, 'YYYY-MM-DD HH24:MI:SS')
          AND (t.from_account IN ({{accs}}) OR t.to_account IN ({{accs}}))
        UNION ALL
        SELECT 'REQUEST', r.request_id, r.ts, r.from_account, r.to_account, r.amount_inr, r.channel,
               CASE WHEN LEFT(r.to_account, 5) = 'EXIT:' THEN TRUE ELSE FALSE END, r.exit_identifier, r.to_bank_id
        FROM {s}.PAYMENT_REQUEST r
        WHERE r.ts > TO_TIMESTAMP({{after}}, 'YYYY-MM-DD HH24:MI:SS')
          AND r.ts <= TO_TIMESTAMP({{until}}, 'YYYY-MM-DD HH24:MI:SS')
          AND r.from_account IN ({{accs}})
        ORDER BY 3, 2""", {"after": after, "until": until, "accs": accounts}).fetchall()
    return [Payment(k, ref, _ts(ts), f, to, _d(amt), ch, bool(ex), ident, tb)
            for k, ref, ts, f, to, amt, ch, ex, ident, tb in rows]


def balance_before(conn, bank: str, account: str, ts: str) -> Decimal:
    """Balance just before ts: the last snapshot plus every payment that went through before ts."""
    s = schema_for(bank)
    snap = conn.execute(f"SELECT ts, balance_inr FROM {s}.BALANCE_SNAPSHOT WHERE account_id = {{a}} "
                        "AND ts < TO_TIMESTAMP({t}, 'YYYY-MM-DD HH24:MI:SS') ORDER BY ts DESC LIMIT 1",
                        {"a": account, "t": ts}).fetchone()
    start, opening = (_ts(snap[0]), _d(snap[1])) if snap else ("1900-01-01 00:00:00", Decimal(0))
    moved = conn.execute(
        f"SELECT SUM(CASE WHEN to_account = {{a}} THEN amount_inr ELSE 0 END) - "
        f"SUM(CASE WHEN from_account = {{a}} THEN amount_inr ELSE 0 END) FROM {s}.TXN_LIVE "
        "WHERE (from_account = {a} OR to_account = {a}) AND ts >= TO_TIMESTAMP({s0}, 'YYYY-MM-DD HH24:MI:SS') "
        "AND ts < TO_TIMESTAMP({t}, 'YYYY-MM-DD HH24:MI:SS')", {"a": account, "s0": start, "t": ts}).fetchval()
    return opening + _d(moved)


def holds_on(conn, bank: str, account: str, ts: str) -> tuple[Decimal, bool]:
    """(money under active liens, frozen?) on an account, counting only holds placed before ts."""
    s = schema_for(bank)
    row = conn.execute(
        f"SELECT SUM(CASE WHEN hold_kind = 'LIEN' THEN amount_inr ELSE 0 END), "
        f"MAX(CASE WHEN hold_kind = 'ACCOUNT_FREEZE' THEN 1 ELSE 0 END) FROM {s}.HOLD_LEDGER "
        "WHERE account_id = {a} AND hold_status = 'ACTIVE' AND placed_at < TO_TIMESTAMP({t}, 'YYYY-MM-DD HH24:MI:SS')",
        {"a": account, "t": ts}).fetchone()
    return (_d(row[0]), bool(int(row[1] or 0))) if row else (Decimal(0), False)


def undelivered(conn, to_bank: str, p: Payment, refused_by: str) -> None:
    """Record at the receiving bank that a payment refused at the sending bank never arrived (once)."""
    t = schema_for(to_bank)
    if conn.execute(f"SELECT COUNT(*) FROM {t}.UNDELIVERED_CREDIT WHERE txn_ref = {{r}}", {"r": p.ref}).fetchval():
        return
    conn.execute(f"""
        INSERT INTO {t}.UNDELIVERED_CREDIT (txn_ref, from_account, to_account, amount_inr, refused_at, refused_by_bank,
                                            stmt_id)
        VALUES ({{r}}, {{f}}, {{to}}, {{amt!d}}, TO_TIMESTAMP({{ts}}, 'YYYY-MM-DD HH24:MI:SS'), {{b}},
                CURRENT_STATEMENT)""",
                 {"r": p.ref, "f": p.from_account, "to": p.to_account, "amt": str(p.amount), "ts": p.ts,
                  "b": refused_by})


def apply(conn, bank: str, p: Payment) -> dict | None:
    """Apply one debit as core banking would. Returns the declined attempt (a dict) when a hold refused it."""
    s = schema_for(bank)
    own = conn.execute(f"SELECT COUNT(*) FROM {s}.ACCOUNT WHERE account_id = {{a}} AND bank_id = {{b}}",
                       {"a": p.from_account, "b": bank}).fetchval()
    if not int(own or 0):
        return None                                 # a credit from another bank: credits are never blocked
    held, frozen = holds_on(conn, bank, p.from_account, p.ts)
    if not held and not frozen:
        return None
    free = balance_before(conn, bank, p.from_account, p.ts) - held
    if not frozen and p.amount <= free:
        return None
    reason = "FREEZE" if frozen else "LIEN"
    card = f"XXXX-{zlib.crc32(p.from_account.encode()) % 10000:04d}" if p.channel == "ATM" else None
    conn.execute(f"""
        INSERT INTO {s}.DECLINED_ATTEMPT (account_id, attempted_at, amount_inr, channel, exit_identifier, card_ref,
                                          intended_account, intended_bank_id, decline_reason, txn_ref, stmt_id)
        VALUES ({{a}}, TO_TIMESTAMP({{t}}, 'YYYY-MM-DD HH24:MI:SS'), {{amt!d}}, {{ch}}, {{ident}}, {{card}},
                {{to}}, {{tb}}, {{why}}, {{ref}}, CURRENT_STATEMENT)""",
                 {"a": p.from_account, "t": p.ts, "amt": str(p.amount), "ch": p.channel, "ident": p.exit_identifier,
                  "card": card, "to": None if p.is_exit else p.to_account, "tb": None if p.is_exit else p.to_bank_id,
                  "why": reason, "ref": p.ref})
    attempt_id = conn.execute(f"SELECT MAX(attempt_id) FROM {s}.DECLINED_ATTEMPT WHERE txn_ref = {{r}}",
                              {"r": p.ref}).fetchval()
    if p.kind == "TXN" and not p.is_exit and p.to_bank_id and p.to_bank_id != bank and p.to_bank_id in BANKS:
        undelivered(conn, p.to_bank_id, p, bank)    # the payment network: it never reaches the other bank either
    return {"attempt_id": int(attempt_id), "account_id": p.from_account, "at": p.ts, "amount": p.amount,
            "channel": p.channel, "exit_identifier": p.exit_identifier,
            "to_account": None if p.is_exit else p.to_account,
            "to_bank_id": None if p.is_exit else p.to_bank_id, "reason": reason, "ref": p.ref, "is_exit": p.is_exit}