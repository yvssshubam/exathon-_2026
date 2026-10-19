# Decisions and spec gaps

Every place where `docs/REQUIREMENTS.md` was ambiguous, self-contradictory, or needed a concrete choice.
Status: **OPEN** needs the team's answer; **DEFAULT** is what the code does until someone says otherwise;
**VERIFY** must be checked against a running Exasol before it is relied on.

## D-01 · OPEN · Which bank is D in, and does the trace continue through other banks?

**Conflict.** Section 3 puts B at Bank Y and D (Murugan) at Bank Z. Section 9 says edges into other banks are
"kept but not expanded further". But AT-03 expects the trail to reach D and the ATM exit, both of which hang off B
(Bank Y), and AT-08 expects D to get `AMOUNT_HOLD ₹1,000` while B gets `REFER_OUT`. The demo script also has
Bank X's officer approving D's hold and issuing Murugan's notice, which only Bank X could do if D were its customer.

**Proposed default.**
1. The trace expands through every bank present in the synthetic data. This stands in for the cross-bank trail
   CFCFRMS already returns to each bank (requirements section 3: tracing exists, attribution is the gap). Each
   edge carries `is_own_bank` of the receiving account.
2. Actions follow `is_own_bank`: own-bank accounts get hold actions; other-bank accounts get `REFER_OUT` carrying
   the attributed amount for that bank.
3. Move D to **Bank X** (one line in `data/demo_case.yaml`), so AT-08 and the demo script hold.

Attribution numbers do not change under any option. Only actions, notices and the trail screen do.

## D-02 · DEFAULT · The A→E→A loop for AT-03 lives in its own case

Adding a loop through `ACC_A` would change the section 9 numbers. The loop is seeded as a separate case
(`CASE_LOOP`, accounts `ACC_LOOP_A`, `ACC_LOOP_E`) so the demo case stays exact.

## D-03 · DEFAULT · Money is integer paise inside the algorithms

Oracle and UDF compute in integer paise and split with largest-remainder rounding (ties broken by component name,
`clean` first), so every split sums exactly to the amount and conservation holds to the paisa, not just the rupee.
Tables keep `DECIMAL(18,2)` rupees. This replaces the `DOUBLE` suggestion in section 7, which cannot guarantee
NFR-03.

## D-04 · DEFAULT · FIFO keeps the sender's order inside a mixed credit

When a credit carries both clean and tainted money (B receives ₹500 of A's float plus ₹1,19,500 of the victim's),
the receiving account appends the pieces as separate lots in the order they left the sender. On the demo case
FIFO therefore equals LIBR. The alternative (treat a mixed credit as one lot, split pro rata inside it) gives
fractional-paise splits that are hard to explain to an officer.

## D-05 · DEFAULT · Unknown opening balances

If an outflow exceeds what the replay knows the account holds, the shortfall is treated as clean money that was
in the account before the window, and recorded as `opening_shortfall`. Tainted money is never created or
destroyed by this, so conservation still holds. Relevant for AMLworld background accounts.

## D-06 · DEFAULT · Attribution has an `as_of` cut-off

The replay includes transactions up to `as_of`. With the tool, `as_of` = the moment holds are placed
(demo: 18 Oct 2026 11:13), so C's 12:30 cash-out is excluded and C still holds ₹80,000. The baseline uses each
hop's own action time (C at 13:05, after the cash-out).

## D-07 · DEFAULT · "Notice amount" per account

Section 9 step 4 says "AMOUNT_HOLD of the notice amount". Per account, the notice amount is the sum of traced
inflows into that account named by the trail (D: ₹1,000; C: ₹80,000), capped at the available balance
(SOP para 9.1(i): "the reported amount, or the part of it available").

## D-08 · VERIFY · `CONNECT_BY_ISCYCLE` will not fire on time-ordered loops

`CONNECT_BY_ISCYCLE` flags a *row* that would become its own ancestor. With `t.ts >= PRIOR t.ts` a money loop
A→E→A at increasing times never revisits the same row, so the flag stays 0 and AT-03's `is_cycle = 1` would fail.
Plan: keep `NOCYCLE` (it is still the hang guard), and set `is_cycle = 1` when the receiving account already
appears in `SYS_CONNECT_BY_PATH` or when `CONNECT_BY_ISCYCLE = 1`. Branches are not cut at a revisit, because money
really can come back. Confirm on Exasol with the `CASE_LOOP` fixture.

## D-09 · VERIFY · Interval and bind syntax

- `PRIOR t.ts + INTERVAL '72' HOUR` in section 9: if Exasol rejects it inside `CONNECT BY`, use
  `ADD_HOURS(PRIOR t.ts, 72)`.
- pyexasol does not take `:name` binds; it formats `{name}` placeholders client-side with escaping
  (`{name!s}` for identifiers is not used). SQL files use `{case_id}`-style placeholders.

## D-10 · DEFAULT · UDF body is a real Python file, tested without Exasol

`sql/udf/attribute_case.py` holds the `run(ctx)` body; `pipeline/run_sql.py` inlines it into
`CREATE OR REPLACE PYTHON3 SET SCRIPT ... AS ... /` when `make db` runs. CI tests it with a fake `ctx`. It is
written independently of `tests/oracle.py` so the comparison means something.

## D-11 · DEFAULT · Dates and IDs

The demo case is dated **Sun 18 Oct 2026**, from UTR `IMPS2026101810020001`. Exits are pseudo-accounts named
`EXIT:<channel>:<identifier>` (for example `EXIT:ATM:BY-ATM-0042`) so the trail graph can draw them; they are
never rows in `ACCOUNT` and never held.

## D-12 · DEFAULT · Lane A deadline anchors

`config/lanes.yaml` anchors each Lane A deadline to the event that starts it (hold placed, grievance received,
grievance forwarded). Until a grievance is forwarded, the officer-decision date is projected from the grievance
received date, assuming same-day forwarding. AT-11's 7 / 15 / 90 days are offsets from those anchors.

## D-13 · VERIFY · How statement IDs reach AUDIT_LOG

`pipeline/db.py:audit()` logs a step right after it runs with `CURRENT_STATEMENT - 1`, assuming nothing else ran
on that connection in between. Result tables (`TRACE_EDGE`, `ATTRIBUTION`, ...) are stricter: each row stores the
`CURRENT_STATEMENT` of the INSERT that produced it, which is exact. Check on first setup that `stmt_id` values line
up with `EXA_DBA_AUDIT_SQL` / `EXA_STATISTICS` where available.

## D-14 · DEFAULT · The attribution replay covers every victim touching the same accounts

`ATTRIBUTION.other_victims_inr` only makes sense if the replay sees all complaints whose money mixed. The UDF
therefore groups by the connected set of traced accounts, not by a single case, and tags lots per victim. The
oracle does the same (`tests/test_conservation.py::test_all_cases_together_are_conserved`).

## D-15 · DEFAULT · FIFO splits several victims chronologically, LIBR and pro rata split them pro rata

FR-07 asks for a pro rata split between victims (SOP 5(xiii)). LIBR (the default for holds) and PRO_RATA do
that. FIFO is kept as a tracing rule in its own right, so it splits by arrival order; the report prints the
rule used. AT-06 is tested under LIBR and PRO_RATA.