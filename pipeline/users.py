"""Create the database users and their privileges (run by `make db` after the schemas; docs/DESIGN.md,
"Separation of duties" and "Each bank sees only its own data").

Per bank (schema PH_X, ...):
  PH_X_MAKER    the agent's tools: read the bank's schema, write only the work tables the analysis fills
                (trace, attribution, evidence, the rules engine's output, intake checks, its own run log) and insert
                proposals and audit rows. No privilege on holds, the outbox, decisions, splits or the watch list.
  PH_X_CHECKER  deterministic code that validates proposals and carries them out: holds, filings, holder
                messages, splits, links, the watch list, officer decisions. Insert-only on the audit log.
Shared:
  PH_PORTAL_SVC the simulated CFCFRMS: its own schema PH_PORTAL, read every bank's outbox, write every bank's inbox.

No bank user has any privilege on another bank's schema, so Bank X's agent cannot read Bank Y's data, not merely
chooses not to. Nobody but the admin can update or delete the audit log.

    python -m pipeline.users            # (re)create the users and grants; needs PH_ROLE_PASSWORD in .env
"""
from __future__ import annotations

import sys

from pipeline.db import BANKS, PORTAL_SCHEMA, role_user, schema_for

# Tables the maker fills while it analyses a ticket (insert, update, delete).
MAKER_WORK = ["INTAKE_CHECK", "TRACE_LINK", "TRACE_EDGE", "REFERRAL_OUT", "ATTRIBUTION", "ATTRIBUTION_RAW",
              "ATTRIBUTION_FLOW", "SCORE_TARGET", "ACCOUNT_SIGNALS", "ACCOUNT_SCORE", "CASE_ACCOUNT", "CASE_ACTION",
              "AGENT_RUN", "AGENT_STEP", "MORNING_LOG", "TODAY_RAW", "TODAY_OUTCOME", "WATCH_EVENT",
              "LEARNED_INPUT", "NOTICE_INBOX", "CREDIT_MATCH", "SCORE_SNAPSHOT", "PAYMENT_CHECK"]
MAKER_INSERT = ["PROPOSAL", "AUDIT_LOG", "COMPLAINT", "TICKET"]   # COMPLAINT, TICKET: a notice read from the inbox
MAKER_UPDATE = ["TICKET"]                       # picking a ticket up: QUEUED -> WORKING
# Tables only the checker changes.
CHECKER_WORK = ["CHECK_RESULT", "HOLD_LEDGER", "PORTAL_ENTRY", "HOLDER_MESSAGE", "DECISION", "VICTIM_SPLIT",
                "WATCH_LIST", "CASE_LINK", "WATCH_EVENT", "PRE_ALERT", "FRAUD_TEAM_TASK", "LAYERING_RECORD",
                "HOLDER_EXPLANATION", "POLICE_DIRECTION", "PAYER_RESPONSE"]
CHECKER_UPDATE = ["PROPOSAL", "CASE_ACTION", "TICKET", "AGENT_RUN"]
CHECKER_INSERT = ["AUDIT_LOG"]


def statements(password: str) -> list[str]:
    """Every statement, in order. Users are created once; grants are repeated on every build because
    CREATE OR REPLACE TABLE drops the privileges on the replaced table."""
    pw = password.replace('"', '""')
    out: list[str] = []
    users = [role_user("portal")] + [role_user(r, b) for b in BANKS for r in ("maker", "checker")]
    for u in users:
        out.append(f'CREATE USER {u} IDENTIFIED BY "{pw}"')
    out += [f"GRANT CREATE SESSION TO {u}" for u in users]
    for bank in BANKS:
        s = schema_for(bank)
        maker, checker = role_user("maker", bank), role_user("checker", bank)
        for u in (maker, checker):
            out.append(f"GRANT SELECT ON SCHEMA {s} TO {u}")
            out.append(f"GRANT EXECUTE ON SCHEMA {s} TO {u}")
        out += [f"GRANT INSERT, UPDATE, DELETE ON {s}.{t} TO {maker}" for t in MAKER_WORK]
        out += [f"GRANT INSERT ON {s}.{t} TO {maker}" for t in MAKER_INSERT]
        out += [f"GRANT UPDATE ON {s}.{t} TO {maker}" for t in MAKER_UPDATE]
        out += [f"GRANT INSERT, UPDATE, DELETE ON {s}.{t} TO {checker}" for t in CHECKER_WORK]
        out += [f"GRANT UPDATE ON {s}.{t} TO {checker}" for t in CHECKER_UPDATE]
        out += [f"GRANT INSERT ON {s}.{t} TO {checker}" for t in CHECKER_INSERT]
        portal = role_user("portal")
        out.append(f"GRANT SELECT ON {s}.PORTAL_ENTRY TO {portal}")
        out.append(f"GRANT SELECT ON {s}.BANK TO {portal}")
        out.append(f"GRANT SELECT, INSERT ON {s}.COMPLAINT TO {portal}")
        out.append(f"GRANT SELECT, INSERT ON {s}.TICKET TO {portal}")
    out.append(f"GRANT SELECT, INSERT, UPDATE, DELETE ON SCHEMA {PORTAL_SCHEMA} TO {role_user('portal')}")
    return out


def existing_users(conn) -> set[str]:
    return {u.upper() for (u,) in conn.execute("SELECT USER_NAME FROM EXA_DBA_USERS").fetchall()}


def apply(conn, password: str, quiet: bool = False) -> int:
    """Create missing users (and reset the password of existing ones), then grant. Returns the number of failures."""
    if not password:
        raise SystemExit("Missing PH_ROLE_PASSWORD in .env: the password for the maker, checker and portal users.")
    have = existing_users(conn)
    pw = password.replace('"', '""')
    failures = 0
    for stmt in statements(password):
        if stmt.startswith("CREATE USER"):
            user = stmt.split()[2]
            if user.upper() in have:
                stmt = f'ALTER USER {user} IDENTIFIED BY "{pw}"'
        try:
            conn.execute(stmt)
        except Exception as exc:  # report every failure, keep going: one missing grant shows up in the db tests
            failures += 1
            shown = stmt if "IDENTIFIED" not in stmt else stmt.split("IDENTIFIED")[0] + "IDENTIFIED BY ***"
            print(f"FAILED {shown}\n  {exc}", file=sys.stderr)
    if not quiet:
        print(f"ok  users and grants ({len(statements(password))} statements, {failures} failed)")
    return failures


def main(argv: list[str] | None = None) -> int:
    from pipeline.db import connect, settings

    conn = connect()
    try:
        return 1 if apply(conn, settings()["role_password"]) else 0
    finally:
        conn.close()


if __name__ == "__main__":
    sys.exit(main())