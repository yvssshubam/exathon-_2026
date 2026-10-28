"""The simulated CFCFRMS portal: the only link between banks (docs/DESIGN.md, "Multi-bank simulation").

It runs as its own database user (PH_PORTAL_SVC) and does three things, the way the national portal does:
  deliver   put each ticket in the receiving bank's inbox when the simulated clock reaches it
  collect   take what each bank filed (its outbox: onward entries, exits, KYC packets, send-backs)
  route     turn an onward entry into a new ticket for the bank that received the money, carrying the victim's
            share as the reported amount, the original acknowledgement number and the police notice

No bank calls another bank, and no bank can read the portal: Bank X's agent never learns what happened at Bank Y.
The whole-trail view in the demo reads the portal as the narrator, never as a bank.
"""
from __future__ import annotations

from datetime import datetime, timedelta

from pipeline.db import BANKS, schema_for

FMT = "%Y-%m-%d %H:%M:%S"
ROUTE_SECONDS = 60          # assumption (README): the portal turns an onward entry into a ticket within a minute

COMPLAINT_COLUMNS = ("case_id, ack_no, complaint_source, ticket_kind, parent_case_id, from_bank_id, victim_account, "
                     "victim_tag, utr, amount_inr, txn_at, reported_at, beneficiary_account, lane, notice_legal_basis, "
                     "notice_has_amount, ordering_authority, ordering_reference, fir_reference, remarks, "
                     "corrects_case_id, share_known, prev_hash, chain_hash")


def _hash_sql(prev: str, ack: str, tag: str, frm: str, to: str, acc: str, utr: str, amt: str, share: str,
              at: str) -> str:
    """The chain hash in Exasol: the same canonical text as pipeline/chain.py (D-17)."""
    parts = [f"COALESCE({prev}, '-')", f"COALESCE({ack}, '-')", f"COALESCE({tag}, '-')", f"COALESCE({frm}, 'POLICE')",
             f"COALESCE({to}, '-')", f"COALESCE({acc}, '-')", f"COALESCE({utr}, '-')",
             f"COALESCE(CAST(CAST(ROUND({amt} * 100) AS DECIMAL(20,0)) AS VARCHAR(30)), '-')",
             f"CASE WHEN {share} = FALSE THEN 'N' ELSE 'Y' END",
             f"COALESCE(TO_CHAR({at}, 'YYYY-MM-DD HH24:MI:SS'), '-')"]
    return "LOWER(HASH_SHA256('PHv1|' || " + " || '|' || ".join(parts) + "))"


# A police ticket is the root of its chain: the portal signs it as it delivers it.
ROOT_HASH = _hash_sql("prev_hash", "ack_no", "victim_tag", "origin", "to_bank_id", "beneficiary_account", "utr",
                      "amount_inr", "share_known", "txn_at")


def _ts(v) -> str:
    return str(v)[:19]


def log(conn, event: str, detail: str, sim_at: str | None = None) -> None:
    conn.execute("INSERT INTO ROUTE_LOG (sim_at, event_name, detail) VALUES "
                 "(TO_TIMESTAMP({t}, 'YYYY-MM-DD HH24:MI:SS'), {e}, {d})",
                 {"t": sim_at, "e": event, "d": detail[:2000]})


