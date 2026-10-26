"""Written reasons (Module 4; docs/DESIGN.md, "Written reasons"). The hold amount is what courts scrutinise, so every
hold carries its reasons from the moment it is proposed, and the grievance officer later starts from them.

Four rules, so a judge's "what if the model invents a reason?" has an answer:
  1. The rules decide the reasons; the model only words them. `structured` reads, from Exasol, the rule that fired,
     the signals behind it, the numbers and the case against. That structured list is the reason.
  2. Claims are checked, not just numbers. A model draft is kept only if every sentence points back to an item it
     was given, every number in it is in the item it cites, no sentence passes a verdict or overstates, and the case
     against is there. Otherwise the fixed template is used. Citations come from the rule's fixed list
     (config/rules.yaml legal_basis), never from the model.
  3. The case against is always included: for narrowing, what still points at the account; for a full freeze,
     what reads like an ordinary account. It keeps the officer's review real.
  4. The model only where judgment was used. A mandatory hold gets a fixed sentence (notice, transaction, amount,
     section); only narrow and widen proposals are worded by the model.
The account holder is told only which authority, which notice and how much is held: never the evidence.
"""
from __future__ import annotations

import json
import re
from decimal import Decimal
from pathlib import Path

import yaml

from pipeline.explain import inr

ROOT = Path(__file__).resolve().parents[1]
RULES = yaml.safe_load((ROOT / "config" / "rules.yaml").read_text(encoding="utf-8"))
MODEL_WORDED = ("PROPOSE_NARROW", "PROPOSE_FULL_FREEZE")
CITATION = {"MANDATORY_HOLD": "hold", "HOLD_MARKER": "hold", "WATCH_HOLD": "hold", "PROPOSE_NARROW": "narrow",
            "PROPOSE_FULL_FREEZE": "freeze", "RECORD_EXIT": "trail", "ONWARD_ENTRY": "trail", "KYC_PACKET": "trail",
            "SEND_BACK": "send_back", "UNMATCHED_NOTICE": "send_back", "PROPOSE_RELEASE": "release"}

SYSTEM = (
    "You word the reasons for a bank officer's decision on one bank account, for the officer to check and, if "
    "challenged, a court. You are given the proposal, the numbers, the reasons for it (ids S1, S2, ...) and the case "
    "against it (ids C1, C2, ...). Write 3 to 6 short plain sentences. Every sentence must say which given items it "
    "rests on. Use only those items; copy every number exactly as written; add no number, no estimate, no legal "
    "citation and no opinion: never call the account or its holder a mule, fraudulent or criminal, and never "
    "overstate (no 'clearly', 'definitely', 'proves'). At least one sentence must state the case against. "
    'Answer with JSON only: {"sentences": [{"text": "...", "cites": ["S1"]}]}')

NUMBER = re.compile(r"\d[\d,]*(?:\.\d+)?")
# Words that pass a verdict on the holder. The facts never use them; the officer and the court decide guilt.
VERDICT = re.compile(r"\b(fraudulent\w*|fraudster\w*|criminal\w*|guilt\w*|scam\w*|illegal\w*|illicit|launder\w*|"
                     r"culprit\w*|crook\w*|mules?)\b", re.I)
# Words that claim more than a signal can carry ("a clear pattern" when one weak signal fired).
OVERSTATE = re.compile(r"\b(clear(ly)?|obvious(ly)?|definite(ly)?|certain(ly)?|undoubted(ly)?|prove[sn]?|proof|"
                       r"conclusive(ly)?|beyond doubt|without doubt|always|unquestionabl\w*)\b", re.I)


def numbers(text: str) -> set[str]:
    """Every number in a text, normalised: '1,19,500' -> '119500', '50,000.00' -> '50000', '05:10' -> {'05', '10'}."""
    out = set()
    for m in NUMBER.finditer(text):
        v = m.group(0).replace(",", "")
        if "." in v:
            v = v.rstrip("0").rstrip(".")
        out.add(v.lstrip("0") or "0")
    return out


def verdicts(text: str) -> set[str]:
    return {m.group(1).lower() for m in VERDICT.finditer(text)}


def citation(action_code: str) -> str:
    return RULES["legal_basis"][CITATION.get(action_code, "hold")]


# ---------------------------------------------------------------- fixed sentences (no model)

