"""Police notices: read the messy text, check it against the transactions, match it or send it back (D-41).

Notices reach the nodal desk as e-mails and scanned letters, not as tidy fields, and many are wrong or incomplete:
a mistyped UTR, a different amount, no legal provision, a demand to freeze the whole account. Acting on a wrong
notice freezes the wrong money; ignoring it loses the golden hour. For each new notice the agent:

  1. reads it    the local model extracts the fields (JSON); every value it gives must be found in the notice
                 itself, or the fixed parser's value is used. With the model off, the parser reads it alone
  2. checks it   in Exasol: is the UTR a payment into this bank, does the amount match, the date, is a legal
                 provision cited; if the UTR is not found, the nearest UTR received that day (EDIT_DISTANCE)
  3. decides     fixed rules: everything checks out -> LINKED to its ticket (or NO_TICKET if none has arrived);
                 any problem -> SENT_BACK
  4. writes      for a sent-back notice, the letter to the police listing each problem and the nearest match; the
                 model may word it, with every number and UTR checked against the facts

Nothing here holds money: holds come from tickets. A notice demanding a whole-account freeze is noted for the
officer, who alone can approve one.

    python -m pipeline.notices                # read every new notice (fixed parser)
    python -m pipeline.notices --mode llm     # let the local model read them
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from decimal import Decimal

from pipeline.explain import inr

FIELDS = ("utr", "amount_inr", "txn_date", "legal_basis", "authority", "notice_ref", "ack_no")
MONTHS = {m: i for i, m in enumerate(
    ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"], 1)}
WORDS = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8, "nine": 9,
         "ten": 10, "twenty": 20, "fifty": 50}
SCALE = {"thousand": 1000, "lakh": 100000, "lakhs": 100000, "crore": 10000000, "crores": 10000000}

UTR = re.compile(r"\b((?:IMPS|NEFT|RTGS|UPI|IFT|ATM|AMLW)[A-Z]?\d{10,20})\b")
UTR_LABELLED = re.compile(r"(?:UTR|txn\.?\s*ref|transaction\s*(?:ref|id))\.?\s*(?:no\.?|number)?\s*[:#-]?\s*"
                          r"([A-Z0-9]{10,22})\b", re.I)
ACK = re.compile(r"\back(?:nowledge?ment)?\.?\s*(?:no\.?|number)?\s*[:#-]?\s*(\d{14})\b", re.I)
RUPEES = re.compile(r"(?:Rs\.?|INR|₹)\s*([\d,]+(?:\.\d{1,2})?)", re.I)
RUPEE_WORDS = re.compile(r"\b(" + "|".join(WORDS) + r")\s+(" + "|".join(SCALE) + r")\b", re.I)
DATE_NUM = re.compile(r"\b(\d{1,2})[./-](\d{1,2})[./-](\d{2}|\d{4})\b")
DATE_WORD = re.compile(r"\b(\d{1,2})(?:st|nd|rd|th)?\s+(" + "|".join(MONTHS) + r")[a-z]*\.?,?\s+(\d{4})\b", re.I)
LEGAL = re.compile(r"((?:u/s|sec(?:tion)?\.?|S\.)\s*\d+[A-Z]?(?:\s*(?:r/w|read with)\s*(?:S\.|sec(?:tion)?\.?)?\s*"
                   r"\d+[A-Z]?)*\s*(?:BNSS|BNS|CrPC|IT Act))", re.I)
AUTHORITY = re.compile(r"((?:Cyber\s+(?:Crime\s+)?(?:Police\s+Station|PS|Cell)|Cyber\s+Police(?:\s+Station)?)"
                       r"(?:\s*,)?(?:\s+[A-Z][a-z]+){0,2})")
REF = re.compile(r"\bref(?:erence)?\.?\s*(?:no\.?)?\s*[:#-]?\s*([A-Z]{2,}(?:/[A-Z0-9-]+){2,})", re.I)
FULL_FREEZE = re.compile(r"\b(?:freez\w*|block\w*)\b[^.]{0,40}?\b(?:account|a/c)\b", re.I)


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", s or "").strip().lower()


def _amount(text: str) -> Decimal:
    return Decimal(text.replace(",", ""))


def amounts(text: str) -> list[Decimal]:
    out = [_amount(m.group(1)) for m in RUPEES.finditer(text) if m.group(1).strip(",")]
    out += [Decimal(WORDS[m.group(1).lower()] * SCALE[m.group(2).lower()]) for m in RUPEE_WORDS.finditer(text)]
    return out


def dates(text: str) -> list[date]:
    out = []
    for d, m, y in DATE_NUM.findall(text):
        y = int(y) + (2000 if len(y) == 2 else 0)
        try:
            out.append(date(y, int(m), int(d)))
        except ValueError:
            pass
    for d, mon, y in DATE_WORD.findall(text):
        try:
            out.append(date(int(y), MONTHS[mon.lower()[:3]], int(d)))
        except ValueError:
            pass
    return out


def parse(text: str) -> dict:
    """The fixed parser: what a careful clerk would pick out. Absent fields are None."""
    ack = ACK.search(text)
    labelled = [m.group(1) for m in UTR_LABELLED.finditer(text) if not (ack and m.group(1) == ack.group(1))]
    utrs = labelled or [m.group(1) for m in UTR.finditer(text)]
    found = amounts(text)
    when = dates(text)
    legal = LEGAL.search(text)
    auth = AUTHORITY.search(text)
    ref = REF.search(text)
    return {"utr": utrs[0].upper() if utrs else None,
            "amount_inr": found[0] if found else None,
            "txn_date": when[0] if when else None,
            "legal_basis": " ".join(legal.group(1).split()) if legal else None,
            "authority": " ".join(auth.group(1).split()).rstrip(",") if auth else None,
            "notice_ref": ref.group(1) if ref else None,
            "ack_no": ack.group(1) if ack else None}


SYSTEM_READ = (
    "You read a police notice sent to a bank about a cyber-fraud payment. Reply with JSON only, with these keys: "
    "utr (the transaction reference), amount_inr (number), txn_date (YYYY-MM-DD), legal_basis (the provision "
    "cited, as written), authority (police unit), notice_ref (the notice's reference number), ack_no (the 14-digit "
    "complaint acknowledgement). Use null for anything the notice does not say. Copy values exactly; never guess.")


def model_read(text: str, llm) -> dict:
    raw = llm.write(SYSTEM_READ, text)
    m = re.search(r"\{.*\}", raw, re.S)
    if not m:
        raise ValueError("no JSON in the reply")
    return json.loads(m.group(0))


def ground(model: dict, parsed: dict, text: str) -> tuple[dict, dict]:
    """Keep a model value only if the notice itself bears it out; otherwise the parser's. Returns (fields, source)."""
    out, source = {}, {}
    plain = _norm(text)
    found_amounts = set(amounts(text))
    found_dates = set(dates(text))
    for k in FIELDS:
        v = model.get(k)
        ok = False
        if v not in (None, "", "null"):
            if k == "amount_inr":
                try:
                    v = Decimal(str(v).replace(",", ""))
                    ok = v in found_amounts
                except ArithmeticError:
                    ok = False
            elif k == "txn_date":
                try:
                    v = date.fromisoformat(str(v)[:10])
                    ok = v in found_dates
                except ValueError:
                    ok = False
            else:
                v = " ".join(str(v).split())
                ok = _norm(v) in plain
                if k == "utr":
                    v = v.upper()
        out[k], source[k] = (v, "model") if ok else (parsed[k], "parser" if parsed[k] is not None else "none")
    return out, source


