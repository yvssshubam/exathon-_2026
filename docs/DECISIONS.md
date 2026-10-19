# Decisions and spec gaps

Every place where `docs/REQUIREMENTS.md` was ambiguous, self-contradictory, or needed a concrete choice.
Status: **OPEN** needs the team's answer; **DEFAULT** is what the code does until someone says otherwise;
**VERIFY** must be checked against a running Exasol before it is relied on.

## D-01 · DECIDED · Every bank can run the tool; D stays at Bank Z

**Conflict.** Section 3 puts B at Bank Y and D (Murugan) at Bank Z. Section 9 says edges into other banks are
"kept but not expanded further". But AT-03 expects the trail to reach D and the ATM exit, both of which hang off B
(Bank Y), and AT-08 expects D to get `AMOUNT_HOLD ₹1,000` while B gets `REFER_OUT`. The demo script also has
an officer approving D's hold and issuing Murugan's notice, which only D's own bank can do.

**Decision** (matches how the real system works: each bank sees only its own accounts and CFCFRMS carries the
trail between banks).
1. The bank using the tool is a setting (`OWN_BANK_ID`, and a parameter of every case step), not a fixed bank.
   The demo can be viewed as Bank X, Y or Z.
2. The trace follows the money through every bank in the synthetic data. That shared view stands in for the
   cross-bank trail CFCFRMS already returns to each bank. Each edge records whether the receiving account belongs
   to the viewing bank.
3. The viewing bank acts only on its own accounts. For accounts at other banks it emits a referral that carries
   the victim's share of the amount sent (`REFER_OUT`, table `REFERRAL_OUT.victim_amount_inr`).
4. D stays at **Bank Z**, as section 3 says. Viewed as Bank X: A and C get holds, B and D get referrals. Viewed as
   Bank Z: D gets `AMOUNT_HOLD ₹1,000` with a drafted narrowing request (AT-08's expectation for D).

Attribution numbers do not change. Planned on top (D-17): a "share unknown" fallback when a bank in the chain does
not use the tool, and a tamper-evident hash chain on referrals.

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

## D-16 · DEFAULT · "Ask" runs on a local model, not a hosted AI service

FR-16 (investigator questions in plain English through the Exasol MCP server) is built into the officer console:
the console's Ask screen sends the question to a local model served by Ollama, which calls the read-only
`exasol-mcp-server` tools (`execute_exasol_query`, `describe_exasol_tables_and_views`) through the MCP Python SDK.
No hosted AI service and no paid API are used, so the whole stack runs offline on one laptop at no cost. The screen
always shows the SQL that ran. Because a small local model writes weaker SQL, every table and column carries a
COMMENT, and common questions are backed by plain SQL views (for example the repeat-offender view, FR-17) so the
model only has to select from a view.

## D-17 · PLANNED · Referrals that survive partial adoption and tampering

- **Share unknown.** If an incoming referral carries no victim share (the sending bank does not use the tool), the
  receiving bank treats the whole amount as possibly the victim's, holds at most that amount (never the whole
  account) and marks the hop "share unknown".
- **Hash chain.** Each referral stores a SHA-256 of its own fields plus the previous hop's hash, so a receiving
  bank, the police or a court can check that the victim's share was passed on unchanged.

## D-18 · DEFAULT · Case steps are parameterised SQL run inside Exasol

Files in `sql/` (numbered) build the database and run once with `make db`. Files in `sql/steps/` are the per-case
steps (trace, attribute, score, recommend). They take parameters such as `{case_id}` and `{own_bank_id}`, are run
by `pipeline/steps.py` (later by the API), and do all their work in Exasol; Python only passes parameters.

## D-19 · DEFAULT · When holds are placed

A case's replay and trace stop at `as_of`: by default the complaint's `reported_at` plus `hold_minutes` (3) from
`config/case_defaults.yaml`. For the demo, 11:10 + 3 minutes = 11:13, the time section 3 gives for holding C.

## D-20 · DEFAULT · One trace row per transaction

`CONNECT BY` returns one row per path, so a transaction reachable along two paths (as in the loop case) appears
twice. `TRACE_EDGE` keeps one row per transaction: its shortest hop, its first path, and `is_cycle` if any path
through it revisits an account.

## D-21 · DEFAULT (found on Exasol) · Time rules live in a link table, not in CONNECT BY

Exasol rejects non-equality conditions that mix `PRIOR` and non-`PRIOR` columns inside `CONNECT BY`
("Feature not supported: Non-equal filter with PRIOR and non PRIOR lookups"), so `t.ts >= PRIOR t.ts` cannot be
written there. The trace is therefore two statements, both in Exasol:
1. a set-based join writes `PH.TRACE_LINK` (which transaction can follow which, with the time window, ordering and
   hold-time rules applied), and
2. `CONNECT BY NOCYCLE PRIOR child_txn = parent_txn` walks that table with equality links only, bounded by
   `LEVEL <= max_hops`.
`TRACE_LINK` is kept per case, so the allowed hops can be inspected when a trail looks wrong.

## D-22 · DEFAULT · How each scoring signal is measured

All over the 90 days before the hold time, from `PH.TXN`, in `sql/steps/060_score.sql`; a Python copy in
`tests/score_oracle.py` checks the numbers.
- **Median dwell:** for each credit, minutes until the account's next debit (or until the hold time); the median.
- **Pass-through:** for each credit, the part of it matched by debits in the next 60 minutes, capped at the credit;
  summed and divided by all credits. (A plain "outflow within 60 min / inflow" would score a shop's evening sweep as
  pass-through, because some small payment always arrived in the hour before it.)
- **Complaint-linked credits:** distinct complaints whose victim paid straight into the account in the last 7 days.
  Second-layer links are left for later.
- **Large-payer share:** share of credit value from the top three payers.
- **Balance retained:** balance at the hold time divided by the victim money that came in (from the LIBR replay),
  a proxy for "lowest balance after the tainted credit".
- **Cash or crypto exits:** count of ATM, cash or crypto withdrawals.
- **Credit vs profile:** credits in the last 30 days divided by the declared band's expected monthly credits.
Dwell's honest threshold is 6 hours, not "days or weeks" as section 9 suggests, because real shops sweep takings
daily; mules forward within minutes either way.

## D-23 · DEFAULT · Account history is generated, not stored

`pipeline/gen_history.py` (seed 42) generates 90 days of history for the case accounts in memory when the cases are
loaded: about 300 small payments a day for merchants such as Murugan, a few large fast-forwarded credits for mules.
It ends before 18 Oct 2026 00:00, when the opening balances are taken, so the case-day numbers are unchanged.

## D-24 · DEFAULT · Recommendations are structured columns; the wording is generated from them

`PH.RECOMMENDATION` stores the action, amounts, band, lane, legal basis, a `reason_code` naming which rule of
requirements s9 step 4 fired, and flags for the drafted requests (narrowing, seizure), last resort and a required
officer comment, instead of a free-form `reasons_json`. `pipeline/explain.py` turns a row into plain sentences, so the
console, the customer notice and the report all word the same numbers the same way. Two further choices:
- An account at another bank gets `REFER_OUT` only when the viewing bank's own account paid it; accounts further
  down the trail get `NONE` ("the bank that paid it refers it"), matching how CFCFRMS passes the trail bank to bank.
  The victim's own payment (hop 1) is never a referral: the complaint ticket already goes to the bank it reached.
- The amount held is the amount the trail names for the account, or the attributed amount if the notice names
  none, capped at the balance (SOP 9.1(i)): A ₹500, C ₹80,000, D ₹1,000 with a request to narrow it to ₹500.