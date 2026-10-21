"""Read case files (data/demo_case.yaml, data/extra_cases.yaml) into plain rows.

The same rows feed the oracle tests and the Exasol loader, so the database and the oracle always
see identical inputs. No business logic lives here.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
DEMO_CASE = ROOT / "data" / "demo_case.yaml"
EXTRA_CASES = ROOT / "data" / "extra_cases.yaml"
LOOP_CASE = ROOT / "data" / "loop_case.yaml"
EXIT_PREFIX = "EXIT:"


def paise(rupees) -> int:
    d = Decimal(str(rupees)) * 100
    if d != d.to_integral_value():
        raise ValueError(f"{rupees!r} has fractions of a paisa")
    return int(d)


@dataclass
class TxnRow:
    txn_id: str
    utr: str
    ts: str
    from_account: str
    to_account: str
    amount_paise: int
    channel: str
    victim_tag: str | None = None
    exit_identifier: str | None = None

    @property
    def is_exit(self) -> bool:
        return self.to_account.startswith(EXIT_PREFIX)

    @property
    def amount_inr(self) -> Decimal:
        return Decimal(self.amount_paise) / 100


@dataclass
class Case:
    case_id: str
    complaints: list[dict]
    banks: list[dict]
    accounts: list[dict]
    txns: list[TxnRow]
    tool_as_of: str | None = None
    expected_libr: dict = field(default_factory=dict)
    expected_actions: dict = field(default_factory=dict)
    expected_compare: dict = field(default_factory=dict)
    today: dict = field(default_factory=dict)

    def opening_paise(self) -> dict[str, int]:
        return {
            a["account_id"]: paise(a["opening_balance_inr"])
            for a in self.accounts
            if a.get("opening_balance_inr") is not None
        }

    def account(self, account_id: str) -> dict:
        return next(a for a in self.accounts if a["account_id"] == account_id)

    def own_bank_ids(self) -> set[str]:
        return {b["bank_id"] for b in self.banks if b["is_own_bank"]}


def _txn(raw: dict) -> TxnRow:
    return TxnRow(
        txn_id=raw["txn_id"],
        utr=raw["utr"],
        ts=raw["ts"],
        from_account=raw["from"],
        to_account=raw["to"],
        amount_paise=paise(raw["amount_inr"]),
        channel=raw["channel"],
        victim_tag=raw.get("victim_tag"),
        exit_identifier=raw.get("exit_identifier"),
    )


def load_case(path: Path = DEMO_CASE) -> Case:
    raw = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    complaints = raw.get("complaints") or [raw["complaint"]]
    for c in complaints:
        c.setdefault("case_id", raw["case_id"])
    case = Case(
        case_id=raw["case_id"],
        complaints=complaints,
        banks=raw.get("banks", []),
        accounts=raw["accounts"],
        txns=[_txn(t) for t in raw["transactions"]],
        tool_as_of=raw.get("tool_as_of"),
        expected_libr=raw.get("expected_libr", {}),
        expected_actions=raw.get("expected_actions", {}),
        expected_compare=raw.get("expected_compare", {}),
        today=raw.get("today", {}),
    )
    _check(case)
    return case


def _check(case: Case) -> None:
    """Fail loudly on malformed case files rather than producing quietly wrong numbers."""
    known = {a["account_id"] for a in case.accounts}
    ids = [t.txn_id for t in case.txns]
    if len(ids) != len(set(ids)):
        raise ValueError(f"{case.case_id}: duplicate txn_id")
    for t in case.txns:
        for acc in (t.from_account, t.to_account):
            if not acc.startswith(EXIT_PREFIX) and acc not in known:
                raise ValueError(f"{case.case_id}: {t.txn_id} uses unknown account {acc}")
        if t.is_exit and not t.exit_identifier:
            raise ValueError(f"{case.case_id}: exit {t.txn_id} has no exit_identifier (FR-05)")
    bank_ids = {b["bank_id"] for b in case.banks}
    for a in case.accounts:
        if bank_ids and a["bank_id"] not in bank_ids:
            raise ValueError(f"{case.case_id}: account {a['account_id']} at unknown bank {a['bank_id']}")