# ---------------------------------------------------------------- checks and decision (pure)

@dataclass
class Facts:
    """What Exasol says about the notice's UTR."""
    found: bool = False
    own_bank: bool = False
    amount_inr: Decimal | None = None
    ts: str | None = None
    case_id: str | None = None
    near: list[dict] = field(default_factory=list)      # [{"utr", "amount_inr", "ts"}], closest first


def decide(f: dict, facts: Facts, bank_name: str, asks_full_freeze: bool) -> tuple[str, list[str], list[str]]:
    """(status, problems, notes). Any problem sends the notice back; nothing is held on a notice alone."""
    problems, notes = [], []
    if not f["utr"]:
        problems.append("The notice gives no UTR (transaction reference), so the payment cannot be identified.")
    elif not facts.found:
        if facts.near:
            n = facts.near[0]
            problems.append(f"UTR {f['utr']} is not found at {bank_name}. The nearest match received that day is "
                            f"{n['utr']}: {inr(n['amount_inr'])} at {n['ts'][11:16]} on {n['ts'][:10]}. "
                            "Please confirm the UTR.")
            if f["amount_inr"] is not None and Decimal(str(f["amount_inr"])) != Decimal(str(n["amount_inr"])):
                problems.append(f"The notice states {inr(f['amount_inr'])}; that nearest match is "
                                f"{inr(n['amount_inr'])}. Please confirm the amount.")
        else:
            problems.append(f"UTR {f['utr']} is not found at {bank_name}, and no payment received that day comes "
                            "close; the notice may be meant for another bank.")
    elif not facts.own_bank:
        problems.append(f"UTR {f['utr']} is a payment into an account at another bank; please send the notice there.")
    if f["amount_inr"] is None:
        problems.append("The notice states no amount to hold.")
    elif facts.found and facts.amount_inr is not None and Decimal(str(f["amount_inr"])) != facts.amount_inr:
        problems.append(f"The notice states {inr(f['amount_inr'])}; the transaction with that UTR is "
                        f"{inr(facts.amount_inr)}.")
    if f["txn_date"] and facts.found and facts.ts and str(f["txn_date"]) != facts.ts[:10]:
        problems.append(f"The notice dates the payment {f['txn_date']}; the transaction is dated {facts.ts[:10]}.")
    if not f["legal_basis"]:
        problems.append("The notice cites no legal provision (for example S.94 BNSS).")
    if asks_full_freeze:
        notes.append("The notice asks for the whole account to be frozen. Under the SOP the bank holds the amount "
                     "traced to the victim; a whole-account freeze is the officer's decision, with written reasons.")
    if problems:
        return "SENT_BACK", problems, notes
    return ("LINKED" if facts.case_id else "NO_TICKET"), problems, notes


