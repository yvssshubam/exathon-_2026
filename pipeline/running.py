"""What is Exasol running right now? Run it in a second terminal when something seems stuck.

  python -m pipeline.running
"""
from __future__ import annotations

import sys


def main() -> int:
    from pipeline.db import connect

    conn = connect()
    me = conn.session_id()
    rows = conn.execute(
        "SELECT SESSION_ID, STATUS, DURATION, SQL_TEXT FROM EXA_DBA_SESSIONS "
        "WHERE STATUS NOT IN ('IDLE') ORDER BY DURATION DESC").fetchall()
    busy = [r for r in rows if str(r[0]) != str(me)]
    if not busy:
        print("Exasol is idle: nothing is running.")
    for session, status, duration, sql in busy:
        print(f"session {session}  {status}  running for {duration}")
        print("  " + " ".join((sql or "").split())[:600])
        print()
    conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())