def deliver(conn, until: str) -> list[tuple[str, str, str]]:
    """Deliver every ticket created by `until` that no bank has yet. Returns (bank, case_id, received_at)."""
    due = conn.execute("SELECT ticket_id, case_id, to_bank_id, created_at FROM TICKET "
                       "WHERE delivered_at IS NULL AND created_at <= TO_TIMESTAMP({u}, 'YYYY-MM-DD HH24:MI:SS') "
                       "ORDER BY created_at, ticket_id", {"u": until}).fetchall()
    out = []
    for ticket_id, case_id, bank, created in due:
        s = schema_for(bank)
        conn.execute(f"UPDATE TICKET SET chain_hash = {ROOT_HASH} WHERE ticket_id = {{t!d}} AND chain_hash IS NULL "
                     "AND origin = 'POLICE'", {"t": str(int(ticket_id))})
        conn.execute(f"""
            INSERT INTO {s}.COMPLAINT ({COMPLAINT_COLUMNS})
            SELECT case_id, ack_no, complaint_source, ticket_kind, parent_case_id,
                   CASE WHEN origin = 'POLICE' THEN CAST(NULL AS VARCHAR(20)) ELSE origin END,
                   victim_account, victim_tag, utr, amount_inr, txn_at, created_at, beneficiary_account, lane,
                   notice_legal_basis, notice_has_amount, ordering_authority, ordering_reference, fir_reference,
                   remarks, corrects_case_id, share_known, prev_hash, chain_hash
            FROM TICKET WHERE ticket_id = {{t!d}}""", {"t": str(int(ticket_id))})
        conn.execute(f"INSERT INTO {s}.TICKET (case_id, received_at, ticket_status, origin) "
                     "SELECT case_id, created_at, 'QUEUED', origin FROM TICKET WHERE ticket_id = {t!d}",
                     {"t": str(int(ticket_id))})
        conn.execute("UPDATE TICKET SET delivered_at = created_at WHERE ticket_id = {t!d}",
                     {"t": str(int(ticket_id))})
        log(conn, "delivered", f"{case_id} to {BANKS[bank]['name']}", _ts(created))
        out.append((bank, case_id, _ts(created)))
    return out