def letter(n: dict, problems: list[str], notes: list[str], bank_name: str) -> str:
    """The send-back letter: what is wrong, item by item, and that a hold follows a corrected notice."""
    when = f"{n['received_at'][11:16]} on {n['received_at'][:10]}"
    ref = n.get("notice_ref")
    subject = f"Your notice {ref}" if ref else f"Your notice of {when}"
    lines = [f"To: {n['sent_from']}",
             f"Subject: {subject}: details needed before a hold can be placed",
             "",
             "Sir/Madam,",
             f"We received your notice{' ' + ref if ref else ''} by {n['channel']} at {when}. We cannot act on it "
             "as it stands:"]
    lines += [f"{i}. {p}" for i, p in enumerate(problems, 1)]
    lines += [""] + notes + ([""] if notes else [])
    lines += ["Please send a corrected notice citing the UTR, the amount and the legal provision. The hold will be "
              "placed as soon as it arrives, and the trail details and KYC will follow.",
              "",
              f"Nodal Officer, {bank_name}"]
    return "\n".join(lines)


SYSTEM_WRITE = (
    "Rewrite this letter from a bank's nodal officer to the police so it reads clearly and politely. Keep every "
    "numbered point, every UTR, amount, date and time exactly as written; add nothing. Keep the To and Subject lines.")


def word(text: str, llm) -> tuple[str, str]:
    """The model's wording if every number and reference survives; else the template."""
    if llm is None:
        return text, "template"
    from pipeline.reasons import grounded

    try:
        written = llm.write(SYSTEM_WRITE, text).strip()
    except Exception:
        return text, "template"
    written = tidy(written, text)
    refs = set(re.findall(r"[A-Z]{3,}\d{6,}", written))
    if written and grounded(written, text) and refs <= set(re.findall(r"[A-Z]{3,}\d{6,}", text)):
        return written, "model"
    return text, "template"


# Characters models emit that the template never uses: curly quotes, invisible joiners, no-break spaces.
PLAIN = str.maketrans({"\u2018": "'", "\u2019": "'", "\u02bc": "'", "\u201c": '"', "\u201d": '"',
                       "\u00a0": " ", "\u202f": " ", "\u200b": None, "\u200c": None, "\u200d": None,
                       "\u2060": None, "\u00ad": None, "\ufeff": None})


def tidy(written: str, template: str) -> str:
    """Clean the model's letter: no trailing spaces, no runs of blank lines, and two template words it ran
    together ("officer'sdecision") split again."""
    written = written.translate(PLAIN)
    lines = [ln.rstrip() for ln in written.splitlines()]
    out = re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip()
    words = template.split()
    for a, b in zip(words, words[1:], strict=False):
        glued = a + b
        if glued not in template:
            out = out.replace(glued, f"{a} {b}")
    return out


# ---------------------------------------------------------------- Exasol

def _d(v) -> Decimal | None:
    return None if v is None else Decimal(str(v))


