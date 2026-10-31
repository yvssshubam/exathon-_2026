"""Load the synthetic world into Exasol (`make seed`): each bank gets only its own data, the portal gets the tickets.

The case files describe one synthetic world: banks, accounts, transactions, police tickets. Each bank's schema gets
what that bank would really have (docs/DESIGN.md, "Each bank sees only its own data"):
  ACCOUNT           its own accounts with synthetic KYC, plus bare rows (ID, bank, beneficiary name) for the other
                    banks' accounts its customers paid or were paid by
  TXN               every transaction with one of its own accounts on either side (90-day history included)
  BALANCE_SNAPSHOT  its own accounts' opening balances
  PAYMENT_REQUEST   the simulation's tool-world-only payment tries from its own accounts
The police tickets go to the portal (PH_PORTAL.TICKET), addressed to the bank of the account they name. Banks'
inboxes start empty: the portal service delivers each ticket when the simulated clock reaches it (pipeline/portal.py),
so no bank ever sees a ticket before it arrived.

When `make data` has loaded AMLworld into schema PH_AML, its banks are spread over the simulated banks (each AMLworld
bank becomes a branch of Bank X, Y or Z) and every bank gets the AMLworld rows touching its accounts, so the agent
works its tickets against a full-size transaction table (DECISIONS D-37). Uses pyexasol's import_from_iterable (a
native IMPORT), never row-by-row INSERTs. Reloading is safe: every table is emptied first.

  python -m pipeline.load_cases                 # load
  python -m pipeline.load_cases --verify        # load, then compare row counts and sums with the files
  python -m pipeline.load_cases --no-amlworld   # leave the AMLworld background out (quick reset)
"""
from __future__ import annotations

import argparse
import sys
import time
import zlib
from decimal import Decimal

import yaml

from pipeline.cases import DEMO_CASE, EXTRA_CASES, LOOP_CASE, PROACTIVE_CASE, ROOT, Case, load_case
from pipeline.db import BANKS, PORTAL_SCHEMA, schema_for
from pipeline.gen_history import generate as generate_history

SNAPSHOT_TS = "2026-10-18 00:00:00.000"   # opening balances are as of the start of the case day
CASE_FILES = {"demo_case": DEMO_CASE, "extra_cases": EXTRA_CASES, "loop_case": LOOP_CASE,
              "proactive_case": PROACTIVE_CASE}
SCORING = ROOT / "config" / "scoring.yaml"
RULES = ROOT / "config" / "rules.yaml"
WEIGHTS = ROOT / "config" / "scoring_weights.yaml"      # approved learned versions (pipeline/weights.py approve)

# Reference tables loaded into every bank's schema (all columns, in table order).
BANK_TABLES = ["BANK", "ACCOUNT", "TXN", "BALANCE_SNAPSHOT", "PAYMENT_REQUEST", "SCORING_CONFIG", "PROFILE_BAND",
               "LEARNED_COEF", "LEARNED_TREE", "SCORING_WEIGHTS", "SUSPECT_REGISTRY", "BANK_HOLIDAY",
               "HOLDER_EXPLANATION", "POLICE_DIRECTION", "PAYER_RESPONSE"]
# Emptied on every load: the inbox (the portal fills it) and every work table, so a reload is a clean slate.
EMPTIED = ["COMPLAINT", "TICKET", "INTAKE_CHECK", "CASE_LINK", "TRACE_LINK", "TRACE_EDGE", "REFERRAL_OUT",
           "ATTRIBUTION", "ATTRIBUTION_RAW", "ATTRIBUTION_FLOW", "VICTIM_SPLIT", "SCORE_TARGET", "ACCOUNT_SIGNALS",
           "ACCOUNT_SCORE", "CASE_ACCOUNT", "CASE_ACTION", "PROPOSAL", "CHECK_RESULT", "HOLD_LEDGER",
           "PORTAL_ENTRY", "HOLDER_MESSAGE", "DECISION", "WATCH_LIST", "DECLINED_ATTEMPT", "UNDELIVERED_CREDIT",
           "WATCH_EVENT",
           "AGENT_RUN", "AGENT_STEP", "MORNING_LOG", "TODAY_RAW", "TODAY_OUTCOME", "LEARNED_INPUT", "NOTICE_INBOX",
           "CREDIT_MATCH", "SCORE_SNAPSHOT", "PAYMENT_CHECK", "PRE_ALERT", "FRAUD_TEAM_TASK", "LAYERING_RECORD",
           "RUN_SETTING"]
