"""`make doctor`: is everything needed for the next step working? Prints PASS/FAIL with a fix for each.

Checks, in order (stop reading at the first FAIL; later checks depend on it):
  1. the `exasol` launcher is installed, and `exasol status` / `exasol info` answer
  2. a Python 3 script language container is installed (`exasol slc list`)
  3. .env exists and has EXA_DSN / EXA_USER / EXA_PASSWORD
  4. pyexasol connects and reports the database version
  5. a Python 3 UDF actually runs, and which packages the SLC has (pandas? numpy?)
  --full also runs `exasol diag local`.
"""
from __future__ import annotations

import argparse
import shutil
import subprocess
import sys

from pipeline.db import ROOT

results: list[tuple[str, bool]] = []


def check(name: str, ok: bool, detail: str = "", fix: str = "") -> bool:
    results.append((name, ok))
    print(f"{'PASS' if ok else 'FAIL'}  {name}" + (f"  — {detail}" if detail else ""))
    if not ok and fix:
        print(f"      fix: {fix}")
    return ok


def run(cmd: list[str], timeout: int = 60) -> tuple[int, str]:
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return p.returncode, (p.stdout + p.stderr).strip()
    except (OSError, subprocess.TimeoutExpired) as exc:
        return 1, str(exc)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--full", action="store_true", help="also run `exasol diag local`")
    args = ap.parse_args(argv)

    if shutil.which("exasol"):
        code, out = run(["exasol", "status"])
        check("exasol status", code == 0, out.splitlines()[0] if out else "",
              "start the database: `exasol start` (or `exasol install local` the first time)")
        code, out = run(["exasol", "info"])
        print("      exasol info:\n" + "\n".join("        " + line for line in out.splitlines()[:20]))
        code, out = run(["exasol", "slc", "list"])
        check("Python 3 SLC installed", code == 0 and "python" in out.lower(), out.replace("\n", " | ")[:120],
              "exasol slc install python3")
        if args.full:
            code, out = run(["exasol", "diag", "local"], timeout=300)
            print(out)
    else:
        check("exasol launcher on PATH", False, "",
              "curl https://downloads.exasol.com/exasol-personal/installer.sh | sh  (macOS/Linux); reopen the shell")

    env = ROOT / ".env"
    if not check(".env present", env.exists(), str(env), "cp .env.example .env, then fill EXA_DSN from `exasol info`"):
        return summary()

    try:
        from pipeline.db import connect

        conn = connect()
    except SystemExit as exc:
        check("settings", False, str(exc))
        return summary()
    except Exception as exc:
        check("pyexasol connect", False, str(exc)[:200],
              "check EXA_DSN host:port from `exasol info`; is the database running? try EXA_FINGERPRINT")
        return summary()

    version = conn.execute(
        "SELECT PARAM_VALUE FROM EXA_METADATA WHERE PARAM_NAME = 'databaseProductVersion'"
    ).fetchval()
    check("pyexasol connect", True, f"Exasol {version}, session {conn.session_id()}")

    try:
        conn.execute("CREATE SCHEMA IF NOT EXISTS PH_STAGE")
        conn.execute(
            "CREATE OR REPLACE PYTHON3 SCALAR SCRIPT PH_STAGE.DOCTOR_SMOKE() RETURNS VARCHAR(200) AS\n"
            "import sys\n"
            "def run(ctx):\n"
            "    import importlib.util as u\n"
            "    have = [m for m in ('pandas', 'numpy', 'scipy', 'yaml') if u.find_spec(m)]\n"
            "    return 'python ' + sys.version.split()[0] + '; has: ' + ','.join(have)\n"
        )
        out = conn.execute("SELECT PH_STAGE.DOCTOR_SMOKE()").fetchval()
        check("Python 3 UDF runs", True, out)
    except Exception as exc:
        check("Python 3 UDF runs", False, str(exc)[:200], "exasol slc install python3, then retry")
    conn.close()
    return summary()


def summary() -> int:
    failed = [n for n, ok in results if not ok]
    print("\n" + ("ALL GOOD" if not failed else f"{len(failed)} problem(s): " + ", ".join(failed)))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())