"""One place that knows how to reach Exasol. Credentials come only from .env (NFR-05).

DSN, user and password are read from `exasol info` once and put in .env; nothing is hard-coded,
including the port (requirements s7, "Unverified, check on first setup").
"""
from __future__ import annotations

import os
import ssl
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


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
        "verify_cert": os.environ.get("EXA_VERIFY_CERT", "false").strip().lower() in ("1", "true", "yes"),
        "own_bank_id": os.environ.get("OWN_BANK_ID", "BANK_X"),
    }


def connect(**extra):
    """Open a pyexasol connection. Local Exasol Personal uses a self-signed certificate, so
    certificate checks are off unless EXA_VERIFY_CERT=true (or a fingerprint is pinned in the DSN)."""
    import pyexasol

    s = settings()
    sslopt = {"cert_reqs": ssl.CERT_REQUIRED if s["verify_cert"] else ssl.CERT_NONE}
    return pyexasol.connect(
        dsn=s["dsn"],
        user=s["user"],
        password=s["password"],
        encryption=True,
        websocket_sslopt=sslopt,
        compression=True,
        **extra,
    )


def audit(conn, event_name: str, case_id: str | None = None, detail: str | None = None,
          actor: str = "pipeline") -> None:
    """Log a pipeline step right after it ran, with the step's statement ID (NFR-06).

    CURRENT_STATEMENT inside this INSERT is the step's statement + 1 because nothing else ran in
    between on this connection (DECISIONS D-13). Steps that write result tables
    also stamp CURRENT_STATEMENT on every row, which is exact.
    """
    conn.execute(
        """
        INSERT INTO PH.AUDIT_LOG (actor, event_name, case_id, detail, session_id, stmt_id)
        SELECT {actor}, {event_name}, {case_id}, {detail}, CURRENT_SESSION, CURRENT_STATEMENT - 1
        """,
        {"actor": actor, "event_name": event_name, "case_id": case_id, "detail": detail},
    )