def mandatory_text(c: dict, utr: str, amount, action_code: str = "MANDATORY_HOLD") -> str:
    """The fixed sentence behind a mandatory hold: notice, transaction, amount, section."""
    what = "a marker to catch later credits up to" if action_code == "HOLD_MARKER" else "held (amount only):"
    notice = c.get("ordering_reference") or c.get("case_id")
    authority = c.get("ordering_authority") or "the police"
    return (f"Notice {notice} from {authority}, transaction {utr}: {what} {inr(amount)} under "
            f"{c.get('notice_legal_basis') or 'S.94 BNSS'}. The rules require this hold; the agent placed it.")


def holder_message(c: dict, account_id: str, amount, kind: str = "LIEN", at: str | None = None) -> str:
    """What the account holder is told: the authority, the notice and how much is held. Never the evidence
    (telling a mule why it is suspected warns it off)."""
    masked = "XXXX" + account_id[-4:] if len(account_id) > 4 else account_id
    notice = c.get("ordering_reference") or c.get("case_id")
    authority = c.get("ordering_authority") or "the police"
    basis = c.get("notice_legal_basis") or "S.94 BNSS"
    if kind == "ACCOUNT_FREEZE":
        return (f"Debits from your account {masked} are frozen on the instruction of {authority}, notice {notice}, "
                f"under {basis}. Money can still be paid into the account.")
    if kind == "RELEASED":
        return f"The hold on your account {masked} (notice {notice}, {authority}) has been lifted."
    if kind == "UNFROZEN":
        return f"The freeze on your account {masked} (notice {notice}, {authority}) has been lifted."
    since = ""
    if at:
        months = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
        since = f" from {at[11:16]} on {int(at[8:10])} {months[int(at[5:7]) - 1]} {at[:4]}"
    return (f"{inr(amount)} in your account ending {account_id[-4:]} is on hold{since}, on a notice from {authority} "
            f"({notice}), under {basis}. The rest of your balance is free to use. To contest the hold, contact the "
            f"police station on the notice or visit your branch with ID.")


# ---------------------------------------------------------------- the structured reasons (rules engine output)

def _d(v) -> Decimal:
    return Decimal(str(v)) if v is not None else Decimal(0)


def _signals(conn, case_id: str, account_id: str) -> list[dict]:
    rows = conn.execute(
        "SELECT signal_name, signal_value, mule_likeness, sentence, mule_rank, honest_rank FROM ACCOUNT_SIGNALS "
        "WHERE case_id = {c} AND account_id = {a}", {"c": case_id, "a": account_id}).fetchall()
    return [{"name": n, "value": v, "ml": float(m or 0), "text": s, "mule_rank": int(mr or 99),
             "honest_rank": int(hr or 99)} for n, v, m, s, mr, hr in rows]


def cross_ticket(conn, account_id: str, case_id: str | None = None) -> list[str]:
    """The kept part of idea 8: does the account appear in other tickets, and is it connected to held accounts by
    its own behaviour (money in and out, again and again)? Never a network, never a note to the police."""
    out = []
    others = conn.execute(
        "SELECT COUNT(DISTINCT e.case_id), MIN(e.ts), MAX(e.ts) FROM TRACE_EDGE e "
        "WHERE e.to_account = {a} AND e.case_id <> {c}", {"a": account_id, "c": case_id or "-"}).fetchone()
    if others and int(others[0] or 0) > 0:
        n = int(others[0]) + (1 if case_id else 0)
        out.append(f"Its money arrived on the trails of {n} tickets at this bank.")
    held = [a for (a,) in conn.execute(
        "SELECT DISTINCT x.acc FROM (SELECT t.from_account AS acc FROM TXN_LIVE t WHERE t.to_account = {a} "
        "UNION SELECT t.to_account FROM TXN_LIVE t WHERE t.from_account = {a}) x "
        "JOIN ACTIVE_HOLD h ON h.account_id = x.acc WHERE x.acc <> {a} ORDER BY 1", {"a": account_id}).fetchall()]
    if held:
        names = dict(conn.execute("SELECT account_id, COALESCE(holder_name, account_id) FROM ACCOUNT "
                                  "WHERE account_id IN ({h})", {"h": held}).fetchall())
        listed = ", ".join(names.get(a, a) for a in held[:4])
        out.append(f"It has exchanged money with {len(held)} account{'s' if len(held) != 1 else ''} that "
                   f"{'are' if len(held) != 1 else 'is'} held now ({listed}).")
    return out