PORTAL_TABLES = ["TICKET", "ENTRY", "ROUTE_LOG", "SIM_CLOCK"]
PORTAL_TICKET_COLUMNS = ["case_id", "to_bank_id", "origin", "parent_case_id", "source_entry_id", "ack_no",
                         "complaint_source", "ticket_kind", "victim_account", "victim_tag", "utr", "amount_inr",
                         "txn_at", "created_at", "beneficiary_account", "lane", "notice_legal_basis",
                         "notice_has_amount", "ordering_authority", "ordering_reference", "fir_reference", "remarks",
                         "corrects_case_id", "delivered_at"]
KYC_COLUMNS = 6
ACCOUNT_STUB_NULLS = 5 + KYC_COLUMNS   # opened_on, account_type, holder_type, profile_band, history_profile + KYC


def ts(value: str | None) -> str | None:
    if value is None:
        return None
    value = str(value)
    return value if "." in value else value + ".000"


def b(value: bool) -> str:
    return "TRUE" if value else "FALSE"


def rupees(paise: int) -> str:
    return f"{paise // 100}.{paise % 100:02d}"


def synthetic_kyc(account: dict) -> tuple:
    """customer_id, ifsc, mobile, email, address, pan: deterministic, obviously synthetic (a case may give its own)."""
    k = account.get("kyc") or {}
    n = zlib.crc32(account["account_id"].encode())
    bank = account["bank_id"][-1]
    return (k.get("customer_id", f"CUST{n % 10**8:08d}"),
            k.get("ifsc", f"BNK{bank}0{n % 10**6:06d}"),
            k.get("mobile", f"+91 9XXXX X{n % 10**4:04d}"),
            k.get("email", f"{account['account_id'].lower()}@example.com"),
            k.get("address", "Synthetic address, Vellore, Tamil Nadu"),
            k.get("pan", f"SYNTH{n % 10**4:04d}X"))


def portal_ticket(c: dict, to_bank: str) -> tuple:
    amount = c.get("amount_inr")
    return (c["case_id"], to_bank, "POLICE", None, None, c["ack_no"], str(c["source"]), c["ticket_kind"],
            c.get("victim_account"), c["victim_tag"], c["utr"], None if amount is None else str(Decimal(str(amount))),
            ts(c.get("txn_at")), ts(c["reported_at"]), c.get("beneficiary_account"), c["lane"],
            c.get("notice_legal_basis"), b(c["notice_has_amount"]), c.get("ordering_authority"),
            c.get("ordering_reference"), c.get("fir_reference"), c.get("remarks"), c.get("corrects_case_id"), None)


