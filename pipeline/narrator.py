"""The narrator's view (Module 8): the whole trail across every bank, for the demo and the judges.

No single bank sees this. It reads every bank's schema and the portal as the admin user, which no bank's agent,
checker or officer ever is. Bank X's agent cannot run this code: its database users have no privilege on the other
schemas. Every screen that shows it carries the caption below.
"""
from __future__ import annotations

from decimal import Decimal

from pipeline.db import BANKS, PORTAL_SCHEMA, schema_for

CAPTION = "Narrator's view: no single bank sees this."


def _d(v) -> Decimal:
    return Decimal(str(v)) if v is not None else Decimal(0)


def _ts(v) -> str | None:
    return str(v)[:19] if v is not None else None


def labels(admin) -> dict[str, str]:
    """Every bank's complaints by the name the officer sees ("02:13 complaint"), not the internal case ID."""
    import re

    from pipeline.explain import ticket_label

    out = {}
    for bank in BANKS:
        for case, reported, kind, from_bank in admin.execute(
                f"SELECT case_id, reported_at, ticket_kind, from_bank_id FROM {schema_for(bank)}.COMPLAINT").fetchall():
            out[case] = ticket_label({"case_id": case, "reported_at": reported, "ticket_kind": kind,
                                      "from_bank_id": from_bank})
    for case, kind, at in admin.execute(
            f"SELECT case_id, ticket_kind, created_at FROM {PORTAL_SCHEMA}.TICKET").fetchall():
        out.setdefault(case, ticket_label({"case_id": case, "reported_at": at, "ticket_kind": kind}))
    alts = "|".join(re.escape(c) for c in sorted(out, key=len, reverse=True))
    out["__re__"] = re.compile(f"(?:ticket )?({alts})") if out else None
    return out


def humanise(text: str, names: dict) -> str:
    """Swap internal case IDs in a sentence for the complaint's name."""
    rx = names.get("__re__")
    return rx.sub(lambda m: f"the {names[m.group(1)]}", text) if rx and text else text


def timeline(admin, until: str | None = None) -> list[dict]:
    """What happened where and when: tickets, holds, filings, refused attempts, the portal's routing."""
    out = []
    for at, event, detail in admin.execute(f"SELECT sim_at, event_name, detail FROM {PORTAL_SCHEMA}.ROUTE_LOG "
                                           "WHERE sim_at IS NOT NULL ORDER BY sim_at, event_id").fetchall():
        out.append({"at": _ts(at), "bank": None, "kind": f"portal_{event}", "text": detail})
    for bank, b in BANKS.items():
        s = schema_for(bank)
        for case, received, picked, run_mode in admin.execute(
                f"SELECT t.case_id, t.received_at, t.picked_up_at, r.run_mode FROM {s}.TICKET t "
                f"LEFT JOIN {s}.AGENT_RUN r ON r.run_id = t.run_id ORDER BY t.received_at").fetchall():
            if picked:
                who = "the desk" if run_mode == "desk" else "the agent"
                out.append({"at": _ts(picked), "bank": bank, "kind": "worked", "case_id": case,
                            "received_at": _ts(received), "by": "desk" if run_mode == "desk" else "agent",
                            "text": f"{b['name']}: {who} picks up {case} (it arrived at {_ts(received)[11:16]})"})
            else:
                out.append({"at": _ts(received), "bank": bank, "kind": "waiting", "case_id": case,
                            "text": f"{b['name']}: {case} waits in the queue"
                                    + (f" (the desk opens at {b.get('desk_opens')})" if b.get("desk_opens") else "")})
        for case, acc, kind, amount, tag, placed, by in admin.execute(
                f"SELECT h.case_id, COALESCE(a.holder_name, h.account_id), h.hold_kind, h.amount_inr, h.hold_tag, "
                f"h.placed_at, h.placed_by FROM {s}.HOLD_LEDGER h LEFT JOIN {s}.ACCOUNT a ON a.account_id = "
                f"h.account_id ORDER BY h.placed_at, h.hold_id").fetchall():
            what = {"LIEN": f"holds Rs {_d(amount):,.2f}", "MARKER": "marks the account to catch later credits",
                    "ACCOUNT_FREEZE": "freezes the whole account"}.get(kind, kind)
            out.append({"at": _ts(placed), "bank": bank, "kind": "hold", "case_id": case, "account": acc,
                        "amount": float(_d(amount)), "hold_kind": kind, "tag": tag, "by": by,
                        "text": f"{b['name']}: {what} on {acc} ({case}"
                                f"{', not linked yet' if tag == 'NOT_LINKED' else ''};"
                                f" {'automatic' if by == 'agent' else 'approved by ' + by})"})
        for acc, at, amount, channel in admin.execute(
                f"SELECT COALESCE(a.holder_name, d.account_id), d.attempted_at, d.amount_inr, d.channel FROM "
                f"{s}.DECLINED_ATTEMPT d LEFT JOIN {s}.ACCOUNT a ON a.account_id = d.account_id "
                "ORDER BY d.attempted_at").fetchall():
            out.append({"at": _ts(at), "bank": bank, "kind": "refused", "account": acc, "amount": float(_d(amount)),
                        "channel": channel,
                        "text": f"{b['name']}: {acc} tries to move Rs {_d(amount):,.2f} by {channel}; refused (held)"})
    # Money that left banking on the case accounts (cash and crypto), as it happened.
    for bank, b in BANKS.items():
        s = schema_for(bank)
        for at, acc, amount, ident in admin.execute(
                f"SELECT t.ts, COALESCE(a.holder_name, t.from_account), t.amount_inr, t.exit_identifier FROM "
                f"{s}.TXN_LIVE t JOIN {s}.ACCOUNT a ON a.account_id = t.from_account AND a.bank_id = {{b}} "
                "WHERE t.is_exit AND t.data_source = 'demo_case' ORDER BY t.ts", {"b": bank}).fetchall():
            out.append({"at": _ts(at), "bank": bank, "kind": "cash_out", "account": acc, "amount": float(_d(amount)),
                        "where": ident,
                        "text": f"{b['name']}: {acc} withdraws Rs {_d(amount):,.2f} ({ident}): the money is gone"})
    names = labels(admin)
    out = [dict(e, text=humanise(e["text"], names)) for e in out if e["at"] and (until is None or e["at"] <= until)]
    return sorted(out, key=lambda e: (e["at"], e["bank"] or ""))