def declined_lines(conn, account_id: str) -> list[str]:
    """Refused attempts on the account, from the declined-attempts feed."""
    out = []
    for at, amount, channel, ident, _intended, bank in conn.execute(
            "SELECT d.attempted_at, d.amount_inr, d.channel, d.exit_identifier, d.intended_account, b.bank_name "
            "FROM DECLINED_ATTEMPT d LEFT JOIN BANK b ON b.bank_id = d.intended_bank_id "
            "WHERE d.account_id = {a} ORDER BY d.attempted_at", {"a": account_id}).fetchall():
        when = str(at)[11:16]
        if channel in ("ATM", "CASH", "CRYPTO"):
            where = f"at {ident}" if ident else f"by {channel}"
            out.append(f"At {when} it tried to withdraw {inr(amount)} {where}; the hold refused it.")
        else:
            to = f"an account at {bank}" if bank else "another account"
            out.append(f"At {when} it tried to send {inr(amount)} by {channel} to {to}; the hold refused it.")
    return out


def structured(conn, case_id: str, account_id: str, action_code: str, numbers_in: dict | None = None) -> dict:
    """The reasons as data, from the rules engine and the evidence step. Nothing here comes from a model."""
    acc = conn.execute("SELECT COALESCE(holder_name, account_id), account_type, holder_type, opened_on FROM ACCOUNT "
                       "WHERE account_id = {a}", {"a": account_id}).fetchone() or (account_id, None, None, None)
    name = acc[0]
    sig = _signals(conn, case_id, account_id)
    mule = sorted((s for s in sig if s["ml"] >= 0.5 and s["text"]), key=lambda s: s["mule_rank"])
    honest = sorted((s for s in sig if s["ml"] < 0.5 and s["text"]), key=lambda s: s["honest_rank"])
    nums = {k: str(v) for k, v in (numbers_in or {}).items() if v is not None}
    support, against = [], []
    if action_code == "PROPOSE_NARROW":
        support = [s["text"] for s in honest[:3]]
        against = [s["text"] for s in mule[:2]]
        for payer, amount in conn.execute(
                "SELECT e.from_account, SUM(e.amount_inr) FROM TRACE_EDGE e JOIN ACTIVE_HOLD h "
                "ON h.account_id = e.from_account WHERE e.case_id = {c} AND e.to_account = {a} "
                "GROUP BY e.from_account", {"c": case_id, "a": account_id}).fetchall():
            payer_name = conn.execute("SELECT COALESCE(holder_name, account_id) FROM ACCOUNT WHERE account_id = {p}",
                                      {"p": payer}).fetchval() or payer
            against.append(f"It received {inr(amount)} directly from account {payer_name}, which is held.")
        if not against:
            against.append("Narrowing releases money the lowest-balance rule counts as the holder's own; if that "
                           "rule is wrong for this account, the released money can leave.")
    elif action_code == "PROPOSE_FULL_FREEZE":
        support = [s["text"] for s in mule[:3]] + declined_lines(conn, account_id) + cross_ticket(conn, account_id,
                                                                                                    case_id)
        against = [s["text"] for s in honest[:2]]
        held = conn.execute("SELECT liens_inr FROM ACTIVE_HOLD WHERE account_id = {a}", {"a": account_id}).fetchval()
        if held:
            against.append(f"{inr(held)} is already held amount-only, so the reported money is protected without "
                           "freezing the rest of the account.")
    elif action_code == "UNMATCHED_NOTICE":
        support = list((numbers_in or {}).get("lines") or [])
        against = ["Holding on a transaction the notice did not name has no legal basis until the officer decides "
                   "it is the notice's transaction."]
    elif action_code == "PROPOSE_RELEASE":
        support = list((numbers_in or {}).get("lines") or [])
        against = ["Releasing lets the money leave; if the police direct a hold later, it may be gone."]
    return {
        "action_code": action_code, "account_id": account_id, "name": name, "case_id": case_id,
        "rule": RULES["officer"].get(action_code) or RULES["automatic"].get(action_code, ""),
        "citation": citation(action_code),
        "numbers": {k: v for k, v in nums.items() if k != "lines"},
        "support": [{"id": f"S{i}", "text": t} for i, t in enumerate(support, 1)],
        "against": [{"id": f"C{i}", "text": t} for i, t in enumerate(against, 1)],
    }