def rows_for(cases: dict[str, Case], history: bool = True) -> dict[str, list]:
    """The whole synthetic world as rows. Tables as in a bank schema, plus PORTAL_TICKET (tuples) and COMPLAINTS
    (the case files' ticket dicts, for the oracles). `bank_rows` cuts it into one bank's share."""
    accounts: dict[str, tuple] = {}
    out: dict[str, list] = {t: [] for t in BANK_TABLES}
    out["PORTAL_TICKET"], out["COMPLAINTS"] = [], []
    for source, case in cases.items():
        for a in case.accounts:
            if a["account_id"] in accounts:
                raise ValueError(f"account {a['account_id']} defined in two case files")
            accounts[a["account_id"]] = (a["account_id"], a["bank_id"], a.get("holder"), a["opened_on"],
                                         a["account_type"], a["holder_type"], a["profile_band"], a.get("history"),
                                         *synthetic_kyc(a))
            if a.get("opening_balance_inr") is not None:
                opening = str(Decimal(str(a["opening_balance_inr"])))
                out["BALANCE_SNAPSHOT"].append((a["account_id"], SNAPSHOT_TS, opening))
        for t in case.txns:
            out["TXN"].append((t.txn_id, t.utr, ts(t.ts), t.from_account, t.to_account, str(t.amount_inr),
                               t.channel, b(t.is_exit), t.exit_identifier, source))
        for t in case.history:
            out["TXN"].append((t.txn_id, t.utr, ts(t.ts), t.from_account, t.to_account, str(t.amount_inr),
                               t.channel, b(t.is_exit), t.exit_identifier, "history"))
        for r in case.requests:
            out["PAYMENT_REQUEST"].append([r.request_id, ts(r.ts), r.from_account, r.to_account, None,
                                           str(r.amount_inr), r.channel, r.exit_identifier, None, source])
        for c in case.complaints:
            out["COMPLAINTS"].append(c)
    bank_of = {k: v[1] for k, v in accounts.items()}
    for c in out["COMPLAINTS"]:
        to_bank = bank_of.get(c.get("beneficiary_account") or "")
        if to_bank not in BANKS:
            raise ValueError(f"{c['case_id']}: names account {c.get('beneficiary_account')}, not at a simulated bank")
        out["PORTAL_TICKET"].append(portal_ticket(c, to_bank))
    for r in out["PAYMENT_REQUEST"]:
        r[4] = bank_of.get(r[3])
    out["PAYMENT_REQUEST"] = [tuple(r) for r in out["PAYMENT_REQUEST"]]
    if history:
        all_accounts = [a for case in cases.values() for a in case.accounts]
        for h in generate_history(all_accounts):
            out["TXN"].append((h["txn_id"], h["utr"], ts(h["ts"]), h["from"], h["to"], rupees(h["amount_paise"]),
                               h["channel"], b(h["to"].startswith("EXIT:")), h["exit_identifier"], "history"))
    scoring = yaml.safe_load(SCORING.read_text(encoding="utf-8"))
    for score_name in ("pass_through", "fraud_exposure"):           # scoring v2 (D-57)
        for name, sig in scoring[score_name].items():
            out["SCORING_CONFIG"].append((name, score_name, sig["low_at"], sig["high_at"], sig["start_weight"],
                                          sig["high_sentence"], sig["low_sentence"]))
    out["SCORING_WEIGHTS"] = weight_rows(scoring)
    from pipeline import learned

    model = learned.load()                      # the retired learned signal: kept in its tables, never scored (D-58)
    out["LEARNED_COEF"] = learned.coef_rows(model)
    out["LEARNED_TREE"] = learned.tree_rows(model)
    rules = yaml.safe_load(RULES.read_text(encoding="utf-8"))
    out["BANK_HOLIDAY"] = [(str(h["date"]), h.get("name")) for h in rules["proactive"].get("bank_holidays") or []]
    for case in cases.values():                 # simulated inputs for proactive holds (D-60 to D-64)
        sim = case.sim_inputs
        out["SUSPECT_REGISTRY"] += [(r["account_id"], ts(r["listed_on"]), r.get("listed_by", "I4C (simulated)"))
                                    for r in sim.get("suspect_registry", [])]
        out["HOLDER_EXPLANATION"] += [(r["account_id"], r["txn_id"], ts(r["explained_at"]), r.get("explanation"), None)
                                      for r in sim.get("holder_explanations", [])]
        out["POLICE_DIRECTION"] += [(r["account_id"], r["txn_id"], r["direction"], r.get("reference"),
                                     ts(r["directed_at"]), None) for r in sim.get("police_directions", [])]
        out["PAYER_RESPONSE"] += [(r["txn_id"], r["response"], ts(r["responded_at"]), r.get("note"), None)
                                  for r in sim.get("payer_responses", [])]
    for band, expected in scoring["profile_band_expected_monthly_credit_inr"].items():
        out["PROFILE_BAND"].append((band, str(Decimal(str(expected)))))
    out["ACCOUNT"] = list(accounts.values())
    out["BANK"] = [(k, v["name"], "FALSE") for k, v in BANKS.items()]
    return out


def weight_rows(scoring: dict) -> list[tuple]:
    """SCORING_WEIGHTS: version 0 (the starting weights, approved by policy) and every approved learned version in
    config/scoring_weights.yaml. Exactly one version is live: the newest approved one (D-58)."""
    saved = yaml.safe_load(WEIGHTS.read_text(encoding="utf-8")) if WEIGHTS.exists() else None
    versions = (saved or {}).get("versions") or []
    live = max((int(v["version"]) for v in versions if v.get("approved_by")), default=0)
    rows = []
    for score_name in ("pass_through", "fraud_exposure"):
        for name, sig in scoring[score_name].items():
            rows.append((0, score_name, name, None, sig["start_weight"], None, "config/scoring.yaml start_weight",
                         None, None, "policy", None, "TRUE" if live == 0 else "FALSE"))
    for v in versions:
        if not v.get("approved_by"):
            continue
        for w in v["weights"]:
            test = (v.get("test") or {}).get(w["score"], {})
            rows.append((int(v["version"]), w["score"], w["signal"], w.get("coefficient"), w["weight"],
                         ts(v.get("trained_at")), v.get("data_window"), test.get("auc"), test.get("precision"),
                         v["approved_by"], ts(v.get("approved_at")), "TRUE" if int(v["version"]) == live else "FALSE"))
    return rows


