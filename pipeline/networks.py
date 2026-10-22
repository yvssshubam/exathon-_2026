"""Mule networks: tickets linked through the accounts their money passed (docs/DESIGN.md, "Mule networks"; D-40).

One ticket shows one victim's trail. The same mule accounts turn up across many tickets, and they pay each other.
This step links them, so the officer sees a ring as one decision and the police get one note for all its tickets:

  candidates  own-bank accounts on worked tickets that look like mules (band MULE or REVIEW) or have a
              whole-account freeze waiting for the officer
  links       money moved directly between two candidates in the 30 days before the latest ticket (PH.TXN,
              cases and the AMLworld background alike), or two candidates cashed out at the same ATM or wallet
  networks    groups of two or more candidates joined by links (connected components), largest first

Exasol finds the links in one pass over the transaction table; grouping them is a few lines of Python. Nothing here
changes money: a network is a way to see and decide, and a freeze still needs the officer.

    python -m pipeline.networks
"""
from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass, field
from decimal import Decimal

from pipeline.explain import inr

LOOKBACK_DAYS = 30


@dataclass
class Link:
    a: str
    b: str
    kind: str                      # MONEY (a paid b), TRIED (a tried to pay b; refused) or SHARED_EXIT
    count: int
    amount: Decimal
    first_at: str
    last_at: str
    detail: str = ""


@dataclass
class Network:
    network_id: str
    members: list[str]
    links: list[Link] = field(default_factory=list)


def build(candidates: list[str], links: list[Link]) -> list[Network]:
    """Connected components of the candidates under the links; two or more members; largest first, then by name."""
    parent = {c: c for c in candidates}

    def root(x: str) -> str:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    usable = [lk for lk in links if lk.a in parent and lk.b in parent and lk.a != lk.b]
    for lk in usable:
        parent[root(lk.a)] = root(lk.b)
    groups: dict[str, list[str]] = {}
    for c in candidates:
        groups.setdefault(root(c), []).append(c)
    comps = sorted((sorted(m) for m in groups.values() if len(m) >= 2), key=lambda m: (-len(m), m[0]))
    out = []
    for i, members in enumerate(comps, 1):
        inside = set(members)
        out.append(Network(f"N{i}", members, [lk for lk in usable if lk.a in inside and lk.b in inside]))
    return out


# ---------------------------------------------------------------- words

def template(n: dict) -> str:
    """The network's facts as plain sentences: the officer's reasons for freezing it and the note to the police."""
    names = [m["name"] for m in n["members"]]
    money = [lk for lk in n["links"] if lk["kind"] == "MONEY"]
    shared = [lk for lk in n["links"] if lk["kind"] == "SHARED_EXIT"]
    parts = [f"{len(names)} accounts at {n['bank_name']} act as one network: {', '.join(names)}."]
    if money:
        total = sum(Decimal(str(lk["amount_inr"])) for lk in money)
        n_tx = sum(lk["count"] for lk in money)
        largest = sorted(money, key=lambda x: -Decimal(str(x["amount_inr"])))[:3]
        times = f"{n_tx} time{'s' if n_tx != 1 else ''}"
        largest_text = "; ".join(f"{lk['from_name']} to {lk['to_name']} {inr(lk['amount_inr'])}" for lk in largest)
        if len(money) == 1 and n_tx == 1:
            lk = money[0]
            parts.append(f"{lk['from_name']} paid {lk['to_name']} {inr(lk['amount_inr'])} in the 30 days before the "
                         "latest ticket.")
        else:
            parts.append(f"They paid each other {times} in the 30 days before the latest ticket, {inr(total)} in "
                         f"all; the largest: {largest_text}.")
    tried = [lk for lk in n["links"] if lk["kind"] == "TRIED"]
    if tried:
        parts.append("Payments between them refused because the money was held: " + "; ".join(
            f"{lk['from_name']} to {lk['to_name']} {inr(lk['amount_inr'])}" for lk in tried) + ".")
    for lk in shared:
        parts.append(f"{lk['from_name']} and {lk['to_name']} both cashed out at {lk['detail']}.")
    t = n["tickets"]
    parts.append(f"Together they are named in {len(t)} fraud ticket{'s' if len(t) != 1 else ''} reporting "
                 f"{inr(n['reported_inr'])}; {inr(n['victim_inr'])} of the victims' money was traced into them and "
                 f"{inr(n['held_inr'])} is held amount-only.")
    if n.get("refused"):
        parts.append(f"Since the holds, {n['refused']} attempt{'s' if n['refused'] != 1 else ''} to move held money "
                     f"{'were' if n['refused'] != 1 else 'was'} refused.")
    every = "both accounts" if len(names) == 2 else f"all {len(names)} accounts"
    parts.append(f"A whole-account freeze of {every} is proposed, so money cannot move between them "
                 "or out of them, and the tickets should be investigated as one case: "
                 + ", ".join(f"{x['case_id']} ({x['received_at'][11:16]}, {inr(x['reported_inr'])})" for x in t) + ".")
    return " ".join(parts)