def lead(r: dict) -> str:
    """The proposal in one sentence, from the numbers (fixed)."""
    n, name = r["numbers"], r["name"]
    code = r["action_code"]
    if code == "PROPOSE_NARROW":
        return (f"Narrow the hold on {name} from {inr(n.get('held'))} to {inr(n.get('proposed'))}: only "
                f"{inr(n.get('proposed'))} of what is held can be traced to reported victims (lowest-balance rule).")
    if code == "PROPOSE_FULL_FREEZE":
        return (f"Freeze the whole account of {name} (balance {inr(n.get('balance'))}, {inr(n.get('held'))} "
                f"already held amount-only).")
    if code == "UNMATCHED_NOTICE":
        return (f"The ticket names no payment into {name} exactly; a payment of {inr(n.get('near_amount'))} at "
                f"{n.get('near_at', '')[11:16]} may be the one it means. Nothing is held.")
    if code == "PROPOSE_RELEASE":
        return f"Release the {inr(n.get('held'))} held on {name}: no police direction in 75 days."
    return ""


def template(r: dict) -> str:
    """The reasons in plain words, fixed order: proposal, why, case against, legal basis."""
    parts = [lead(r)]
    if r["support"]:
        parts.append("Why: " + " ".join(s["text"] for s in r["support"]))
    parts.append("Case against: " + " ".join(s["text"] for s in r["against"]))
    parts.append("Legal basis: " + r["citation"])
    return "\n".join(p for p in parts if p)


def claims_check(sentences: list[dict], r: dict) -> str | None:
    """Why a model draft is rejected, or None when it passes."""
    items = {s["id"]: s["text"] for s in r["support"] + r["against"]}
    allowed_everywhere = numbers(lead(r)) | {n for v in r["numbers"].values() for n in numbers(str(v))}
    if not sentences:
        return "empty draft"
    cited_against = False
    for s in sentences:
        text, cites = str(s.get("text", "")).strip(), s.get("cites") or []
        if not text:
            return "an empty sentence"
        if not cites:
            return f"a sentence points to no given reason: {text[:80]!r}"
        unknown = [c for c in cites if c not in items]
        if unknown:
            return f"a sentence cites reasons it was not given ({', '.join(map(str, unknown))})"
        cited_against |= any(str(c).startswith("C") for c in cites)
        extra = numbers(text) - allowed_everywhere - {n for c in cites for n in numbers(items[c])}
        if extra:
            return f"a number not in the reasons it cites ({', '.join(sorted(extra))}): {text[:80]!r}"
        if verdicts(text):
            return f"a verdict on the holder ({', '.join(sorted(verdicts(text)))})"
        if OVERSTATE.search(text):
            return f"overstates ({OVERSTATE.search(text).group(0)!r}): {text[:80]!r}"
    if r["against"] and not cited_against:
        return "the case against is missing"
    return None


def word(r: dict, llm=None) -> dict:
    """The draft the officer reads: the model's wording if it is on and passes the claims check, else the template.
    Returns {"text", "by", "note"} (note: why a model draft was rejected)."""
    fixed = template(r)
    if r["action_code"] not in MODEL_WORDED or llm is None:
        return {"text": fixed, "by": "template", "note": None}
    brief = json.dumps({"proposal": lead(r), "numbers": r["numbers"], "reasons_for": r["support"],
                        "case_against": r["against"]}, ensure_ascii=False)
    try:
        raw = llm.write(SYSTEM, brief)
        start, end = raw.find("{"), raw.rfind("}")
        sentences = json.loads(raw[start:end + 1])["sentences"]
    except Exception as exc:  # unreachable model, not JSON, wrong shape: the template stands
        return {"text": fixed, "by": "template", "note": f"model draft unusable: {str(exc)[:120]}"}
    note = claims_check(sentences, r)
    if note:
        return {"text": fixed, "by": "template", "note": f"model draft rejected: {note}"[:400]}
    body = " ".join(str(s["text"]).strip() for s in sentences)
    return {"text": f"{lead(r)}\n{body}\nLegal basis: {r['citation']}", "by": "model", "note": None}