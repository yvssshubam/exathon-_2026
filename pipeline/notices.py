"""Police notices that reach the bank as e-mails, letters or PDFs: read them into a ticket the agent works
(DECISIONS D-52).

Most tickets reach the bank through the portal as tidy fields. An investigating officer's notice often does not: it
comes by e-mail or as a letter, and the details are in prose ("txn ref IMPS2026... dt 18/10/26 for Rs. Two Lakh
only, u/s 94 BNSS"). For each new notice in the bank's inbox (NOTICE_INBOX) the agent:

  1. reads it      the text of an e-mail (.eml), a text file, or a PDF with a text layer (a scanned image needs OCR:
                   pytesseract and Tesseract, if installed; otherwise it goes to the officer unread)
  2. extracts      UTR, amount, date and time, receiving account, legal provision, police unit, reference and the
                   complaint's acknowledgement number. A fixed parser always runs; the local model may read it too,
                   but a value it gives is kept only if the notice itself bears it out, else the parser's
  3. registers     the notice as an investigating officer's ticket in the bank's own queue. The free text goes in
                   the ticket's remarks, which the agent treats as data, never as an instruction
  4. hands over    the agent then works it like any ticket: intake checks every field against the payment in
                   Exasol (UTR, amount, date and time, account, provision, acknowledgement); the same payment as an
                   earlier ticket is linked, never held twice; any mismatch sends it back with a letter that names
                   each one ("the notice says Rs 1,25,800; the payment with that UTR is Rs 1,52,800")

Nothing is held on a notice alone: a hold follows only an exact match, through the same checker as every ticket.

    python -m pipeline.notices read data/notices/NTC_02.pdf     # read one file and print what was found (no DB)
    python -m pipeline.notices read FILE --mode llm             # let the local model read it too
"""
from __future__ import annotations

import argparse
import email
import json
import re
import sys
from datetime import date
from decimal import Decimal
from email import policy
from pathlib import Path

from pipeline.db import ROOT

FIELDS = ("utr", "amount_inr", "txn_date", "txn_time", "beneficiary_account", "legal_basis", "authority",
          "notice_ref", "ack_no", "fir_reference")
