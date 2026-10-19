"""Reference implementation of the attribution rules (requirements section 9, step 2).

This file is the oracle: a slow, plain replay with no Exasol and no shared code with the UDF in
sql/udf/attribute_case.py. Exasol's results are compared against it (AT-04, AT-05, AT-06).
Keep it short and obvious. If it disagrees with Exasol, check this file by hand first.

Money is integer paise throughout (DECISIONS D-03). Splits use largest-remainder rounding, so
every split adds up exactly and money is conserved to the paisa.

Rules, on every outflow from an account:
  LIBR      clean money leaves first, then tainted money (victims split pro rata between themselves)
  FIFO      oldest pieces leave first; the opening balance is the oldest piece; a mixed credit keeps
            the order its pieces left the sender (DECISIONS D-04)
  PRO_RATA  every component (clean and each victim) leaves in proportion to its share of the balance
"""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field

CLEAN = "clean"
RULES = ("LIBR", "FIFO", "PRO_RATA")
EXIT_PREFIX = "EXIT:"


@dataclass(frozen=True)
class Txn:
    txn_id: str
    ts: str                      # "YYYY-MM-DD HH:MM:SS"; sorts correctly as a string
    from_account: str
    to_account: str
    amount_paise: int
    victim_tag: str | None = None  # set only on a complaint's root transaction

    @property
    def is_exit(self) -> bool:
        return self.to_account.startswith(EXIT_PREFIX)


def _tie_key(component: str) -> tuple[int, str]:
    return (0 if component == CLEAN else 1, component)


def allocate(total: int, weights: dict[str, int]) -> dict[str, int]:
    """Split `total` paise across `weights` in proportion, as integers that sum exactly to `total`.

    Largest remainder method; ties go to `clean` first, then by component name.
    """
    weights = {k: w for k, w in weights.items() if w > 0}
    if total == 0 or not weights:
        return {}
    whole = sum(weights.values())
    shares: dict[str, int] = {}
    remainders: list[tuple[int, tuple[int, str], str]] = []
    for key, w in weights.items():
        q, r = divmod(total * w, whole)
        shares[key] = q
        remainders.append((-r, _tie_key(key), key))
    left = total - sum(shares.values())
    for _, _, key in sorted(remainders)[:left]:
        shares[key] += 1
    return {k: v for k, v in shares.items() if v > 0}


class _Pool:
    """Composition of one account for LIBR and PRO_RATA: component -> paise."""

    def __init__(self, opening: int):
        self.comp: dict[str, int] = {CLEAN: opening} if opening else {}

    def balance(self) -> int:
        return sum(self.comp.values())

    def credit(self, pieces: list[tuple[str, int]]) -> None:
        for component, paise in pieces:
            self.comp[component] = self.comp.get(component, 0) + paise

    def top_up_clean(self, paise: int) -> None:
        self.comp[CLEAN] = self.comp.get(CLEAN, 0) + paise

    def take(self, taken: dict[str, int]) -> list[tuple[str, int]]:
        for component, paise in taken.items():
            self.comp[component] -= paise
            if self.comp[component] == 0:
                del self.comp[component]
        return sorted(taken.items(), key=lambda kv: _tie_key(kv[0]))


class _Lots:
    """Ordered pieces of one account for FIFO."""

    def __init__(self, opening: int):
        self.lots: deque[list] = deque([[CLEAN, opening]] if opening else [])

    def balance(self) -> int:
        return sum(p for _, p in self.lots)

    def credit(self, pieces: list[tuple[str, int]]) -> None:
        for component, paise in pieces:
            if paise:
                self.lots.append([component, paise])

    def top_up_clean(self, paise: int) -> None:
        self.lots.appendleft([CLEAN, paise])   # pre-window money is the oldest money

    def comp(self) -> dict[str, int]:
        out: dict[str, int] = {}
        for component, paise in self.lots:
            out[component] = out.get(component, 0) + paise
        return out