def bank_rows(world: dict[str, list], bank: str) -> dict[str, list[tuple]]:
    """One bank's share of the world: what goes into its schema."""
    accounts = {r[0]: r for r in world["ACCOUNT"]}
    own = {k for k, r in accounts.items() if r[1] == bank}
    txns = [t for t in world["TXN"] if t[3] in own or t[4] in own]
    others = ({t[3] for t in txns} | {t[4] for t in txns}) - own
    stubs = [(k, accounts[k][1], accounts[k][2], *([None] * ACCOUNT_STUB_NULLS)) for k in sorted(others)
             if k in accounts]
    return {
        "BANK": [(k, name, b(k == bank)) for k, name, _ in world["BANK"]],
        "ACCOUNT": [accounts[k] for k in sorted(own)] + stubs,
        "TXN": txns,
        "BALANCE_SNAPSHOT": [r for r in world["BALANCE_SNAPSHOT"] if r[0] in own],
        "PAYMENT_REQUEST": [r for r in world["PAYMENT_REQUEST"] if r[2] in own],
        "SCORING_CONFIG": world["SCORING_CONFIG"],
        "PROFILE_BAND": world["PROFILE_BAND"],
        "LEARNED_COEF": world["LEARNED_COEF"],
        "LEARNED_TREE": world["LEARNED_TREE"],
        "SCORING_WEIGHTS": world["SCORING_WEIGHTS"],
        "SUSPECT_REGISTRY": world["SUSPECT_REGISTRY"],          # national: every bank may read it (simulated)
        "BANK_HOLIDAY": world["BANK_HOLIDAY"],
        "HOLDER_EXPLANATION": [r for r in world["HOLDER_EXPLANATION"] if r[0] in own],
        "POLICE_DIRECTION": [r for r in world["POLICE_DIRECTION"] if r[0] in own],
        "PAYER_RESPONSE": [r for r in world["PAYER_RESPONSE"] if r[0] in {t[0] for t in txns if t[4] in own}],
    }


# ---------------------------------------------------------------- AMLworld background, spread over the banks

AML_MAP_SQL = [
    # Each AMLworld bank becomes a branch of one simulated bank, in turn by bank ID (fixed, so reloads match).
    """CREATE OR REPLACE TABLE PH_AML.BANK_MAP AS
       SELECT bank_id,
              CASE MOD(ROW_NUMBER() OVER (ORDER BY bank_id), 3) WHEN 1 THEN 'BANK_X' WHEN 2 THEN 'BANK_Y'
                   ELSE 'BANK_Z' END AS sim_bank_id
       FROM PH_AML.BANK""",
    """CREATE OR REPLACE TABLE PH_AML.ACCOUNT_MAP AS
       SELECT a.account_id, m.sim_bank_id FROM PH_AML.ACCOUNT a JOIN PH_AML.BANK_MAP m ON m.bank_id = a.bank_id""",
]
AML_TXN_SQL = """
    SELECT t.txn_id, t.utr, t.ts, t.from_account, t.to_account, t.amount_inr, t.channel, t.is_exit,
           t.exit_identifier, t.data_source
    FROM PH_AML.TXN t
    JOIN PH_AML.ACCOUNT_MAP mf ON mf.account_id = t.from_account
    JOIN PH_AML.ACCOUNT_MAP mt ON mt.account_id = t.to_account
    WHERE mf.sim_bank_id = {bank} OR mt.sim_bank_id = {bank}"""


def amlworld_loaded(conn) -> bool:
    return bool(conn.execute("SELECT COUNT(*) FROM EXA_ALL_TABLES WHERE TABLE_SCHEMA = 'PH_AML' "
                             "AND TABLE_NAME = 'TXN'").fetchval())


