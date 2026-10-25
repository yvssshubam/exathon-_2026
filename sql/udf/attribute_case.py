# Body of the Exasol Python 3 SET script ATTRIBUTE_CASE (one per bank schema) (requirements s9, step 2; FR-06, FR-07).
# sql/050_attribute_udf.sql wraps this file into CREATE OR REPLACE PYTHON3 SET SCRIPT via --@include.
#
# One call = one tracing rule (the SQL groups by rule). Input rows are of two kinds:
#   OPEN  account balance at the start of the replay   (from_account = account, amount_paise = balance)
#   TXN   one money movement, replayed in time order   (victim_tag set only on a complaint's root payment)
# Output rows:
#   HOLD  money left in an account after the replay, per component ('clean' or a victim tag)
#   EXIT  victim money that left banking through an exit
#   MOVE  victim money carried by one transaction (feeds referrals and the trail labels)
#   SHORT money an account paid out beyond what the replay knew it had (treated as old clean money)
#
# Written independently of tests/oracle.py on purpose: every account is a queue of lots for all three rules,
# where the oracle keeps plain totals. Tests run both on the same data and require identical answers.
# Money is integer paise; splits use largest remainder, ties to 'clean' first, then by name (DECISIONS D-03).

CLEAN = "clean"


def _order(component):
    return (component != CLEAN, component)


def split(total, weights):
    """Integer split of `total` in proportion to `weights` (component -> paise), summing exactly to total."""
    live = {k: w for k, w in weights.items() if w > 0}
    whole = sum(live.values())
    if total <= 0 or whole <= 0:
        return {}
    out, rest = {}, []
    for k, w in live.items():
        out[k] = total * w // whole
        rest.append(((total * w) % whole, k))
    left = total - sum(out.values())
    rest.sort(key=lambda item: (-item[0], _order(item[1])))
    for _, k in rest[:left]:
        out[k] += 1
    return {k: v for k, v in out.items() if v}


class Ledger:
    """An account as an ordered queue of lots [component, paise]; oldest first."""

    def __init__(self, opening=0):
        self.lots = [[CLEAN, opening]] if opening > 0 else []

    def balance(self):
        return sum(p for _, p in self.lots)

    def totals(self):
        t = {}
        for c, p in self.lots:
            t[c] = t.get(c, 0) + p
        return t

    def add(self, pieces):
        for c, p in pieces:
            if p > 0:
                self.lots.append([c, p])

    def add_old_clean(self, paise):
        self.lots.insert(0, [CLEAN, paise])

    def _remove(self, component, paise):
        """Take paise of one component, oldest lots of that component first."""
        for lot in self.lots:
            if paise == 0:
                break
            if lot[0] == component and lot[1] > 0:
                take = min(lot[1], paise)
                lot[1] -= take
                paise -= take
        self.lots = [lot for lot in self.lots if lot[1] > 0]

    def take(self, rule, amount):
        """Remove `amount` paise under `rule`; return the pieces that left, in leaving order."""
        if rule == "FIFO":
            out, need = [], amount
            while need:
                c, p = self.lots[0]
                t = min(p, need)
                out.append((c, t))
                need -= t
                if t == p:
                    self.lots.pop(0)
                else:
                    self.lots[0][1] = p - t
            return out
        totals = self.totals()
        if rule == "PRO_RATA":
            parts = split(amount, totals)
        elif rule == "LIBR":
            parts = {}
            clean = min(totals.get(CLEAN, 0), amount)
            if clean:
                parts[CLEAN] = clean
            parts.update(split(amount - clean, {c: p for c, p in totals.items() if c != CLEAN}))
        else:
            raise ValueError("unknown rule " + str(rule))
        for c, p in parts.items():
            self._remove(c, p)
        return sorted(parts.items(), key=lambda kv: _order(kv[0]))


def replay(rule, opening, txns):
    """opening: {account: paise}; txns: [(ts, txn_id, from, to, paise, victim_tag)].
    Returns (holdings, exits, moves, shortfalls)."""
    ledgers, exits, moves, short = {}, {}, [], {}

    def ledger(acc):
        if acc not in ledgers:
            ledgers[acc] = Ledger(opening.get(acc, 0))
        return ledgers[acc]

    # A ticket that reports only part of a payment (an onward ticket carries the victim's share) arrives as two
    # rows with the same transaction: the tagged part first, then the rest as an ordinary (clean) movement.
    for _ts, txn_id, frm, to, paise, victim in sorted(txns, key=lambda t: (t[0], t[1], t[5] is None)):
        if victim:
            pieces = [(victim, paise)]
        else:
            src = ledger(frm)
            gap = paise - src.balance()
            if gap > 0:
                src.add_old_clean(gap)
                short[frm] = short.get(frm, 0) + gap
            pieces = src.take(rule, paise)
        for c, p in pieces:
            if c != CLEAN:
                moves.append((txn_id, c, p))
        if to.startswith("EXIT:"):
            bucket = exits.setdefault(to, {})
            for c, p in pieces:
                if c != CLEAN:
                    bucket[c] = bucket.get(c, 0) + p
        else:
            ledger(to).add(pieces)
    holdings = {acc: lg.totals() for acc, lg in ledgers.items()}
    return holdings, exits, moves, short


def run(ctx):
    rule, opening, txns = None, {}, []
    while True:
        rule = ctx.trace_rule
        if ctx.row_kind == "OPEN":
            opening[ctx.from_account] = int(ctx.amount_paise)
        else:
            txns.append((ctx.ts, ctx.txn_id, ctx.from_account, ctx.to_account, int(ctx.amount_paise),
                         ctx.victim_tag))
        if not ctx.next():
            break
    holdings, exits, moves, short = replay(rule, opening, txns)
    for acc, comps in holdings.items():
        ctx.emit(rule, "HOLD", acc, CLEAN, comps.get(CLEAN, 0))
        for c, p in comps.items():
            if c != CLEAN and p:
                ctx.emit(rule, "HOLD", acc, c, p)
    for exit_id, comps in exits.items():
        for c, p in comps.items():
            ctx.emit(rule, "EXIT", exit_id, c, p)
    for txn_id, c, p in moves:
        ctx.emit(rule, "MOVE", txn_id, c, p)
    for acc, p in short.items():
        ctx.emit(rule, "SHORT", acc, CLEAN, p)