@dataclass
class Result:
    rule: str
    holdings: dict[str, dict[str, int]] = field(default_factory=dict)   # account -> component -> paise
    exits: dict[str, dict[str, int]] = field(default_factory=dict)      # exit id -> victim -> paise
    moves: list[tuple[str, str, int]] = field(default_factory=list)     # (txn_id, component, paise)
    shortfalls: dict[str, int] = field(default_factory=dict)            # account -> paise (DECISIONS D-05)
    reported: dict[str, int] = field(default_factory=dict)              # victim -> paise at the root

    def tainted(self, account: str, victim: str | None = None) -> int:
        comp = self.exits.get(account) if account.startswith(EXIT_PREFIX) else self.holdings.get(account)
        comp = comp or {}
        if victim is not None:
            return comp.get(victim, 0)
        return sum(p for c, p in comp.items() if c != CLEAN)

    def clean(self, account: str) -> int:
        return self.holdings.get(account, {}).get(CLEAN, 0)

    def victim_total(self, victim: str) -> int:
        in_accounts = sum(c.get(victim, 0) for c in self.holdings.values())
        in_exits = sum(c.get(victim, 0) for c in self.exits.values())
        return in_accounts + in_exits

    def victims(self) -> list[str]:
        return sorted(self.reported)


def replay(
    txns: list[Txn],
    rule: str,
    opening: dict[str, int] | None = None,
    as_of: str | None = None,
) -> Result:
    """Replay `txns` in time order under `rule` and return where every victim's paise ended up."""
    if rule not in RULES:
        raise ValueError(f"unknown rule {rule!r}; expected one of {RULES}")
    opening = opening or {}
    make = _Lots if rule == "FIFO" else _Pool
    accounts: dict[str, _Pool | _Lots] = {}
    result = Result(rule=rule)

    def account(acc: str):
        if acc not in accounts:
            accounts[acc] = make(opening.get(acc, 0))
        return accounts[acc]

    for t in sorted(txns, key=lambda t: (t.ts, t.txn_id)):
        if as_of is not None and t.ts > as_of:
            continue
        if t.amount_paise <= 0:
            raise ValueError(f"{t.txn_id}: amount must be positive")

        if t.victim_tag:
            # The complaint's root: all of it is the victim's money on arrival. The victim's own
            # account (usually at another bank) is not replayed.
            pieces = [(t.victim_tag, t.amount_paise)]
            result.reported[t.victim_tag] = result.reported.get(t.victim_tag, 0) + t.amount_paise
        else:
            src = account(t.from_account)
            short = t.amount_paise - src.balance()
            if short > 0:
                src.top_up_clean(short)
                result.shortfalls[t.from_account] = result.shortfalls.get(t.from_account, 0) + short
            pieces = _debit(src, rule, t.amount_paise)

        for component, paise in pieces:
            result.moves.append((t.txn_id, component, paise))

        if t.is_exit:
            dest = result.exits.setdefault(t.to_account, {})
            for component, paise in pieces:
                if component != CLEAN:
                    dest[component] = dest.get(component, 0) + paise
        else:
            account(t.to_account).credit(pieces)

    for acc, state in accounts.items():
        comp = state.comp() if isinstance(state, _Lots) else dict(state.comp)
        result.holdings[acc] = {c: p for c, p in comp.items() if p}
    return result


def _debit(src: _Pool | _Lots, rule: str, amount: int) -> list[tuple[str, int]]:
    if rule == "FIFO":
        out: list[tuple[str, int]] = []
        need = amount
        while need:
            lot = src.lots[0]
            take = min(lot[1], need)
            out.append((lot[0], take))
            lot[1] -= take
            need -= take
            if lot[1] == 0:
                src.lots.popleft()
        return out

    if rule == "PRO_RATA":
        return src.take(allocate(amount, src.comp))

    # LIBR: clean first, then tainted money split pro rata between victims.
    from_clean = min(src.comp.get(CLEAN, 0), amount)
    taken = {CLEAN: from_clean} if from_clean else {}
    tainted = {c: p for c, p in src.comp.items() if c != CLEAN}
    taken.update(allocate(amount - from_clean, tainted))
    return src.take(taken)


def to_paise(rupees) -> int:
    """Exact rupees -> paise. Accepts int, str or Decimal; refuses floats with more than 2 decimals."""
    from decimal import Decimal

    d = Decimal(str(rupees))
    paise = d * 100
    if paise != paise.to_integral_value():
        raise ValueError(f"{rupees!r} has fractions of a paisa")
    return int(paise)