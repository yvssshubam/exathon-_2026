# Contributing

1. `make install`, then `make check` must stay green on every commit (CI runs it). If you touch the database side,
   run `make check-db` against Exasol Personal too.
2. The design is in `docs/DESIGN.md` and every decision in `docs/DECISIONS.md` (later entries override earlier ones).
   If a rule is unclear, add an entry to `docs/DECISIONS.md` instead of guessing in code.
3. Any change to attribution must keep `tests/oracle.py` and the Exasol UDF in agreement. Change the oracle first,
   by hand, with a test; then the UDF.
4. SQL files are numbered (`NNN_name.sql`), idempotent, and every table and column has a `COMMENT`.
5. Never commit `.env`, credentials, AMLworld CSVs or Exasol binaries.
6. Keep the split between the system and the officer: the system may place only the holds the rules require (amount
   only) and file the paperwork. Never add a path that places a whole-account freeze, reduces a hold or releases one
   without a named officer's decision, and never give the agent a tool that changes data directly.
7. A language model may order the tools and word the reasons. It must never calculate an amount or choose the
   reasons; keep the claim checks in `pipeline/reasons.py` passing.