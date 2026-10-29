"""A likely unreported victim (DECISIONS D-53). When new money reaches an account the bank already holds as mule-like,
and the payer has not paid it before, the payer may be a victim who has not reported yet. The watch holds the money
under the existing notice as it always has (D-47); this module prepares a pre-alert for the officer to send:

  * the payment (who paid, when, how much, the UTR) and that no complaint names it yet
  * where the account is on hold: every active hold, the complaint and notice behind it, since when, and the new
    money held under the same notice, not linked to a reported victim yet
  * why the payer may be a victim, and the case against: a new payer can be a genuine customer or part of the scheme

The officer sends it with one click (it is filed with CFCFRMS for the police and the payer's bank) or keeps it. If
the payer's own complaint arrives first, the pre-alert is closed unsent and the minutes between the flag and the
complaint are recorded. Nothing is ever held on a pre-alert, and it never says the payer did anything wrong.
"""
from __future__ import annotations

from datetime import datetime, timedelta
from decimal import Decimal

import yaml

from pipeline.db import ROOT
from pipeline.explain import inr

RULES = yaml.safe_load((ROOT / "config" / "rules.yaml").read_text(encoding="utf-8"))
MIN_INR = Decimal(str(RULES["watch"]["pre_alert_min_inr"]))
NEW_DAYS = int(RULES["watch"]["pre_alert_new_sender_days"])
CITATION = RULES["legal_basis"]["pre_alert"]
FMT = "%Y-%m-%d %H:%M:%S"
MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")


def _d(v) -> Decimal:
    return Decimal(str(v)) if v is not None else Decimal(0)


def _ts(v) -> str:
    return str(v)[:19]


def when(ts: str) -> str:
    """'2026-10-18 05:45:00' -> '05:45 on 18 Oct 2026'."""
    t = _ts(ts)
    return f"{t[11:16]} on {int(t[8:10])} {MONTHS[int(t[5:7]) - 1]} {t[:4]}"


def holds_on(conn, account: str) -> list[dict]:
    """Where the account is on hold now: one row per complaint (case), newest money last. Readable by the maker and
    the checker alike."""
    rows = conn.execute(
        "SELECT h.case_id, SUM(CASE WHEN h.hold_kind = 'LIEN' THEN h.amount_inr ELSE 0 END), "
        "MAX(CASE WHEN h.hold_kind = 'ACCOUNT_FREEZE' THEN 1 ELSE 0 END), MIN(h.placed_at), "
        "MAX(CASE WHEN h.hold_tag = 'NOT_LINKED' THEN 1 ELSE 0 END), "
        "MAX(CASE WHEN h.hold_tag = 'NOT_LINKED' THEN h.source_txn_id END), "
        "c.ordering_authority, c.ordering_reference, c.notice_legal_basis, c.ack_no, c.victim_tag "
        "FROM HOLD_LEDGER h LEFT JOIN COMPLAINT c ON c.case_id = h.case_id "
        "WHERE h.account_id = {a} AND h.hold_status = 'ACTIVE' AND h.hold_kind IN ('LIEN', 'ACCOUNT_FREEZE') "
        "GROUP BY h.case_id, CASE WHEN h.hold_tag = 'NOT_LINKED' THEN h.source_txn_id ELSE '-' END, "
        "c.ordering_authority, c.ordering_reference, c.notice_legal_basis, c.ack_no, c.victim_tag "
        "ORDER BY MIN(h.placed_at), h.case_id",
        {"a": account}).fetchall()
    out = []
    for case, liens, frozen, since, not_linked, source, authority, ref, legal, ack, victim in rows:
        out.append({"case_id": case, "amount_inr": str(_d(liens)), "frozen": bool(frozen), "since": _ts(since),
                    "not_linked": bool(not_linked), "source_txn_id": source if not_linked else None,
                    "authority": authority, "reference": ref, "legal_basis": legal, "ack_no": ack,
                    "victim_tag": None if not_linked else victim})
    return out


def is_new_sender(conn, account: str, payer: str | None, txn_id: str, at: str, amount) -> str | None:
    """Why the payer is not a likely unreported victim, or None when they may be one."""
    if not payer or payer.startswith("EXIT:"):
        return "no payer account"
    if _d(amount) < MIN_INR:
        return f"below {inr(MIN_INR)}"
    q = conn.execute
    if q("SELECT COUNT(*) FROM COMPLAINT c JOIN TXN_LIVE t ON t.utr = c.utr WHERE t.txn_id = {t}",
         {"t": txn_id}).fetchval():
        return "a complaint already names this payment"
    if q("SELECT COUNT(*) FROM COMPLAINT WHERE victim_account = {p}", {"p": payer}).fetchval():
        return "the payer is already a reported victim"
    # money moving inside a trail (one held account paying another) is layering, not a new victim
    for sql in ("SELECT COUNT(*) FROM HOLD_LEDGER WHERE account_id = {p} AND hold_status = 'ACTIVE'",
                "SELECT COUNT(*) FROM WATCH_LIST WHERE account_id = {p} AND left_at IS NULL",
                "SELECT COUNT(*) FROM TRACE_EDGE WHERE to_account = {p}"):
        if q(sql, {"p": payer}).fetchval():
            return "the payer is on a trail itself"
    since = (datetime.strptime(_ts(at), FMT) - timedelta(days=NEW_DAYS)).strftime(FMT)
    if q("SELECT COUNT(*) FROM TXN_LIVE WHERE from_account = {p} AND to_account = {a} AND txn_id <> {t} "
         "AND ts >= TO_TIMESTAMP({s}, 'YYYY-MM-DD HH24:MI:SS') AND ts <= TO_TIMESTAMP({n}, 'YYYY-MM-DD HH24:MI:SS')",
         {"p": payer, "a": account, "t": txn_id, "s": since, "n": _ts(at)}).fetchval():
        return f"the payer has paid this account before, within {NEW_DAYS} days"
    return None


