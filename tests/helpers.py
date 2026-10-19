"""Glue between case files (pipeline/cases.py) and the oracle (tests/oracle.py)."""
from pipeline.cases import Case
from tests.oracle import Txn, replay


def oracle_txns(case: Case) -> list[Txn]:
    return [
        Txn(t.txn_id, t.ts, t.from_account, t.to_account, t.amount_paise, t.victim_tag)
        for t in case.txns
    ]


def run(case: Case, rule: str, as_of: str | None = None):
    return replay(oracle_txns(case), rule, opening=case.opening_paise(), as_of=as_of)


def rupees(paise: int) -> float:
    return paise / 100