def copy_amlworld(conn, bank: str) -> int:
    """Copy the AMLworld rows touching `bank`'s branches into its schema, unless they are already there.
    Returns the number of AMLworld transactions the bank has."""
    s = schema_for(bank)
    have_map = conn.execute("SELECT COUNT(*) FROM EXA_ALL_TABLES WHERE TABLE_SCHEMA = 'PH_AML' "
                            "AND TABLE_NAME = 'ACCOUNT_MAP'").fetchval()
    if not have_map:
        for stmt in AML_MAP_SQL:
            conn.execute(stmt)
    expected = int(conn.execute(f"SELECT COUNT(*) FROM ({AML_TXN_SQL})", {"bank": bank}).fetchval())
    present = int(conn.execute(f"SELECT COUNT(*) FROM {s}.TXN WHERE data_source = 'amlworld'").fetchval())
    if present == expected:
        print(f"kept   {present:9} AMLworld transactions in {s}.TXN")
        return present
    started = time.perf_counter()
    conn.execute(f"DELETE FROM {s}.TXN WHERE data_source = 'amlworld'")
    conn.execute(f"DELETE FROM {s}.ACCOUNT WHERE LEFT(account_id, 4) = 'AML_'")
    conn.execute(f"INSERT INTO {s}.TXN {AML_TXN_SQL}", {"bank": bank})
    conn.execute(f"""
        INSERT INTO {s}.ACCOUNT (account_id, bank_id, holder_name, opened_on, account_type, holder_type,
                                 profile_band, history_profile, customer_id)
        SELECT a.account_id, m.sim_bank_id, a.holder_name,
               CASE WHEN m.sim_bank_id = {{bank}} THEN a.opened_on END,
               CASE WHEN m.sim_bank_id = {{bank}} THEN a.account_type END,
               CASE WHEN m.sim_bank_id = {{bank}} THEN a.holder_type END,
               CASE WHEN m.sim_bank_id = {{bank}} THEN a.profile_band END,
               CASE WHEN m.sim_bank_id = {{bank}} THEN a.history_profile END,
               CASE WHEN m.sim_bank_id = {{bank}} THEN a.customer_id END
        FROM PH_AML.ACCOUNT a JOIN PH_AML.ACCOUNT_MAP m ON m.account_id = a.account_id
        WHERE a.account_id IN (SELECT from_account FROM {s}.TXN WHERE data_source = 'amlworld'
                               UNION SELECT to_account FROM {s}.TXN WHERE data_source = 'amlworld')""",
                 {"bank": bank})
    print(f"copied {expected:9} AMLworld transactions into {s}.TXN ({time.perf_counter() - started:.1f}s)")
    return expected


# ---------------------------------------------------------------- loading

def hold_mode(override: str | None = None) -> str:
    """The proactive-hold mode of this run (D-62): the demo bar's choice, else config/rules.yaml."""
    if override in ("rbi_draft", "current_sop"):
        return override
    return yaml.safe_load(RULES.read_text(encoding="utf-8"))["proactive"]["proactive_hold_mode"]


def load(conn, world: dict[str, list], amlworld: bool = True, mode: str | None = None) -> dict[str, int]:
    """Empty every bank's tables, load its share, put the police tickets on the portal. Returns AMLworld
    transactions per bank (kept or copied). `conn` is an admin connection."""
    keep_aml = amlworld and amlworld_loaded(conn)
    copied: dict[str, int] = {}
    for bank in BANKS:
        s = schema_for(bank)
        mine = bank_rows(world, bank)
        for table in EMPTIED:
            conn.execute(f"TRUNCATE TABLE {s}.{table}")
        for table in BANK_TABLES:
            if keep_aml and table in ("TXN", "ACCOUNT"):
                key = "data_source = 'amlworld'" if table == "TXN" else "LEFT(account_id, 4) = 'AML_'"
                conn.execute(f"DELETE FROM {s}.{table} WHERE NOT ({key})")
            else:
                conn.execute(f"TRUNCATE TABLE {s}.{table}")
            if mine[table]:
                conn.import_from_iterable(mine[table], (s, table))
        conn.execute(f"INSERT INTO {s}.RUN_SETTING (setting_name, setting_value) VALUES ('proactive_hold_mode', "
                     "{m})", {"m": hold_mode(mode)})
        from pipeline.notices import inbox_rows

        notices = inbox_rows().get(bank, [])        # police notices that reach this desk by e-mail or letter (D-52)
        if notices:
            conn.import_from_iterable(notices, (s, "NOTICE_INBOX"), import_params={"columns": [
                "notice_id", "received_at", "channel", "sender", "source_file", "raw_text", "notice_status"]})
        print(f"loaded {s}: " + ", ".join(f"{t} {len(mine[t])}" for t in ("ACCOUNT", "TXN", "PAYMENT_REQUEST"))
              + (f", NOTICE_INBOX {len(notices)}" if notices else ""))
        if keep_aml:
            copied[bank] = copy_amlworld(conn, bank)
    if amlworld and not keep_aml:
        print("AMLworld not loaded (make data): the banks run without the background")
    for table in PORTAL_TABLES:
        conn.execute(f"TRUNCATE TABLE {PORTAL_SCHEMA}.{table}")
    conn.import_from_iterable(world["PORTAL_TICKET"], (PORTAL_SCHEMA, "TICKET"),
                              import_params={"columns": PORTAL_TICKET_COLUMNS})
    print(f"loaded {PORTAL_SCHEMA}.TICKET: {len(world['PORTAL_TICKET'])} police tickets (delivered when the clock "
          f"reaches them)")
    return copied