def lookup(conn, bank: str, notice_id: str, f: dict, received_at: str) -> Facts:
    """The UTR's payment (a join, so Exasol uses its index), its ticket, and near UTRs received around that day."""
    facts = Facts()
    if f["utr"]:
        row = conn.execute(
            "SELECT SUM(t.amount_inr), MIN(t.ts), MAX(CASE WHEN a.bank_id = {b} THEN 1 ELSE 0 END) "
            "FROM PH.NOTICE n JOIN PH.TXN t ON t.utr = n.utr LEFT JOIN PH.ACCOUNT a ON a.account_id = t.to_account "
            "WHERE n.notice_id = {n}", {"b": bank, "n": notice_id}).fetchone()
        if row and row[1] is not None:
            facts.found, facts.amount_inr, facts.ts = True, _d(row[0]), str(row[1])[:19]
            facts.own_bank = bool(int(row[2] or 0))
            facts.case_id = conn.execute("SELECT MIN(case_id) FROM PH.COMPLAINT WHERE utr = {u}",
                                         {"u": f["utr"]}).fetchval()
    if not facts.found:
        day = f["txn_date"] or date.fromisoformat(received_at[:10])
        lo = datetime.combine(day - timedelta(days=1), datetime.min.time()).strftime("%Y-%m-%d %H:%M:%S")
        hi = datetime.combine(day + timedelta(days=2), datetime.min.time()).strftime("%Y-%m-%d %H:%M:%S")
        if f["utr"]:
            rows = conn.execute(
                "SELECT t.utr, t.amount_inr, t.ts FROM PH.TXN t JOIN PH.ACCOUNT a ON a.account_id = t.to_account "
                "WHERE a.bank_id = {b} AND t.ts >= TO_TIMESTAMP({lo}, 'YYYY-MM-DD HH24:MI:SS') "
                "AND t.ts < TO_TIMESTAMP({hi}, 'YYYY-MM-DD HH24:MI:SS') AND EDIT_DISTANCE(t.utr, {u}) <= 2 "
                "ORDER BY EDIT_DISTANCE(t.utr, {u}), t.ts LIMIT 3",
                {"b": bank, "lo": lo, "hi": hi, "u": f["utr"]}).fetchall()
        elif f["amount_inr"] is not None:
            rows = conn.execute(
                "SELECT t.utr, t.amount_inr, t.ts FROM PH.TXN t JOIN PH.ACCOUNT a ON a.account_id = t.to_account "
                "WHERE a.bank_id = {b} AND t.ts >= TO_TIMESTAMP({lo}, 'YYYY-MM-DD HH24:MI:SS') "
                "AND t.ts < TO_TIMESTAMP({hi}, 'YYYY-MM-DD HH24:MI:SS') AND t.amount_inr = {amt!d} "
                "ORDER BY t.ts LIMIT 3",
                {"b": bank, "lo": lo, "hi": hi, "amt": str(f["amount_inr"])}).fetchall()
        else:
            rows = []
        facts.near = [{"utr": u, "amount_inr": _d(a), "ts": str(t)[:19]} for u, a, t in rows]
    return facts


