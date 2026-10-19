# Contributing

1. `make install`, then `make check` must stay green on every commit (CI runs it).
2. Business rules come from `docs/REQUIREMENTS.md` sections 4 and 9. If a rule is unclear, add an entry to
   `docs/DECISIONS.md` instead of guessing in code.
3. Any change to attribution must keep `tests/oracle.py` and the Exasol UDF in agreement. Change the oracle
   first, by hand, with a test; then the UDF.
4. SQL files are numbered (`NNN_name.sql`), idempotent, and every table and column has a `COMMENT`.
5. Never commit `.env`, credentials, AMLworld CSVs or Exasol binaries.
6. Never add an auto-approve path: the tool recommends; an officer decides.