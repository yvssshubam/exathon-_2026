-- 015_smoke_udf.sql — proves the Python 3 script language container works (requirements s7).
-- Local Exasol Personal ships without an SLC: run `exasol slc install python3` first (make setup does).
-- Script bodies end at a line holding only "/" (exasol connect convention; pipeline/run_sql.py honours it).

CREATE OR REPLACE PYTHON3 SCALAR SCRIPT PH.SMOKE_PYTHON() RETURNS VARCHAR(200) AS
import sys
def run(ctx):
    return "python " + sys.version.split()[0]
/

-- Lists the packages inside the installed SLC; the local version may differ from the docs (s7 Unverified).
CREATE OR REPLACE PYTHON3 SCALAR SCRIPT PH.SLC_PACKAGES() EMITS (package_name VARCHAR(200), package_version VARCHAR(100)) AS
import importlib.metadata as md
def run(ctx):
    seen = set()
    for d in md.distributions():
        name = d.metadata["Name"] or "?"
        if name.lower() in seen:
            continue
        seen.add(name.lower())
        ctx.emit(name, d.version or "?")
/