def hold_lines(holds: list[dict], txn_id: str, bank_name: str, account: str) -> list[str]:
    out = []
    for h in holds:
        amount = inr(h["amount_inr"])
        if h["not_linked"] and h["source_txn_id"] == txn_id:
            out.append(f"- {amount} since {h['since'][11:16]}: this payment, held under the same notice, not linked "
                       f"to a reported victim yet.")
            continue
        notice = ", ".join(x for x in (h["authority"], h["reference"], h["legal_basis"]) if x) or "the police notice"
        what = "whole account frozen" if h["frozen"] else f"{amount} held"
        tail = ", not linked to a reported victim yet" if h["not_linked"] else ""
        out.append(f"- {what} since {when(h['since'])}, complaint {h['ack_no'] or h['case_id']} ({h['case_id']}), "
                   f"under {notice}{tail}.")
    return [f"Where account {account} is on hold at {bank_name}:", *out] if out else []


def draft(c: dict) -> tuple[dict, str]:
    """The structured reasons and the alert text. c: account, name, bank_name, payer, payer_bank_id, payer_where,
    txn_id, utr, channel, amount, at, holds, case_id."""
    held = sum((_d(h["amount_inr"]) for h in c["holds"]), Decimal(0))
    cases = sorted({h["case_id"] for h in c["holds"] if not (h["not_linked"] and h["source_txn_id"] == c["txn_id"])})
    support = [
        {"id": "S1", "text": f"The payer has not paid {c['name']} in the {NEW_DAYS} days before this payment."},
        {"id": "S2", "text": f"{c['name']} was already on hold for {len(cases)} reported "
                             f"{'complaint' if len(cases) == 1 else 'complaints'} when the money arrived; "
                             f"{inr(held)} is held on it now."},
        {"id": "S3", "text": "No complaint names this payment yet."},
    ]
    against = [
        {"id": "C1", "text": "A new payer can be an ordinary payment to the holder: a sale, a loan or family money."},
        {"id": "C2", "text": "The payer may be part of the same scheme, not a victim; the alert says only that they "
                             "may be one."},
    ]
    reasons = {"trigger": "watch", "source_txn_id": c["txn_id"], "utr": c["utr"], "channel": c["channel"],
               "payer_account": c["payer"], "payer_bank_id": c["payer_bank_id"], "payer_where": c["payer_where"],
               "paid_at": _ts(c["at"]), "amount": str(_d(c["amount"])), "held_inr": str(held), "holds": c["holds"],
               "rule": RULES["officer"]["PROPOSE_PRE_ALERT"], "citation": CITATION, "support": support,
               "against": against, "numbers": {"amount": str(_d(c["amount"])), "held": str(held)}}
    payer_bank = c.get("payer_bank_name") or "the payer's bank"
    lines = [f"To: CFCFRMS, for the investigating police and {payer_bank}",
             f"Subject: Pre-alert: a payment into an account already on hold for cyber fraud ({c['bank_name']}, "
             f"account {c['account']})",
             "",
             f"At {when(c['at'])}, {c['payer_where']} paid {inr(c['amount'])} by {c['channel'] or 'transfer'} "
             f"(UTR {c['utr'] or '-'}) into account {c['account']} at {c['bank_name']}. No complaint names this "
             f"payment yet.",
             "",
             *hold_lines(c["holds"], c["txn_id"], c["bank_name"], c["account"]),
             "",
             f"The payer had not paid this account in the {NEW_DAYS} days before. They may be a victim who has not "
             f"reported yet. {payer_bank[:1].upper() + payer_bank[1:]} may wish to contact its customer; if they "
             f"confirm, a complaint on 1930 or cybercrime.gov.in naming UTR {c['utr'] or '-'} links the money "
             f"already held to them.",
             "",
             "Nothing more is held on this alert, and it does not say the payer has done anything wrong.",
             "",
             f"Nodal Officer, {c['bank_name']}"]
    return reasons, "\n".join(lines)


def minutes(a: str, b: str) -> int:
    return int((datetime.strptime(_ts(b), FMT) - datetime.strptime(_ts(a), FMT)).total_seconds() // 60)


def duration(m: int) -> str:
    if m < 60:
        return f"{m} minute{'' if m == 1 else 's'}"
    h, rest = divmod(m, 60)
    return f"{h} h {rest} min" if rest else f"{h} h"