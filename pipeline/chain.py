"""Referrals that survive tampering (DECISIONS D-17): every ticket carries a SHA-256 hash chain.

A police ticket is the root. When a bank files an onward entry, it signs the referral: the hash of the previous
ticket's hash plus the fields the next bank acts on (who sent it, who gets it, the account, the UTR, the victim's
share, when the money moved). The portal passes that hash on with the ticket unchanged. So:

  * the receiving bank's checker recomputes the hash from the ticket it received (075_check.sql, check
    referral_intact): a share changed on the way, or any other field, fails the check, and nothing is held
    automatically on a ticket that does not match what the sender signed;
  * the police or a court can walk the whole chain from the portal and check every hop with any SHA-256 tool
    (`python -m pipeline.chain`), and that each hop names the hash of the one before it.

Share unknown: an onward entry with no victim share (a sending bank that does not compute one) is still routed. The
portal carries the whole transfer as the most the next bank may hold (an amount-only hold, never the whole account),
marks the ticket "share unknown" and signs it itself, so the chain goes on.

The canonical text is fixed: PHv1|prev|ack|victim|from|to|account|utr|amount in paise|Y or N|moved at, with '-' for
a missing value. Exasol computes it with HASH_SHA256 (sql/portal, sql/steps/083 and 075); this module computes the
same text with hashlib, so the two are checked against each other.

    python -m pipeline.chain                # every chain on the portal, hop by hop
    python -m pipeline.chain V_LAKSHMI      # one victim's
"""
from __future__ import annotations

import argparse
import hashlib
import sys
from decimal import ROUND_HALF_UP, Decimal

VERSION = "PHv1"


def _text(v) -> str:
    return "-" if v is None or v == "" else str(v)


def paise(amount) -> str:
    if amount is None:
        return "-"
    return str(int((Decimal(str(amount)) * 100).to_integral_value(rounding=ROUND_HALF_UP)))


def canonical(prev, ack_no, victim_tag, from_bank, to_bank, account, utr, amount, share_known, txn_at) -> str:
    """The exact text that is hashed, the same as the SQL builds it."""
    parts = [VERSION, _text(prev), _text(ack_no), _text(victim_tag), _text(from_bank or "POLICE"), _text(to_bank),
             _text(account), _text(utr), paise(amount), "N" if share_known is False else "Y",
             _text(None if txn_at is None else str(txn_at)[:19])]
    return "|".join(parts)


def digest(*fields) -> str:
    return hashlib.sha256(canonical(*fields).encode("utf-8")).hexdigest()


def check_ticket(t: dict) -> bool:
    """Does the ticket's hash match its own fields? t: a portal TICKET row as a dict."""
    want = digest(t["prev_hash"], t["ack_no"], t["victim_tag"], t["origin"], t["to_bank_id"],
                  t["beneficiary_account"], t["utr"], t["amount_inr"], t["share_known"], t["txn_at"])
    return (t.get("chain_hash") or "").lower() == want


def verify(tickets: list[dict]) -> list[dict]:
    """Every hop: its own hash, and (for an onward ticket) the link to the ticket it came from."""
    by_key = {(t["case_id"], t["to_bank_id"]): t for t in tickets}
    out = []
    for t in tickets:
        own = check_ticket(t)
        parent = by_key.get((t.get("parent_case_id"), t.get("origin"))) if t.get("parent_case_id") else None
        if t.get("parent_case_id"):
            link = parent is not None and (parent.get("chain_hash") or "").lower() == (t.get("prev_hash") or "").lower()
        else:
            link = t.get("prev_hash") is None
        out.append({"case_id": t["case_id"], "to_bank_id": t["to_bank_id"], "from": t["origin"],
                    "amount_inr": None if t["amount_inr"] is None else float(t["amount_inr"]),
                    "share_known": t["share_known"] is not False, "hash": t.get("chain_hash"),
                    "fields_match": own, "linked": link, "intact": own and link})
    return out


COLUMNS = ["case_id", "to_bank_id", "origin", "parent_case_id", "ack_no", "victim_tag", "beneficiary_account", "utr",
           "amount_inr", "share_known", "txn_at", "prev_hash", "chain_hash"]


def tickets(conn, victim_tag: str | None = None) -> list[dict]:
    """Portal tickets in the chain so far, in order (a police ticket joins it when the portal delivers and signs it).
    Needs a connection that can read PH_PORTAL: the admin or the portal user."""
    from pipeline.db import PORTAL_SCHEMA

    where = "(delivered_at IS NOT NULL OR origin <> 'POLICE')" + (" AND victim_tag = {v}" if victim_tag else "")
    rows = conn.execute(f"SELECT {', '.join(COLUMNS)} FROM {PORTAL_SCHEMA}.TICKET WHERE {where} "
                        "ORDER BY created_at, ticket_id", {"v": victim_tag}).fetchall()
    return [dict(zip(COLUMNS, r, strict=True)) for r in rows]


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("victim", nargs="?", help="victim tag, e.g. V_LAKSHMI (default: every ticket)")
    args = ap.parse_args(argv)
    from pipeline.db import connect

    conn = connect()
    try:
        hops = verify(tickets(conn, args.victim))
    finally:
        conn.close()
    bad = 0
    for h in hops:
        share = "" if h["share_known"] else "  share unknown"
        state = "intact" if h["intact"] else ("FIELDS CHANGED" if not h["fields_match"] else "LINK BROKEN")
        print(f"{h['case_id']:22} {h['from']:7} -> {h['to_bank_id']:7} {h['amount_inr'] or 0:>12,.2f}  "
              f"{(h['hash'] or 'no hash')[:16]}  {state}{share}")
        bad += 0 if h["intact"] else 1
    print(f"{len(hops)} tickets, {bad} not intact")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())