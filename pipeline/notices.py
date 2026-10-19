"""Customer notices in English, Hindi and Tamil (FR-11).

Wording lives in templates/notices/<lang>.yaml; this module only fills it in. The facts (amounts, UTR, dates,
legal basis, authority) come from Exasol rows passed in by pipeline/officer.py. The victim's name, account and
details are never among the fields, so they cannot appear in a notice (SOP para 9.1(ii)).
"""
from __future__ import annotations

from pathlib import Path
from string import Template

import yaml

from pipeline.explain import inr

ROOT = Path(__file__).resolve().parents[1]
TEMPLATES = ROOT / "templates" / "notices"
LANGS = ("en", "hi", "ta")
FIELDS = ("holder_name", "bank_name", "notice_date", "hold_ref", "account_id", "txn_time", "txn_amount", "txn_utr",
          "hold_amount", "scope", "lane", "authority", "reference", "legal_basis", "respond_by",
          "grievance_officer", "grievance_contact", "signals")

LAYOUT = """$title
$bank_name · $notice_date · $reference_label $hold_ref

$dear $holder_name,

$intro

$what_label: $scope_text

$why_label: $reason

$who_label: $authority_text
$basis_label: $legal_basis

$do_label: $respond

$grievance_label: $grievance_officer, $grievance_contact

$closing
"""


def rupees(amount) -> str:
    return inr(amount).replace("Rs ", "₹")


def phrases(lang: str) -> dict:
    if lang not in LANGS:
        raise ValueError(f"no notice template for language {lang!r}; have {LANGS}")
    return yaml.safe_load((TEMPLATES / f"{lang}.yaml").read_text(encoding="utf-8"))


def render(lang: str, facts: dict) -> str:
    """facts: the FIELDS above. scope is 'amount' or 'account'; lane is 'A' or 'B'."""
    missing = [f for f in FIELDS if f not in facts]
    if missing:
        raise ValueError(f"notice is missing {missing}")
    p = phrases(lang)
    f = {k: ("" if v is None else str(v)) for k, v in facts.items()}

    def fill(key: str) -> str:
        return Template(p[key]).substitute(f)

    whole = facts["scope"] == "account"
    parts = {
        **{k: v for k, v in p.items() if k.endswith("_label") or k in ("title", "dear")},
        **f,
        "intro": fill("intro"),
        "scope_text": fill("scope_account" if whole else "scope_amount"),
        "reason": fill("reason_account" if whole else "reason_amount"),
        "authority_text": fill(f"authority_{facts['lane']}"),
        "respond": fill(f"respond_{facts['lane']}"),
        "closing": fill("closing_account" if whole else "closing_amount"),
    }
    return Template(LAYOUT).substitute(parts)