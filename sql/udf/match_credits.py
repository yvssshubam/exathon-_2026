# Body of the Exasol Python 3 SET script MATCH_CREDITS (one per bank schema; DECISIONS D-57).
# sql/bank/052_match_udf.sql wraps this file into CREATE OR REPLACE PYTHON3 SET SCRIPT via --@include, and
# sql/steps/060_score.sql calls it once per account (GROUP BY account_id).
#
# Per-credit matching for P2, P3 and E4: each debit is given to the earliest credit that arrived in the 60 minutes
# before it and still has room, and spills over to the next one only if that credit is full. So one debit is never
# counted twice, and a credit's matched amount is never above the credit.
#
# Input rows, one per money movement on the account in the window:
#   direction     'C' credit into the account, 'D' debit out of it
#   ts            when it happened
#   txn_id        the transaction
#   amount_paise  integer paise
#   exit_kind     for a debit: 'CASH' (ATM, cash or crypto), 'AUTO' (NACH or UPI AutoPay: never matched, D-54) or
#                 'MOVE' (any other payment out); ignored for credits
#   fraud_linked  for a credit: TRUE when it came from a flagged account or a complaint names it (E4)
# Output rows, one per credit:
#   txn_id, amount_paise, fraud_linked, out_paise (matched to any debit within the hour), cash_paise (the part of
#   out_paise that left as cash or crypto)
#
# Written to be read: plain lists, no clever tricks. tests/test_match_credits.py runs it without Exasol.

WINDOW_SECONDS = 60 * 60


def _seconds(ts):
    """Seconds since an arbitrary origin, for a datetime or a 'YYYY-MM-DD HH:MM:SS' string."""
    if isinstance(ts, str):
        import datetime as _dt

        ts = _dt.datetime.strptime(ts[:19], "%Y-%m-%d %H:%M:%S")
    return ts.timestamp() if hasattr(ts, "timestamp") else float(ts)


def match(events):
    """events: list of dicts with direction, ts, txn_id, amount_paise, exit_kind, fraud_linked.
    Returns one dict per credit: txn_id, amount_paise, fraud_linked, out_paise, cash_paise."""
    ordered = sorted(events, key=lambda e: (_seconds(e["ts"]), 0 if e["direction"] == "C" else 1, e["txn_id"]))
    credits = []                     # credits in arrival order, each with the room it still has
    for e in ordered:
        if e["direction"] == "C":
            credits.append({"txn_id": e["txn_id"], "at": _seconds(e["ts"]), "amount_paise": int(e["amount_paise"]),
                            "fraud_linked": bool(e["fraud_linked"]), "out_paise": 0, "cash_paise": 0})
            continue
        if e["exit_kind"] == "AUTO":
            continue                 # automatic payments to registered companies are not money moving on
        left = int(e["amount_paise"])
        debit_at = _seconds(e["ts"])
        for c in credits:            # earliest first
            if left <= 0:
                break
            if c["at"] < debit_at - WINDOW_SECONDS or c["at"] > debit_at:
                continue
            room = c["amount_paise"] - c["out_paise"]
            if room <= 0:
                continue
            take = min(room, left)
            c["out_paise"] += take
            if e["exit_kind"] == "CASH":
                c["cash_paise"] += take
            left -= take
    return [{k: c[k] for k in ("txn_id", "amount_paise", "fraud_linked", "out_paise", "cash_paise")}
            for c in credits]


def run(ctx):
    account = None
    events = []
    while True:
        account = ctx.account_id
        events.append({"direction": ctx.direction, "ts": ctx.ts, "txn_id": ctx.txn_id,
                       "amount_paise": int(ctx.amount_paise), "exit_kind": ctx.exit_kind,
                       "fraud_linked": bool(ctx.fraud_linked)})
        if not ctx.next():
            break
    for c in match(events):
        ctx.emit(account, c["txn_id"], c["amount_paise"], c["fraud_linked"], c["out_paise"], c["cash_paise"])