#!/bin/sh
# make setup — Exasol Personal + Python 3 SLC + Python deps, on macOS or Linux (requirements s7).
# Safe to re-run: each step checks whether it is already done.
# Windows: install the launcher from the Exasol download portal, then follow the same steps in PowerShell.
set -eu
cd "$(dirname "$0")/.."

say() { printf '\n==> %s\n' "$1"; }

say "1/5 Exasol launcher"
if command -v exasol >/dev/null 2>&1; then
  echo "found: $(command -v exasol)"
else
  echo "installing the exasol launcher (MIT) from downloads.exasol.com"
  curl -fsSL https://downloads.exasol.com/exasol-personal/installer.sh | sh
  echo "If 'exasol' is still not found, open a new shell (the installer updates PATH) and re-run make setup."
  command -v exasol >/dev/null 2>&1 || exit 1
fi

say "2/5 Local database"
if exasol status >/dev/null 2>&1; then
  echo "database already installed:"; exasol status || true
else
  echo "running: exasol install local   (needs Podman on Linux; macOS 15+ on Apple Silicon, 8 GB RAM)"
  exasol install local
fi

say "3/5 Python 3 script language container (local installs ship without one)"
if exasol slc list 2>/dev/null | grep -qi python; then
  exasol slc list
else
  exasol slc install python3
fi

say "4/5 Python packages"
PY="${PY:-python3}"
"$PY" -c 'import sys; assert sys.version_info >= (3, 11), "need Python 3.11+"'
if [ ! -d .venv ]; then "$PY" -m venv .venv; fi
. .venv/bin/activate
pip install -q -r requirements-dev.txt

say "5/5 Connection settings"
if [ ! -f .env ]; then cp .env.example .env; echo "created .env from .env.example"; fi
echo "Copy the host, port and (if shown) fingerprint from 'exasol info' into .env:"
exasol info || true

cat <<'EOF'

Next:
  . .venv/bin/activate
  make doctor     # every line should say PASS
  make check-db   # schema + demo cases + DB tests
EOF