# ---------------------------------------------------------------- Exasol

def _d(v) -> Decimal:
    return Decimal(str(v)) if v is not None else Decimal(0)


def find(conn, bank: str) -> list[Network]:
    q = lambda sql, **p: conn.execute(sql, {"b": bank, **p}).fetchall()  # noqa: E731
    candidates = [a for (a,) in q("""
        SELECT DISTINCT ca.account_id
        FROM PH.CASE_ACCOUNT ca
        JOIN PH.TICKET t ON t.case_id = ca.case_id
        JOIN PH.AGENT_RUN r ON r.run_id = t.run_id AND r.run_status = 'DONE'
        WHERE ca.viewing_bank_id = {b} AND ca.is_own_bank AND NOT ca.is_exit
          AND (ca.band IN ('MULE', 'REVIEW')
               OR EXISTS (SELECT 1 FROM PH.CASE_ACTION x WHERE x.case_id = ca.case_id
                            AND x.viewing_bank_id = ca.viewing_bank_id AND x.account_id = ca.account_id
                            AND x.action_code = 'PROPOSE_FULL_FREEZE'))
        ORDER BY 1""")]
    if len(candidates) < 2:
        return []
    window = q("SELECT ADD_DAYS(MIN(t.received_at), -{d!d}), MAX(t.received_at) + INTERVAL '1' DAY FROM PH.TICKET t "
               "WHERE t.ticket_status <> 'QUEUED'", d=LOOKBACK_DAYS)[0]
    start, end = str(window[0])[:19], str(window[1])[:19]
    # A payment the watch refused (held money) is an attempt, not money that moved: it still links the two accounts,
    # but it is stated as an attempt and never counted as paid.
    refused = ("LEFT JOIN (SELECT DISTINCT txn_id FROM PH.WATCH_EVENT WHERE viewing_bank_id = {b} "
               "AND event_kind = 'BLOCKED') w ON w.txn_id = t.txn_id")
    links = [Link(f, t, kind, int(n), _d(amt), str(a)[:19], str(z)[:19]) for f, t, kind, n, amt, a, z in q(f"""
        SELECT t.from_account, t.to_account, CASE WHEN w.txn_id IS NULL THEN 'MONEY' ELSE 'TRIED' END,
               COUNT(*), SUM(t.amount_inr), MIN(t.ts), MAX(t.ts)
        FROM PH.TXN t {refused}
        WHERE t.from_account IN ({{c}}) AND t.to_account IN ({{c}}) AND t.from_account <> t.to_account
          AND t.ts > TO_TIMESTAMP({{s}}, 'YYYY-MM-DD HH24:MI:SS')
          AND t.ts <= TO_TIMESTAMP({{e}}, 'YYYY-MM-DD HH24:MI:SS')
        GROUP BY t.from_account, t.to_account, CASE WHEN w.txn_id IS NULL THEN 'MONEY' ELSE 'TRIED' END
        ORDER BY 1, 2, 3""", c=candidates, s=start, e=end)]
    by_exit: dict[str, list[tuple]] = {}
    for f, ident, n, amt, a, z in q(f"""
        SELECT t.from_account, COALESCE(t.exit_identifier, t.to_account), COUNT(*), SUM(t.amount_inr), MIN(t.ts),
               MAX(t.ts)
        FROM PH.TXN t {refused}
        WHERE t.from_account IN ({{c}}) AND t.is_exit AND w.txn_id IS NULL
          AND t.ts > TO_TIMESTAMP({{s}}, 'YYYY-MM-DD HH24:MI:SS')
          AND t.ts <= TO_TIMESTAMP({{e}}, 'YYYY-MM-DD HH24:MI:SS')
        GROUP BY t.from_account, COALESCE(t.exit_identifier, t.to_account)
        ORDER BY 2, 1""", c=candidates, s=start, e=end):
        by_exit.setdefault(ident, []).append((f, int(n), _d(amt), str(a)[:19], str(z)[:19]))
    for ident, users in by_exit.items():
        for (f1, n1, a1, s1, z1), (f2, n2, a2, s2, z2) in zip(users, users[1:], strict=False):
            links.append(Link(f1, f2, "SHARED_EXIT", n1 + n2, a1 + a2, min(s1, s2), max(z1, z2), ident))
    return build(candidates, links)


