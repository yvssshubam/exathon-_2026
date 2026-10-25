"""Read case files (data/demo_case.yaml, data/extra_cases.yaml, data/loop_case.yaml) into plain rows.

The same rows feed the oracle tests and the Exasol loader, so the database and the oracle always see identical
inputs. No business logic lives here, apart from `bank_view`: what one bank can see of the synthetic world (its own
accounts' transactions), which is exactly what the loader puts in that bank's schema.
"""
from __future__ import annotations

from dataclasses import dataclass, field, replace
from decimal import Decimal
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
DEMO_CASE = ROOT / "data" / "demo_case.yaml"
EXTRA_CASES = ROOT / "data" / "extra_cases.yaml"
LOOP_CASE = ROOT / "data" / "loop_case.yaml"
EXIT_PREFIX = "EXIT:"
TICKET_KINDS = ("POLICE", "ONWARD", "IO_NOTICE")


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
class Request:
    """A payment a holder tries only because the money is held (PAYMENT_REQUEST)."""
    request_id: str
    ts: str
    from_account: str
    to_account: str
    amount_paise: int
    channel: str
    exit_identifier: str | None = None

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
    requests: list[Request] = field(default_factory=list)
    history: list[TxnRow] = field(default_factory=list)
    tool_as_of: str | None = None
    expected_libr: dict = field(default_factory=dict)
    expected_libr_bank_x: dict = field(default_factory=dict)
    expected_actions: dict = field(default_factory=dict)
    expected_morning: list = field(default_factory=list)
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

    def bank_of(self, account_id: str) -> str | None:
        a = next((a for a in self.accounts if a["account_id"] == account_id), None)
        return a["bank_id"] if a else None

    def own_bank_ids(self) -> set[str]:
        return {b["bank_id"] for b in self.banks if b["is_own_bank"]}

    def complaint(self, case_id: str) -> dict:
        return next(c for c in self.complaints if c["case_id"] == case_id)

    def police_tickets(self) -> list[dict]:
        """Every ticket the police send, in the order they reach the banks."""
        return sorted(self.complaints, key=lambda c: (str(c["reported_at"]), c["case_id"]))


def touches(t: TxnRow, own: set[str]) -> bool:
    """A bank sees a transaction when one of its own accounts is on either side of it."""
    return t.from_account in own or t.to_account in own


def bank_view(case: Case, bank: str) -> Case:
    """What `bank` can see of the case: the transactions that touch its own accounts. The opening balances of
    other banks' accounts are not its business either, and its own tickets are those naming its own accounts."""
    own = {a["account_id"] for a in case.accounts if a["bank_id"] == bank}
    return replace(
        case,
        txns=[t for t in case.txns if touches(t, own)],
        history=[t for t in case.history if touches(t, own)],
        requests=[r for r in case.requests if r.from_account in own],
        complaints=[c for c in case.complaints if case.bank_of(c.get("beneficiary_account") or "") == bank],
        accounts=[a if a["bank_id"] == bank else {**a, "opening_balance_inr": None} for a in case.accounts],
        banks=[{**b, "is_own_bank": b["bank_id"] == bank} for b in case.banks],
    )


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


def _request(raw: dict) -> Request:
    return Request(raw["request_id"], raw["ts"], raw["from"], raw["to"], paise(raw["amount_inr"]), raw["channel"],
                   raw.get("exit_identifier"))


def load_case(path: Path = DEMO_CASE) -> Case:
    raw = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    complaints = raw.get("complaints") or [raw["complaint"]]
    for c in complaints:
        c.setdefault("case_id", raw["case_id"])
        c.setdefault("ticket_kind", "POLICE")
    case = Case(
        case_id=raw["case_id"],
        complaints=complaints,
        banks=raw.get("banks", []),
        accounts=raw["accounts"],
        txns=[_txn(t) for t in raw["transactions"]],
        requests=[_request(r) for r in raw.get("payment_requests") or []],
        history=[_txn(t) for t in raw.get("history_transactions") or []],
        tool_as_of=raw.get("tool_as_of"),
        expected_libr=raw.get("expected_libr", {}),
        expected_libr_bank_x=raw.get("expected_libr_bank_x", {}),
        expected_actions=raw.get("expected_actions", {}),
        expected_morning=raw.get("expected_morning", []),
        expected_compare=raw.get("expected_compare", {}),
        today=raw.get("today", {}),
    )
    _check(case)
    return case


def _check(case: Case) -> None:
    """Fail loudly on malformed case files rather than producing quietly wrong numbers."""
    known = {a["account_id"] for a in case.accounts}
    ids = [t.txn_id for t in case.txns + case.history] + [r.request_id for r in case.requests]
    if len(ids) != len(set(ids)):
        raise ValueError(f"{case.case_id}: duplicate txn_id or request_id")
    for t in case.txns + case.history:
        for acc in (t.from_account, t.to_account):
            if not acc.startswith(EXIT_PREFIX) and acc not in known:
                raise ValueError(f"{case.case_id}: {t.txn_id} uses unknown account {acc}")
        if t.is_exit and not t.exit_identifier:
            raise ValueError(f"{case.case_id}: exit {t.txn_id} has no exit_identifier (FR-05)")
    for t in case.history:
        if t.ts >= "2026-10-18":
            raise ValueError(f"{case.case_id}: history {t.txn_id} must end before the case day")
    for r in case.requests:
        for acc in (r.from_account, r.to_account):
            if not acc.startswith(EXIT_PREFIX) and acc not in known:
                raise ValueError(f"{case.case_id}: request {r.request_id} uses unknown account {acc}")
    for c in case.complaints:
        if c["ticket_kind"] not in TICKET_KINDS:
            raise ValueError(f"{c['case_id']}: ticket_kind must be one of {TICKET_KINDS}")
        if c.get("beneficiary_account") and c["beneficiary_account"] not in known:
            raise ValueError(f"{c['case_id']}: beneficiary {c['beneficiary_account']} is not an account in the file")
    bank_ids = {b["bank_id"] for b in case.banks}
    for a in case.accounts:
        if bank_ids and a["bank_id"] not in bank_ids:
            raise ValueError(f"{case.case_id}: account {a['account_id']} at unknown bank {a['bank_id']}")