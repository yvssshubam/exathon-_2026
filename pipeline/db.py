"""One place that knows how to reach Exasol, as whom, and in which bank's schema. Credentials come only from .env.

Every bank has its own schema (config/banks.yaml: PH_X, PH_Y, PH_Z) and its own database users, so a bank can only
read its own data and the model's side of the agent can never write a hold (docs/DESIGN.md, "Separation of duties"):

  role     database user       may
  admin    EXA_USER (sys)      build schemas, load data, run the simulated core banking and today's desk
  maker    PH_<X>_MAKER        read its bank's schema; write only its own work tables and PROPOSAL (never holds,
                               never the portal outbox, never decisions)
  checker  PH_<X>_CHECKER      read its bank's schema; carry out actions: holds, filings, splits, the watch list
  portal   PH_PORTAL_SVC       the simulated CFCFRMS: read banks' outboxes, write banks' inboxes, own PH_PORTAL

`make db` creates the users (pipeline/users.py) with the password in PH_ROLE_PASSWORD.
"""
from __future__ import annotations

import os
import ssl
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
BANKS: dict = yaml.safe_load((ROOT / "config" / "banks.yaml").read_text(encoding="utf-8"))
PORTAL_SCHEMA = "PH_PORTAL"
ROLES = ("admin", "maker", "checker", "portal")


def schema_for(bank: str) -> str:
    try:
        return BANKS[bank]["schema"]
    except KeyError as exc:
        raise SystemExit(f"Unknown bank {bank!r}; config/banks.yaml has {sorted(BANKS)}") from exc


def role_user(role: str, bank: str | None = None) -> str:
    """Database user for a role: PH_X_MAKER, PH_X_CHECKER, PH_PORTAL_SVC."""
    if role == "portal":
        return "PH_PORTAL_SVC"
    if role not in ("maker", "checker"):
        raise ValueError(f"no database user for role {role!r}")
    return f"{schema_for(bank)}_{role.upper()}"


def settings() -> dict:
    try:
        from dotenv import load_dotenv

        load_dotenv(ROOT / ".env")
    except ImportError:
        pass
    missing = [k for k in ("EXA_DSN", "EXA_USER", "EXA_PASSWORD") if not os.environ.get(k)]
    if missing:
        raise SystemExit(
            f"Missing {', '.join(missing)}. Copy .env.example to .env and fill it in from `exasol info`."
        )
    dsn = os.environ["EXA_DSN"].strip()
    fingerprint = os.environ.get("EXA_FINGERPRINT", "").strip()
    if fingerprint and "/" not in dsn:
        host, port = dsn.rsplit(":", 1)
        dsn = f"{host}/{fingerprint}:{port}"
    return {
        "dsn": dsn,
        "user": os.environ["EXA_USER"],
        "password": os.environ["EXA_PASSWORD"],
        "role_password": os.environ.get("PH_ROLE_PASSWORD", "").strip(),
        "verify_cert": os.environ.get("EXA_VERIFY_CERT", "false").strip().lower() in ("1", "true", "yes"),
        "own_bank_id": os.environ.get("OWN_BANK_ID", "BANK_X"),
    }


def connect(role: str = "admin", bank: str | None = None, **extra):
    """Open a pyexasol connection as `role`, with `bank`'s schema open (unqualified names resolve there).

    Local Exasol Personal uses a self-signed certificate, so certificate checks are off unless EXA_VERIFY_CERT=true
    (or a fingerprint is pinned in the DSN)."""
    import pyexasol

    if role not in ROLES:
        raise ValueError(f"role must be one of {ROLES}")
    s = settings()
    if role == "admin":
        user, password = s["user"], s["password"]
    else:
        if not s["role_password"]:
            raise SystemExit("Missing PH_ROLE_PASSWORD in .env (the password `make db` gives the maker, checker and "
                             "portal users). Add it, then run `make db`.")
        if role != "portal" and not bank:
            raise ValueError(f"the {role} works for one bank: pass bank=")
        user, password = role_user(role, bank), s["role_password"]
    schema = PORTAL_SCHEMA if role == "portal" else (schema_for(bank) if bank else None)
    sslopt = {"cert_reqs": ssl.CERT_REQUIRED if s["verify_cert"] else ssl.CERT_NONE}
    kwargs = {"schema": schema} if schema else {}
    try:
        return pyexasol.connect(
            dsn=s["dsn"],
            user=user,
            password=password,
            encryption=True,
            websocket_sslopt=sslopt,
            compression=True,
            **kwargs,
            **extra,
        )
    except pyexasol.ExaError as exc:
        if role != "admin" and "authentication" in str(exc).lower():
            raise SystemExit(f"Cannot log in as {user}: run `make db` (it creates the users with PH_ROLE_PASSWORD "
                             f"from .env). {exc}") from exc
        raise


def own_bank() -> str:
    return settings()["own_bank_id"]


def audit(conn, event_name: str, case_id: str | None = None, detail: str | None = None,
          actor: str = "maker", sim_at: str | None = None, account_id: str | None = None) -> None:
    """Log a step right after it ran, with the step's statement ID, in the open schema's AUDIT_LOG (append-only).

    CURRENT_STATEMENT inside this INSERT is the step's statement + 1 because nothing else ran in between on this
    connection. Steps that write result tables also stamp CURRENT_STATEMENT on every row, which is exact.
    """
    conn.execute(
        """
        INSERT INTO AUDIT_LOG (actor, event_name, case_id, account_id, detail, sim_at, session_id, stmt_id)
        SELECT {actor}, {event_name}, {case_id}, {account_id}, {detail},
               TO_TIMESTAMP({sim_at}, 'YYYY-MM-DD HH24:MI:SS'), CURRENT_SESSION, CURRENT_STATEMENT - 1
        """,
        {"actor": actor, "event_name": event_name, "case_id": case_id, "account_id": account_id,
         "detail": None if detail is None else str(detail)[:2000],
         "sim_at": None if sim_at is None else str(sim_at)[:19]},
    )