def save(conn, bank: str, networks: list[Network]) -> None:
    conn.execute("DELETE FROM PH.NETWORK_MEMBER WHERE viewing_bank_id = {b}", {"b": bank})
    conn.execute("DELETE FROM PH.NETWORK_LINK WHERE viewing_bank_id = {b}", {"b": bank})
    members = [(n.network_id, bank, m) for n in networks for m in n.members]
    links = [(n.network_id, bank, lk.a, lk.b, lk.kind, lk.count, str(lk.amount), lk.first_at + ".000",
              lk.last_at + ".000", lk.detail or None) for n in networks for lk in n.links]
    if members:
        conn.import_from_iterable(members, ("PH", "NETWORK_MEMBER"),
                                  import_params={"columns": ["network_id", "viewing_bank_id", "account_id"]})
    if links:
        conn.import_from_iterable(links, ("PH", "NETWORK_LINK"), import_params={"columns": [
            "network_id", "viewing_bank_id", "from_account", "to_account", "link_kind", "txn_count", "amount_inr",
            "first_at", "last_at", "detail"]})


def run_networks(conn, bank: str) -> dict:
    networks = find(conn, bank)
    save(conn, bank, networks)
    return {"networks": len(networks), "accounts": sum(len(n.members) for n in networks)}


def details(conn, bank: str, network_id: str | None = None) -> list[dict]:
    """Networks as the console shows them: members with balances and waiting freezes, links, tickets, totals."""
    q = lambda sql, **p: conn.execute(sql, {"b": bank, "n": network_id, **p}).fetchall()  # noqa: E731
    where = " AND m.network_id = {n}" if network_id else ""
    rows = q("""
        SELECT m.network_id, m.account_id, COALESCE(a.holder_name, m.account_id), COALESCE(bk.bank_name, a.bank_id)
        FROM PH.NETWORK_MEMBER m
        LEFT JOIN PH.ACCOUNT a ON a.account_id = m.account_id
        LEFT JOIN PH.BANK bk ON bk.bank_id = a.bank_id
        WHERE m.viewing_bank_id = {b}""" + where + " ORDER BY m.network_id, m.account_id")
    nets: dict[str, dict] = {}
    names: dict[str, str] = {}
    for nid, acc, name, bank_name in rows:
        n = nets.setdefault(nid, {"network_id": nid, "bank_name": bank_name, "members": [], "links": [],
                                  "tickets": [], "waiting": []})
        n["members"].append({"account_id": acc, "name": name})
        names[acc] = name
    if not nets:
        return []
    accs = list(names)
    balance = {a: _d(b) for a, b in q("SELECT account_id, MAX(balance_now_inr) FROM PH.CASE_ACCOUNT "
                                       "WHERE viewing_bank_id = {b} AND account_id IN ({a}) GROUP BY account_id",
                                       a=accs)}
    held = {a: _d(h) for a, h in q("SELECT account_id, SUM(amount_inr) FROM PH.HOLD_LEDGER WHERE viewing_bank_id = {b} "
                                    "AND account_id IN ({a}) AND hold_status = 'ACTIVE' AND hold_kind = 'LIEN' "
                                    "GROUP BY account_id", a=accs)}
    refused: dict[str, int] = {}
    for a, kind, bal in q("SELECT account_id, event_kind, balance_inr FROM PH.WATCH_EVENT WHERE viewing_bank_id = {b} "
                          "AND account_id IN ({a}) ORDER BY event_at, event_id", a=accs):
        balance[a] = _d(bal)                       # the watch's last event knows the latest balance
        refused[a] = refused.get(a, 0) + (kind == "BLOCKED")
    for n in nets.values():
        for m in n["members"]:
            m["balance_inr"] = float(balance.get(m["account_id"], 0))
            # liens from several tickets can add up to more than the account holds; what is held is at most that
            m["held_inr"] = float(min(held.get(m["account_id"], Decimal(0)), balance.get(m["account_id"], Decimal(0))))
        n["held_inr"] = sum(m["held_inr"] for m in n["members"])
        n["balance_inr"] = sum(m["balance_inr"] for m in n["members"])
        n["refused"] = sum(refused.get(m["account_id"], 0) for m in n["members"])
    for nid, f, t, kind, cnt, amt, a, z, detail in q(
            "SELECT network_id, from_account, to_account, link_kind, txn_count, amount_inr, first_at, last_at, detail "
            "FROM PH.NETWORK_LINK l WHERE l.viewing_bank_id = {b}" + where.replace("m.", "l.")
            + " ORDER BY network_id, amount_inr DESC"):
        if nid in nets:
            nets[nid]["links"].append({"from_account": f, "to_account": t, "from_name": names.get(f, f),
                                       "to_name": names.get(t, t), "kind": kind, "count": int(cnt),
                                       "amount_inr": float(amt), "first_at": str(a)[:19], "last_at": str(z)[:19],
                                       "detail": detail})
    member_of = {m["account_id"]: n["network_id"] for n in nets.values() for m in n["members"]}
    tickets: dict[str, dict] = {}
    for case, acc, received, reported, victim, code, status in q("""
        SELECT ca.case_id, ca.account_id, t.received_at, c.amount_inr, ca.victim_amount_inr,
               x.action_code, x.action_status
        FROM PH.CASE_ACCOUNT ca
        JOIN PH.TICKET t ON t.case_id = ca.case_id
        JOIN PH.COMPLAINT c ON c.case_id = ca.case_id
        LEFT JOIN PH.CASE_ACTION x ON x.case_id = ca.case_id AND x.viewing_bank_id = ca.viewing_bank_id
                                  AND x.account_id = ca.account_id AND x.action_code = 'PROPOSE_FULL_FREEZE'
        WHERE ca.viewing_bank_id = {b} AND ca.account_id IN ({a})
        ORDER BY t.received_at, ca.case_id""", a=accs):
        nid = member_of[acc]
        key = (nid, case)
        tk = tickets.setdefault(key, {"case_id": case, "received_at": str(received)[:19],
                                      "reported_inr": float(reported or 0), "victim_inr": 0.0, "nid": nid})
        tk["victim_inr"] += float(victim or 0)
        if code and status in ("PROPOSED", "WAITING"):
            nets[nid]["waiting"].append({"case_id": case, "account_id": acc, "name": names[acc]})
    for tk in tickets.values():
        n = nets[tk.pop("nid")]
        n["tickets"].append(tk)
    for n in nets.values():
        n["reported_inr"] = sum(t["reported_inr"] for t in n["tickets"])
        n["victim_inr"] = sum(t["victim_inr"] for t in n["tickets"])
    return list(nets.values())


SYSTEM = (
    "You draft the reasons for freezing a network of bank accounts that pass fraud money between them, for a bank "
    "officer to check and send to the police. Write one factual paragraph in plain words. Use only the facts given "
    "and keep the list of tickets at the end. Copy every number exactly as written (amounts, counts, times, ticket "
    "IDs); add no other number, no estimate and no opinion about guilt.")


def draft(n: dict, llm=None) -> dict:
    """The officer's starting text for freezing the whole network: the model's paragraph if grounded, else fixed."""
    from pipeline.reasons import grounded

    text = template(n)
    if llm is not None:
        try:
            written = llm.write(SYSTEM, text).strip()
        except Exception:
            written = ""
        if written and grounded(written, text):
            return {"text": written, "written_by": "model", "facts": text}
    return {"text": text, "written_by": "template", "facts": text}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--bank")
    args = ap.parse_args(argv)
    from pipeline.db import connect, settings

    bank = args.bank or settings()["own_bank_id"]
    conn = connect()
    try:
        r = run_networks(conn, bank)
        print(f"{r['networks']} networks, {r['accounts']} accounts")
        for n in details(conn, bank):
            print(f"\n{n['network_id']}: {', '.join(m['name'] for m in n['members'])}")
            print("  " + template(n))
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())