def victim_trail(admin, victim_tag: str) -> dict:
    """Where one victim's money is, per bank: held (each bank's holds, at most that bank's share), or gone."""
    banks = []
    total_held = Decimal(0)
    names = labels(admin)
    for bank, b in BANKS.items():
        s = schema_for(bank)
        rows = admin.execute(
            f"SELECT ca.case_id, ca.account_id, COALESCE(a.holder_name, ca.account_id), ca.victim_amount_inr, "
            f"ca.is_exit, (SELECT SUM(h.amount_inr) FROM {s}.HOLD_LEDGER h WHERE h.case_id = ca.case_id "
            f"AND h.account_id = ca.account_id AND h.hold_status = 'ACTIVE' AND h.hold_kind = 'LIEN'), "
            f"(SELECT MAX(CASE WHEN h.hold_kind = 'ACCOUNT_FREEZE' THEN 1 ELSE 0 END) FROM {s}.HOLD_LEDGER h "
            f"WHERE h.account_id = ca.account_id AND h.hold_status = 'ACTIVE') "
            f"FROM {s}.CASE_ACCOUNT ca JOIN {s}.COMPLAINT c ON c.case_id = ca.case_id "
            f"LEFT JOIN {s}.ACCOUNT a ON a.account_id = ca.account_id "
            f"WHERE c.victim_tag = {{v}} AND (ca.is_own_bank OR ca.is_exit) AND ca.victim_amount_inr > 0 "
            f"ORDER BY ca.case_id, ca.hop", {"v": victim_tag}).fetchall()
        accounts, held = [], Decimal(0)
        for case, acc, name, victim, is_exit, lien, frozen in rows:
            protected = _d(victim) if int(frozen or 0) else min(_d(victim), _d(lien))
            held += Decimal(0) if is_exit else protected
            accounts.append({"case_id": case, "label": names.get(case, case), "account_id": acc, "name": name,
                             "victim_inr": float(_d(victim)),
                             "held_inr": 0.0 if is_exit else float(protected), "is_exit": bool(is_exit)})
        waiting = admin.execute(f"SELECT COUNT(*) FROM {s}.TICKET t JOIN {s}.COMPLAINT c ON c.case_id = t.case_id "
                                f"WHERE c.victim_tag = {{v}} AND t.ticket_status = 'QUEUED'",
                                {"v": victim_tag}).fetchval()
        total_held += held
        # the victim's money moving through this bank, as its trace saw it
        moves = [{"case_id": case, "label": names.get(case, case), "hop": int(hop), "from": fn or fa, "to": tn or ta,
                  "to_id": ta, "amount_inr": float(_d(amt)), "victim_inr": float(_d(vin)), "at": _ts(ts),
                  "is_exit": bool(ex), "where": ident}
                 for case, hop, fa, ta, amt, ts, ex, fn, tn, vin, ident in admin.execute(
                     f"SELECT e.case_id, e.hop, e.from_account, e.to_account, e.amount_inr, e.ts, e.is_exit, "
                     f"fa.holder_name AS from_name, ta.holder_name AS to_name, "
                     f"COALESCE(f.victim_inr, e.amount_inr) AS victim_inr, t.exit_identifier "
                     f"FROM {s}.TRACE_EDGE e JOIN {s}.COMPLAINT c ON c.case_id = e.case_id "
                     f"LEFT JOIN {s}.ACCOUNT fa ON fa.account_id = e.from_account "
                     f"LEFT JOIN {s}.ACCOUNT ta ON ta.account_id = e.to_account "
                     f"LEFT JOIN {s}.TXN t ON t.txn_id = e.txn_id "
                     f"LEFT JOIN {s}.ATTRIBUTION_FLOW f ON f.case_id = e.case_id AND f.txn_id = e.txn_id "
                     f"AND f.trace_rule = 'LIBR' AND f.victim_tag = c.victim_tag "
                     f"WHERE c.victim_tag = {{v}} ORDER BY e.ts, e.hop", {"v": victim_tag}).fetchall()]
        received = [{"case_id": case, "label": names.get(case, case), "at": _ts(at), "kind": kind,
                     "picked_up_at": _ts(picked), "amount_inr": float(_d(amt))}
                    for case, at, kind, picked, amt in admin.execute(
                        f"SELECT t.case_id, t.received_at, c.ticket_kind, t.picked_up_at, c.amount_inr "
                        f"FROM {s}.TICKET t JOIN {s}.COMPLAINT c ON c.case_id = t.case_id "
                        f"WHERE c.victim_tag = {{v}} ORDER BY t.received_at",
                        {"v": victim_tag}).fetchall()]
        banks.append({"bank_id": bank, "name": b["name"], "works_tickets": b.get("works_tickets"),
                      "desk_opens": b.get("desk_opens"), "victim_held_inr": float(held), "accounts": accounts,
                      "tickets_waiting": int(waiting or 0), "moves": moves, "tickets": received})
    return {"victim_tag": victim_tag, "banks": banks, "victim_held_inr": float(total_held), "caption": CAPTION}