def read_one(conn, bank: str, n: dict, llm=None, bank_name: str = "Bank X") -> dict:
    text = n["raw_text"]
    parsed = parse(text)
    fields, source, read_by = parsed, {k: ("parser" if parsed[k] is not None else "none") for k in FIELDS}, "parser"
    if llm is not None:
        try:
            fields, source = ground(model_read(text, llm), parsed, text)
            read_by = "model" if any(s == "model" for s in source.values()) else "parser"
        except Exception:                                   # unreachable or confused model: the parser's reading
            fields, read_by = parsed, "parser"
    asks = bool(FULL_FREEZE.search(text))
    conn.execute(
        "UPDATE PH.NOTICE SET utr = {u}, amount_inr = {amt}, txn_date = {d}, legal_basis = {lb}, authority = {au}, "
        "notice_ref = {ref}, ack_no = {ack}, asks_full_freeze = {ff!r}, read_by = {rb}, field_sources = {fs} "
        "WHERE notice_id = {n}",
        {"u": fields["utr"], "amt": None if fields["amount_inr"] is None else str(fields["amount_inr"]),
         "d": None if fields["txn_date"] is None else str(fields["txn_date"]), "lb": fields["legal_basis"],
         "au": fields["authority"], "ref": fields["notice_ref"], "ack": fields["ack_no"],
         "ff": "TRUE" if asks else "FALSE", "rb": read_by, "fs": json.dumps(source), "n": n["notice_id"]})
    facts = lookup(conn, bank, n["notice_id"], fields, n["received_at"])
    status, problems, notes = decide(fields, facts, bank_name, asks)
    reply, reply_by = (None, None)
    if status == "SENT_BACK":
        reply, reply_by = word(letter({**n, **fields}, problems, notes, bank_name), llm)
    read_at = (datetime.strptime(n["received_at"][:19], "%Y-%m-%d %H:%M:%S") + timedelta(minutes=1)).strftime(
        "%Y-%m-%d %H:%M:%S")
    conn.execute(
        "UPDATE PH.NOTICE SET notice_status = {s}, case_id = {c}, problems = {p}, agent_notes = {nt}, "
        "suggestion_utr = {sg}, reply_text = {r}, reply_by = {rb}, read_at = TO_TIMESTAMP({t}, "
        "'YYYY-MM-DD HH24:MI:SS'), stmt_id = CURRENT_STATEMENT WHERE notice_id = {n}",
        {"s": status, "c": facts.case_id, "p": "\n".join(problems) or None, "nt": "\n".join(notes) or None,
         "sg": facts.near[0]["utr"] if facts.near and status == "SENT_BACK" else None, "r": reply, "rb": reply_by,
         "t": read_at, "n": n["notice_id"]})
    return {"notice_id": n["notice_id"], "status": status, "case_id": facts.case_id, "problems": problems,
            "read_by": read_by}


def run_notices(conn, bank: str, mode: str = "pipeline", llm=None) -> dict:
    """Read every new notice. Re-reading is safe: run with all=True after a reset to read them again."""
    if mode == "llm" and llm is None:
        from pipeline.llm import client

        llm = client()
    if mode != "llm":
        llm = None
    bank_name = conn.execute("SELECT bank_name FROM PH.BANK WHERE bank_id = {b}", {"b": bank}).fetchval() or bank
    new = conn.execute("SELECT notice_id, received_at, channel, sent_from, raw_text FROM PH.NOTICE "
                       "WHERE viewing_bank_id = {b} AND notice_status = 'NEW' ORDER BY received_at",
                       {"b": bank}).fetchall()
    out = [read_one(conn, bank, {"notice_id": i, "received_at": str(r)[:19], "channel": ch, "sent_from": fr,
                                 "raw_text": t}, llm, bank_name) for i, r, ch, fr, t in new]
    count = lambda s: sum(1 for x in out if x["status"] == s)  # noqa: E731
    return {"read": len(out), "linked": count("LINKED"), "no_ticket": count("NO_TICKET"),
            "sent_back": count("SENT_BACK")}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", choices=["pipeline", "llm"], default="pipeline")
    ap.add_argument("--bank")
    ap.add_argument("--again", action="store_true", help="read every notice again, not only new ones")
    args = ap.parse_args(argv)
    from pipeline.db import connect, settings

    bank = args.bank or settings()["own_bank_id"]
    conn = connect()
    try:
        if args.again:
            conn.execute("UPDATE PH.NOTICE SET notice_status = 'NEW' WHERE viewing_bank_id = {b} AND sent_at IS NULL",
                         {"b": bank})
        r = run_notices(conn, bank, args.mode)
        sent = conn.execute("SELECT COUNT(*) FROM PH.NOTICE WHERE viewing_bank_id = {b} AND sent_at IS NOT NULL",
                            {"b": bank}).fetchval()
        print(f"read {r['read']} notices: {r['linked']} matched a ticket, {r['no_ticket']} valid without a ticket, "
              f"{r['sent_back']} sent back" + (f"; {sent} already answered, not read again" if int(sent) else ""))
        for nid, status, case, sources, sent_by, reply in conn.execute(
                "SELECT notice_id, notice_status, case_id, field_sources, sent_by, reply_text FROM PH.NOTICE "
                "WHERE viewing_bank_id = {b} ORDER BY received_at", {"b": bank}).fetchall():
            print(f"\n{nid}  {status}" + (f"  -> {case}" if case else "") + (f"  (letter sent by {sent_by})"
                                                                              if sent_by else ""))
            by: dict[str, list[str]] = {}
            for field, src in (json.loads(sources) if sources else {}).items():
                by.setdefault(src, []).append(field)
            print("  read by " + "; ".join(f"{src}: {', '.join(fs)}" for src, fs in sorted(by.items())))
            if reply:
                print("  " + reply.replace("\n", "\n  "))
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())