def collect(conn, bank: str) -> list[int]:
    """Collect `bank`'s new filings; route its onward entries. Returns the portal ticket IDs it created."""
    s = schema_for(bank)
    last = conn.execute("SELECT COALESCE(MAX(bank_entry_id), 0) FROM ENTRY WHERE from_bank_id = {b}",
                        {"b": bank}).fetchval()
    conn.execute(f"""
        INSERT INTO ENTRY (from_bank_id, bank_entry_id, case_id, entry_kind, from_account, account_id, bank_id, txn_id,
                           utr, channel, amount_inr, victim_amount_inr, moved_at, detail, filed_at, received_at,
                           prev_hash, chain_hash)
        SELECT {{b}}, entry_id, case_id, entry_kind, from_account, account_id, bank_id, txn_id, utr, channel,
               amount_inr, victim_amount_inr, moved_at, detail, filed_at, filed_at, prev_hash, chain_hash
        FROM {s}.PORTAL_ENTRY WHERE entry_id > {{last!d}}""", {"b": bank, "last": str(int(last or 0))})
    created = []
    for entry_id, case_id, account, to_bank, utr, share, moved, filed, moved_inr in conn.execute(
            "SELECT entry_id, case_id, account_id, bank_id, utr, victim_amount_inr, moved_at, filed_at, amount_inr "
            "FROM ENTRY WHERE from_bank_id = {b} AND entry_kind = 'ONWARD' AND routed_ticket_id IS NULL "
            "ORDER BY entry_id", {"b": bank}).fetchall():
        # share unknown (D-17): a sending bank that gives no victim share. The next bank may hold at most the whole
        # transfer (an amount-only hold, never the whole account); the ticket says so, and the portal signs it.
        known = share is not None
        if not known and moved_inr is not None and float(moved_inr) > 0:
            share = moved_inr
            log(conn, "share_unknown", f"{case_id}: onward entry to {to_bank} gives no victim share; the ticket "
                                       f"carries the whole transfer as the most to hold", _ts(filed))
        if not share or float(share) <= 0:
            # none of the victim's money went there: a Rs 0 ticket would only fail the next bank's checks
            log(conn, "not_routed", f"{case_id}: onward entry to {to_bank} carries none of the victim's money",
                _ts(filed))
            conn.execute("UPDATE ENTRY SET routed_ticket_id = -1 WHERE entry_id = {e!d}", {"e": str(int(entry_id))})
            continue
        if to_bank not in BANKS:
            log(conn, "not_routed", f"{case_id}: onward entry to {to_bank} (not in the simulation)", _ts(filed))
            conn.execute("UPDATE ENTRY SET routed_ticket_id = -1 WHERE entry_id = {e!d}", {"e": str(int(entry_id))})
            continue
        n = conn.execute("SELECT COUNT(*) FROM TICKET WHERE parent_case_id = {c} AND to_bank_id = {t}",
                         {"c": case_id, "t": to_bank}).fetchval()
        new_case = f"{case_id}-{to_bank[-1]}{int(n) + 1}"
        at = (datetime.strptime(_ts(filed), FMT) + timedelta(seconds=ROUTE_SECONDS)).strftime(FMT)
        conn.execute("""
            INSERT INTO TICKET (case_id, to_bank_id, origin, parent_case_id, source_entry_id, ack_no, complaint_source,
                                ticket_kind, victim_account, victim_tag, utr, amount_inr, txn_at, created_at,
                                beneficiary_account, lane, notice_legal_basis, notice_has_amount, ordering_authority,
                                ordering_reference, fir_reference, remarks, corrects_case_id, delivered_at,
                                share_known, prev_hash, chain_hash)
            SELECT {n}, {t}, {fb}, {c}, {e!d}, p.ack_no, 'BANK', 'ONWARD', CAST(NULL AS VARCHAR(40)), p.victim_tag,
                   {u}, {share!d}, TO_TIMESTAMP({moved}, 'YYYY-MM-DD HH24:MI:SS'),
                   TO_TIMESTAMP({at}, 'YYYY-MM-DD HH24:MI:SS'), {acc}, p.lane, p.notice_legal_basis, TRUE,
                   p.ordering_authority, p.ordering_reference, p.fir_reference, CAST(NULL AS VARCHAR(2000)),
                   CAST(NULL AS VARCHAR(40)), CAST(NULL AS TIMESTAMP), {known!r},
                   -- the filing bank's signature, passed on unchanged; or, when it gave none, the portal's
                   COALESCE(e.prev_hash, p.chain_hash),
                   COALESCE(e.chain_hash, """ + _hash_sql("p.chain_hash", "p.ack_no", "p.victim_tag", "{fb}", "{t}",
                                                          "{acc}", "{u}", "{share!d}", "{known!r}",
                                                          "TO_TIMESTAMP({moved}, 'YYYY-MM-DD HH24:MI:SS')") + """)
            FROM TICKET p JOIN ENTRY e ON e.entry_id = {e!d}
            WHERE p.case_id = {c} AND p.to_bank_id = {fb}""",
                     {"n": new_case, "t": to_bank, "fb": bank, "c": case_id, "e": str(int(entry_id)), "u": utr,
                      "share": str(share or 0), "moved": _ts(moved), "at": at, "acc": account,
                      "known": "TRUE" if known else "FALSE"})
        ticket_id = conn.execute("SELECT MAX(ticket_id) FROM TICKET WHERE case_id = {n}", {"n": new_case}).fetchval()
        if ticket_id is None:
            log(conn, "not_routed", f"{case_id}: no portal ticket for the filing bank's case", _ts(filed))
            continue
        conn.execute("UPDATE ENTRY SET routed_ticket_id = {t!d} WHERE entry_id = {e!d}",
                     {"t": str(int(ticket_id)), "e": str(int(entry_id))})
        log(conn, "routed", f"{BANKS[bank]['name']} filed an onward entry on {case_id}: ticket {new_case} for "
                            f"{BANKS[to_bank]['name']} (account {account})", at)
        created.append(int(ticket_id))
    return created


def next_ticket_at(conn) -> str | None:
    """When the next undelivered ticket is due."""
    v = conn.execute("SELECT MIN(created_at) FROM TICKET WHERE delivered_at IS NULL").fetchval()
    return _ts(v) if v else None


def deliver_one(conn, case_id: str) -> None:
    """Deliver one ticket now, whatever the clock says (for `python -m pipeline.agent CASE_X` on its own)."""
    row = conn.execute("SELECT created_at FROM TICKET WHERE case_id = {c} AND delivered_at IS NULL",
                       {"c": case_id}).fetchone()
    if row:
        deliver(conn, _ts(row[0]))