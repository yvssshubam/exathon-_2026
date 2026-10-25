"""Small helpers for the database tests (`make check-db`, or `python -m pytest -m db`). They need Exasol Personal
running, `make db` and `make seed` done, and PH_ROLE_PASSWORD in .env."""
from __future__ import annotations

from decimal import Decimal

import pytest


def connect_or_skip(role: str = "admin", bank: str | None = None):
    try:
        from pipeline.db import connect

        return connect(role, bank)
    except SystemExit as exc:                    # missing password or users: say what to run
        pytest.skip(str(exc))
    except Exception as exc:                     # noqa: BLE001 - no database here
        pytest.skip(f"no Exasol connection: {exc}")


def q(conn, sql: str, **params) -> list[tuple]:
    return conn.execute(sql, params).fetchall()


def one(conn, sql: str, **params):
    return conn.execute(sql, params).fetchval()


def d(v) -> Decimal:
    return Decimal(str(v)) if v is not None else Decimal(0)


def ts(v) -> str:
    return str(v)[:19]


def refused(conn, sql: str, **params) -> bool:
    """True when Exasol refuses the statement (no privilege, or the object is out of reach)."""
    import pyexasol

    try:
        conn.execute(sql, params)
    except pyexasol.ExaQueryError:
        return True
    return False