NOTICES = ROOT / "data" / "notices.yaml"
MONTHS = {m: i for i, m in enumerate(
    ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"], 1)}
WORDS = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8, "nine": 9,
         "ten": 10, "twenty": 20, "fifty": 50}
SCALE = {"thousand": 1000, "lakh": 100000, "lakhs": 100000, "crore": 10000000, "crores": 10000000}

UTR = re.compile(r"\b((?:IMPS|NEFT|RTGS|UPI|IFT|AMLW)[A-Z]?\d{10,22})\b")
UTR_LABELLED = re.compile(r"(?:UTR|txn\.?\s*ref|transaction\s*(?:ref|id))\.?\s*(?:no\.?|number)?\s*[:#-]?\s*"
                          r"([A-Z0-9]{10,24})\b", re.I)
ACK = re.compile(r"\b(?:ack(?:nowledge?ment)?|complaint)\.?\s*(?:no\.?|number)?\s*[:#-]?\s*(\d{14})\b", re.I)
RUPEES = re.compile(r"(?:Rs\.?|INR|₹)\s*([\d,]+(?:\.\d{1,2})?)", re.I)
RUPEE_WORDS = re.compile(r"\b(" + "|".join(WORDS) + r")\s+(" + "|".join(SCALE) + r")\b", re.I)
DATE_NUM = re.compile(r"\b(\d{1,2})[./-](\d{1,2})[./-](\d{2}|\d{4})\b")
DATE_WORD = re.compile(r"\b(\d{1,2})(?:st|nd|rd|th)?\s+(" + "|".join(MONTHS) + r")[a-z]*\.?,?\s+(\d{4})\b", re.I)
TIME = re.compile(r"\b(?:at\s+)?([01]?\d|2[0-3])[:.]([0-5]\d)\s*(?:hrs|hours|h\b|IST)", re.I)
ACCOUNT = re.compile(r"\b(?:a/c|acct|account)\.?\s*(?:no\.?|number)?\s*[:#-]?\s*([A-Z]{2,4}_[A-Z0-9_]+|\d{9,18})\b",
                     re.I)
LEGAL = re.compile(r"((?:u/s|sec(?:tion)?\.?|S\.)\s*\d+[A-Z]?(?:\s*(?:r/w|read with)\s*(?:S\.|sec(?:tion)?\.?)?\s*"
                   r"\d+[A-Z]?)*\s*(?:BNSS|BNS|CrPC|IT Act))", re.I)
AUTHORITY = re.compile(r"((?:Cyber\s+(?:Crime\s+)?(?:Police\s+Station|PS|Cell)|Cyber\s+Police(?:\s+Station)?)"
                       r"(?:[ \t]*,)?(?:[ \t]+[A-Z][a-z]+){0,2})")    # the place's name, never past the line
REF = re.compile(r"\bref(?:erence)?\.?\s*(?:no\.?)?\s*[:#-]?\s*([A-Z]{2,}(?:/[A-Z0-9-]+){2,})", re.I)
FIR = re.compile(r"\b(FIR\s*(?:No\.?)?\s*\d+/\d{4})", re.I)


# ---------------------------------------------------------------- reading a file

class Unreadable(ValueError):
    """The notice has no text the bank can read (a scanned image and no OCR installed)."""


def read_file(path: str | Path) -> str:
    """The notice's text from an e-mail (.eml), a text file or a PDF. A PDF without a text layer is OCRed if
    pytesseract and Tesseract are installed; otherwise Unreadable."""
    p = Path(path)
    suffix = p.suffix.lower()
    if suffix == ".eml":
        msg = email.message_from_bytes(p.read_bytes(), policy=policy.default)
        body = msg.get_body(preferencelist=("plain", "html"))
        text = body.get_content() if body else ""
        head = "\n".join(f"{k}: {msg[k]}" for k in ("From", "Subject", "Date") if msg[k])
        return f"{head}\n\n{text}".strip()
    if suffix == ".pdf":
        from pypdf import PdfReader

        text = "\n".join((page.extract_text() or "") for page in PdfReader(str(p)).pages).strip()
        if text:
            return text
        return _ocr(p)
    return p.read_text(encoding="utf-8")


def _ocr(p: Path) -> str:
    try:
        import pytesseract
        from pdf2image import convert_from_path
    except ImportError as exc:
        raise Unreadable(f"{p.name} is a scanned image with no text layer; reading it needs OCR (pip install "
                         "pytesseract pdf2image, plus the free Tesseract program)") from exc
    text = "\n".join(pytesseract.image_to_string(img) for img in convert_from_path(str(p))).strip()
    if not text:
        raise Unreadable(f"OCR found no text in {p.name}")
    return text


# ---------------------------------------------------------------- the fixed parser

def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", s or "").strip().lower()


def amounts(text: str) -> list[Decimal]:
    out = [Decimal(m.group(1).replace(",", "")) for m in RUPEES.finditer(text) if m.group(1).strip(",")]
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


def times(text: str) -> list[str]:
    return [f"{int(h):02d}:{m}" for h, m in TIME.findall(text)]


def parse(text: str) -> dict:
    """What a careful clerk would pick out. A field the notice does not give is None."""
    ack = ACK.search(text)
    labelled = [m.group(1).upper() for m in UTR_LABELLED.finditer(text) if not (ack and m.group(1) == ack.group(1))]
    utrs = labelled or [m.group(1).upper() for m in UTR.finditer(text)]
    found, when, at = amounts(text), dates(text), times(text)
    acc = ACCOUNT.search(text)
    legal, auth, ref, fir = LEGAL.search(text), AUTHORITY.search(text), REF.search(text), FIR.search(text)
    return {"utr": utrs[0] if utrs else None,
            "amount_inr": found[0] if found else None,
            "txn_date": when[0] if when else None,
            "txn_time": at[0] if at else None,
            "beneficiary_account": acc.group(1).upper() if acc else None,
            "legal_basis": " ".join(legal.group(1).split()) if legal else None,
            "authority": " ".join(auth.group(1).split()).rstrip(",") if auth else None,
            "notice_ref": ref.group(1) if ref else None,
            "ack_no": ack.group(1) if ack else None,
            "fir_reference": " ".join(fir.group(1).split()) if fir else None}


# ---------------------------------------------------------------- the local model, held to the text

SYSTEM_READ = (
    "You read a police notice sent to a bank about a cyber-fraud payment. Reply with JSON only, with these keys: "
    "utr (the transaction reference), amount_inr (number), txn_date (YYYY-MM-DD), txn_time (HH:MM, or null), "
    "beneficiary_account (the account that received the money), legal_basis (the provision cited, as written), "
    "authority (police unit), notice_ref (the notice's reference number), ack_no (the 14-digit complaint "
    "acknowledgement), fir_reference (the FIR number, if any). Use null for anything the notice does not say. Copy "
    "values exactly as written; never guess or fill in.")


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
    found_amounts, found_dates, found_times = set(amounts(text)), set(dates(text)), set(times(text))
    for k in FIELDS:
        v, ok = model.get(k), False
        if v not in (None, "", "null"):
            try:
                if k == "amount_inr":
                    v = Decimal(str(v).replace(",", ""))
                    ok = v in found_amounts
                elif k == "txn_date":
                    v = date.fromisoformat(str(v)[:10])
                    ok = v in found_dates
                elif k == "txn_time":
                    v = str(v)[:5].zfill(5)
                    ok = v in found_times
                else:
                    v = " ".join(str(v).split())
                    ok = _norm(v) in plain
                    if k in ("utr", "beneficiary_account"):
                        v = v.upper()
            except (ArithmeticError, ValueError):
                ok = False
        out[k], source[k] = (v, "model") if ok else (parsed[k], "parser" if parsed[k] is not None else "none")
    return out, source


def read(text: str, llm=None) -> tuple[dict, dict, str]:
    """(fields, where each came from, who read it). The model is optional and can only confirm what the text says."""
    parsed = parse(text)
    if llm is None:
        return parsed, {k: ("parser" if v is not None else "none") for k, v in parsed.items()}, "parser"
    try:
        fields, source = ground(model_read(text, llm), parsed, text)
    except Exception:                     # noqa: BLE001 - the model is optional: the parser's reading stands
        return parsed, {k: ("parser" if v is not None else "none") for k, v in parsed.items()}, "parser"
    return fields, source, "model" if "model" in source.values() else "parser"


def asks_full_freeze(text: str) -> bool:
    return bool(re.search(r"\b(?:freez\w*|block\w*)\b[^.]{0,40}?\b(?:entire|whole|full)\b|\b(?:entire|whole|full)\b"
                          r"[^.]{0,20}?\b(?:account|a/c)\b[^.]{0,20}?\b(?:freez\w*|block\w*)", text, re.I))


# ---------------------------------------------------------------- into the bank's queue

def ticket_row(n: dict, f: dict, victim_tag: str, text: str) -> dict:
    """The COMPLAINT row for a read notice: an investigating officer's notice. A date without a time is kept as a
    date (txn_time_known FALSE), so intake compares the day, not a made-up minute."""
    known = f["txn_time"] is not None
    txn_at = None
    if f["txn_date"]:
        txn_at = f"{f['txn_date']} {f['txn_time']}:00" if known else f"{f['txn_date']} 00:00:00"
    return {"case_id": n["case_id"], "ack_no": f["ack_no"] or "-", "complaint_source": "IO",
            "ticket_kind": "IO_NOTICE", "victim_tag": victim_tag, "utr": f["utr"] or "-",
            "amount_inr": None if f["amount_inr"] is None else str(f["amount_inr"]),
            "txn_at": txn_at, "txn_time_known": "TRUE" if known else "FALSE", "reported_at": n["received_at"],
            "beneficiary_account": f["beneficiary_account"], "lane": "A", "notice_legal_basis": f["legal_basis"],
            "notice_has_amount": "TRUE" if f["amount_inr"] is not None else "FALSE",
            "ordering_authority": f["authority"], "ordering_reference": f["notice_ref"],
            "fir_reference": f["fir_reference"], "remarks": text[:2000]}


def register(conn, bank: str, notice_id: str, now: str, llm=None) -> dict:
    """Read one notice from the bank's inbox and put it in the bank's queue as a ticket. `conn` is the bank's maker.
    Returns {"case_id", "fields", "source", "read_by", "status", "text"}."""
    row = conn.execute("SELECT notice_id, received_at, channel, sender, source_file, raw_text FROM NOTICE_INBOX "
                       "WHERE notice_id = {n}", {"n": notice_id}).fetchone()
    nid, received, channel, sender, source_file, raw = row
    n = {"notice_id": nid, "received_at": str(received)[:19], "channel": channel, "sender": sender,
         "case_id": f"NOTICE_{nid}"}
    try:
        text = raw or read_file(ROOT / source_file)
    except (Unreadable, OSError) as exc:
        conn.execute("UPDATE NOTICE_INBOX SET notice_status = 'UNREADABLE', read_at = TO_TIMESTAMP({t}, "
                     "'YYYY-MM-DD HH24:MI:SS'), read_note = {r}, stmt_id = CURRENT_STATEMENT WHERE notice_id = {n}",
                     {"t": now, "r": str(exc)[:400], "n": nid})
        return {"case_id": None, "status": "UNREADABLE", "text": str(exc), "fields": {}, "source": {}, "read_by": None}
    fields, source, by = read(text, llm)
    # the victim this payment belongs to, if an earlier ticket here named the same payment; else the notice's own
    tag = conn.execute("SELECT MIN(victim_tag) FROM COMPLAINT WHERE utr = {u} AND ticket_kind <> 'IO_NOTICE'",
                       {"u": fields["utr"] or "-"}).fetchval() or f"V_{nid}"
    t = ticket_row(n, fields, tag, text)
    cols = list(t)
    def slot(c: str) -> str:
        if c in ("txn_at", "reported_at"):
            return f"TO_TIMESTAMP({{{c}}}, 'YYYY-MM-DD HH24:MI:SS')"
        if c in ("txn_time_known", "notice_has_amount"):
            return f"{{{c}!r}}"
        return f"{{{c}!d}}" if c == "amount_inr" and t[c] is not None else f"{{{c}}}"

    values = ", ".join(slot(c) for c in cols)
    conn.execute(f"INSERT INTO COMPLAINT ({', '.join(cols)}) VALUES ({values})", t)
    conn.execute("INSERT INTO TICKET (case_id, received_at, ticket_status, origin) VALUES "
                 "({c}, TO_TIMESTAMP({t}, 'YYYY-MM-DD HH24:MI:SS'), 'QUEUED', 'POLICE')", {"c": n["case_id"], "t": now})
    shown = {k: (str(v) if v is not None else None) for k, v in fields.items()}
    note = ("The notice asks for the whole account to be frozen; the bank holds the amount traced to the victim, and "
            "a whole-account freeze is the officer's decision.") if asks_full_freeze(text) else None
    conn.execute("UPDATE NOTICE_INBOX SET notice_status = 'READ', "
                 "read_at = TO_TIMESTAMP({t}, 'YYYY-MM-DD HH24:MI:SS'), read_by = {b}, fields_json = {j}, "
                 "read_note = {r}, case_id = {c}, raw_text = {x}, stmt_id = CURRENT_STATEMENT WHERE notice_id = {n}",
                 {"t": now, "b": by, "j": json.dumps({"fields": shown, "source": source}), "r": note,
                  "c": n["case_id"], "x": text[:4000], "n": nid})
    return {"case_id": n["case_id"], "fields": fields, "source": source, "read_by": by, "status": "READ",
            "text": text}


def pending(conn, until: str) -> list[tuple[str, str]]:
    """(notice_id, received_at) of notices in the inbox not read yet, due by `until`."""
    return [(nid, str(at)[:19]) for nid, at in conn.execute(
        "SELECT notice_id, received_at FROM NOTICE_INBOX WHERE notice_status = 'NEW' "
        "AND received_at <= TO_TIMESTAMP({u}, 'YYYY-MM-DD HH24:MI:SS') ORDER BY received_at, notice_id",
        {"u": until}).fetchall()]


def inbox_rows(path: Path = NOTICES) -> dict[str, list[tuple]]:
    """NOTICE_INBOX rows per bank, from data/notices.yaml (loaded with the rest of the synthetic world)."""
    import yaml

    out: dict[str, list[tuple]] = {}
    if not path.exists():
        return out
    for n in yaml.safe_load(path.read_text(encoding="utf-8")).get("notices") or []:
        out.setdefault(n["to_bank"], []).append(
            (n["notice_id"], n["received_at"] + ".000", n["channel"], n["sender"], n.get("file"),
             (n.get("text") or "").strip() or None, "NEW"))
    return out


# ---------------------------------------------------------------- command line

def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("read", help="read one notice file and print what was found")
    r.add_argument("file")
    r.add_argument("--mode", choices=["parser", "llm"], default="parser")
    args = ap.parse_args(argv)
    llm = None
    if args.mode == "llm":
        from pipeline.llm import client

        llm = client()
    try:
        text = read_file(args.file)
    except Unreadable as exc:
        print(f"unreadable: {exc}", file=sys.stderr)
        return 1
    fields, source, by = read(text, llm)
    print(f"read by: {by}")
    for k in FIELDS:
        v = fields[k]
        print(f"  {k:20} {'-' if v is None else v}   ({source[k]})")
    if asks_full_freeze(text):
        print("  note: the notice asks for a whole-account freeze (the officer's decision, never automatic)")
    return 0


if __name__ == "__main__":
    sys.exit(main())