def verify(conn, world: dict[str, list], copied: dict[str, int] | None = None) -> bool:
    ok = True
    copied = copied or {}
    for bank in BANKS:
        s = schema_for(bank)
        mine = bank_rows(world, bank)
        for table in BANK_TABLES:
            where = (" WHERE data_source <> 'amlworld'" if table == "TXN" and bank in copied else
                     " WHERE LEFT(account_id, 4) <> 'AML_'" if table == "ACCOUNT" and bank in copied else "")
            n = conn.execute(f"SELECT COUNT(*) FROM {s}.{table}{where}").fetchval()
            good = n == len(mine[table])
            ok &= good
            print(f"{'ok ' if good else 'BAD'} {s}.{table:16} db={n:7}  files={len(mine[table]):7}")
        db_sum = Decimal(str(conn.execute(
            f"SELECT COALESCE(SUM(amount_inr), 0) FROM {s}.TXN WHERE data_source <> 'amlworld'").fetchval()))
        file_sum = sum((Decimal(r[5]) for r in mine["TXN"]), Decimal(0))
        good = db_sum == file_sum
        ok &= good
        print(f"{'ok ' if good else 'BAD'} {s}.SUM(TXN.amount_inr) db={db_sum}  files={file_sum}")
    n = conn.execute(f"SELECT COUNT(*) FROM {PORTAL_SCHEMA}.TICKET").fetchval()
    good = n == len(world["PORTAL_TICKET"])
    ok &= good
    print(f"{'ok ' if good else 'BAD'} {PORTAL_SCHEMA}.TICKET db={n}  files={len(world['PORTAL_TICKET'])}")
    demo = conn.execute(f"SELECT COUNT(*) FROM {schema_for('BANK_X')}.TXN WHERE txn_id LIKE 'T_DEMO_%'").fetchval()
    good = demo == 10
    ok &= good
    print(f"{'ok ' if good else 'BAD'} Bank X sees {demo} of the demo night's 13 transactions (10 touch its accounts)")
    return ok


def world_from_files() -> dict[str, list]:
    return rows_for({source: load_case(path) for source, path in CASE_FILES.items()})


def reset(conn, amlworld: bool = True, mode: str | None = None) -> dict[str, int]:
    """Reload everything: every ticket waits on the portal again, every work table is empty (the console's Reset).
    mode: rbi_draft or current_sop for this run's proactive holds (default: config/rules.yaml)."""
    world = world_from_files()
    copied = load(conn, world, amlworld, mode)
    from pipeline.db import audit

    for bank in BANKS:
        conn.execute(f"OPEN SCHEMA {schema_for(bank)}")
        audit(conn, "seed", actor="admin", detail=f"{len(bank_rows(world, bank)['TXN'])} case transactions, "
                                                    f"{copied.get(bank, 0)} AMLworld transactions")
    return copied


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--verify", action="store_true")
    ap.add_argument("--no-amlworld", action="store_true", help="leave the AMLworld background out")
    args = ap.parse_args(argv)

    from pipeline.db import connect

    conn = connect()
    try:
        copied = reset(conn, amlworld=not args.no_amlworld)
        good = verify(conn, world_from_files(), copied) if args.verify else True
    finally:
        conn.close()
    return 0 if good else 1


if __name__ == "__main__":
    sys.exit(main())