# Decisions and spec gaps

Every place where `docs/REQUIREMENTS.md` (the first spec) or `docs/DESIGN.md` (the golden-hour redesign) was
ambiguous, self-contradictory, or needed a concrete choice. D-27 onwards come from the redesign; entries it replaced
are marked **SUPERSEDED** and kept so the history stays readable.
Status: **OPEN** needs the team's answer; **DEFAULT** is what the code does until someone says otherwise;
**VERIFY** must be checked against a running Exasol before it is relied on.

## D-01 · DECIDED, UPDATED BY D-28 · Every bank can run the tool

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

## D-06 · DEFAULT · Attribution has an `as_of` cut-off (numbers updated by D-28)

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

## D-12 · SUPERSEDED (D-33: deadlines cut) · Lane A deadline anchors

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

## D-16 · SUPERSEDED (D-31) · "Ask" runs on a local model, not a hosted AI service

FR-16 (investigator questions in plain English through the Exasol MCP server) is built into the officer console:
the console's Ask screen sends the question to a local model served by Ollama, which calls the read-only
`exasol-mcp-server` tools (`execute_exasol_query`, `describe_exasol_tables_and_views`) through the MCP Python SDK.
No hosted AI service and no paid API are used, so the whole stack runs offline on one laptop at no cost. The screen
always shows the SQL that ran. Because a small local model writes weaker SQL, every table and column carries a
COMMENT, and common questions are backed by plain SQL views (for example the repeat-offender view, FR-17) so the
model only has to select from a view.

## D-17 · DEFAULT · Referrals that survive partial adoption and tampering

- **Share unknown.** If an incoming referral carries no victim share (the sending bank does not use the tool), the
  receiving bank treats the whole amount as possibly the victim's, holds at most that amount (never the whole
  account) and marks the hop "share unknown". The portal routes such an entry with the whole transfer as the
  reported amount and `share_known = FALSE`, and signs it itself so the chain goes on.
- **Hash chain.** Each referral stores a SHA-256 of its own fields plus the previous hop's hash, so a receiving
  bank, the police or a court can check that the victim's share was passed on unchanged. The portal signs a police
  ticket as it delivers it (the root); a bank signs each onward entry as it files it (083); the portal passes the
  hash on unchanged. The receiving bank's checker recomputes it (075, `referral_intact`): a changed field fails the
  check, and nothing is held automatically on that ticket. The text hashed is fixed (`pipeline/chain.py`): version,
  previous hash, acknowledgement number, victim, sender, receiver, account, UTR, amount in paise, share known, time.
  Exasol computes it with HASH_SHA256 and Python with hashlib, and the tests check that the two agree;
  `python -m pipeline.chain` walks every chain on the portal. Free-text remarks are not signed: they are untrusted
  data, never acted on.

## D-18 · DEFAULT · Case steps are parameterised SQL run inside Exasol

Files in `sql/` (numbered) build the database and run once with `make db`. Files in `sql/steps/` are the per-case
steps (trace, attribute, score, recommend). They take parameters such as `{case_id}` and `{own_bank_id}`, are run
by `pipeline/steps.py` (later by the API), and do all their work in Exasol; Python only passes parameters.

## D-19 · DEFAULT · When holds are placed (demo times updated by D-28)

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

## D-24 · SUPERSEDED (D-29) · Recommendations are structured columns; the wording is generated from them

`PH.RECOMMENDATION` stores the action, amounts, band, lane, legal basis, a `reason_code` naming which rule of
requirements s9 step 4 fired, and flags for the drafted requests (narrowing, seizure), last resort and a required
officer comment, instead of a free-form `reasons_json`. `pipeline/explain.py` turns a row into plain sentences, so the
console, the customer notice and the report all word the same numbers the same way. Two further choices:
- An account at another bank gets `REFER_OUT` only when the viewing bank's own account paid it; accounts further
  down the trail get `NONE` ("the bank that paid it refers it"), matching how CFCFRMS passes the trail bank to bank.
  The victim's own payment (hop 1) is never a referral: the complaint ticket already goes to the bank it reached.
- The amount held is the amount the trail names for the account, or the attributed amount if the notice names
  none, capped at the balance (SOP 9.1(i)): A ₹500, C ₹80,000, D ₹1,000 with a request to narrow it to ₹500.

## D-25 · SUPERSEDED (D-30, D-33) · Officer actions, clocks and notices

- `pipeline/officer.py` is the only way to place a hold. It enforces AT-09 (named officer; written reason for a
  rejection, an edited amount, a whole-account hold or a REVIEW-band approval) and writes `PH.DECISION`
  append-only, with the Exasol statement IDs behind the numbers the officer saw.
- Deadlines are computed in Exasol from `PH.LANE_RULE` (loaded from `config/lanes.yaml`): approval starts the
  `hold_placed` clocks; a grievance starts the `grievance_received` ones; clocks anchored to another deadline
  (`<rule>_due`) follow automatically.
- Decision time defaults to the case's hold time (demo: 18 Oct 2026 11:13), so the demo's dates stay fixed.
- Notices are rendered from `templates/notices/{en,hi,ta}.yaml`. The Hindi and Tamil text was drafted for the demo
  and should be checked by a native speaker before any real use. The victim's details are never passed to the
  renderer, so they cannot appear (SOP 9.1(ii)).
- `PH.COMPLAINT_CHECK` (a view) flags an incomplete notice: no amount, no legal basis (Lane A), no or unknown UTR,
  or an acknowledgement number that is not 14 digits or does not match its source (FR-02).

## D-26 · DEFAULT, UPDATED BY D-30 · The API is a thin router

`app/api/main.py` (FastAPI) opens one Exasol session per request, calls `pipeline/steps.py` (which runs the SQL in
Exasol) and `pipeline/officer.py` (which enforces the approval rules), and shapes JSON for the console. It holds no
business rules of its own and has no route that places a hold without an officer's approval. Officer identity is a
name in the request: login and permissions are production security, out of scope (requirements s1). The victim's
account is never returned by the case or account endpoints, only by the victim's own status view.
## D-27 · DECIDED · Build zone: the bank nodal officer inside the golden hour

The problem discovery (`docs/PROBLEM_DISCOVERY.md`) found that the money is decided at the first bank, in the first
hour, by a desk that keeps bank hours and has every reason to over-freeze. The build therefore leads with speed at
Layer 1 (work every ticket at once, at any hour) and keeps attribution as the precision half (size each hold to the
victim's share, with reasons). Mule detection is not the product: MuleHunter.AI and the I4C Suspect Registry cover
it, and the account signals stay only as an input to how much to hold.

## D-28 · DECIDED · The demo ticket lands at 02:13, and Murugan banks with Bank X

The fraud payment is at 01:52 and the ticket reaches Bank X at 02:13 (the "2:13 a.m. run"); holds land at 02:16
(D-19). D (Murugan's pump) moved to Bank X, so the bank running the tool holds his account and the narrowing decision
is its own (the open point in D-01). The money: A sends ₹70,000 to B at Bank Y and ₹80,000 to C; C pays Murugan
₹1,000 and withdraws ₹50,000 before the ticket; C cashes out the rest at 04:30 and A at 05:10 if nobody acts. C starts
with ₹500 of its own, which under LIBR leaves first, inside the diesel payment: so only ₹500 of Murugan's ₹1,000 is
the victim's. Expected numbers are in `data/demo_case.yaml` and asserted by the tests.

## D-29 · DEFAULT · Actions carry who does them: AUTOMATIC or OFFICER

`PH.CASE_ACTION` replaces `PH.RECOMMENDATION`. Each row is an action code, a mode and a status. Automatic:
`MANDATORY_HOLD`, `HOLD_MARKER`, `RECORD_EXIT`, `ONWARD_ENTRY` (plus the KYC draft). Officer: `PROPOSE_NARROW`,
`PROPOSE_FULL_FREEZE`, `CONFIRM_HOLD`. One account can have both (Murugan: the ₹1,000 hold is done, narrowing to ₹500
waits). The facts the rules read are written first to `PH.CASE_ACCOUNT`, so a wrong action can be traced to a fact.
Thresholds and legal bases live in `config/rules.yaml`; `tests/rules_oracle.py` applies the same table in Python.

## D-30 · DECIDED · Mandatory holds are placed by the agent, not approved by the officer

The first spec had an officer approve every hold. The redesign follows the SOP: the bank cannot refuse an alert and
must hold the reported amount or the part available, so waiting for approval only loses the golden hour. The agent
places exactly that hold (amount only, `placed_by = 'agent'`) and nothing wider. Narrowing, full freezes and
borderline accounts wait for an officer; the hold ledger keeps every replaced hold. Releasing a mandatory hold stays
with the bank's Department Head and is out of scope.

## D-31 · DEFAULT · The model orchestrates and writes; it never calculates or decides

The agent runs in a fixed order by default. In `llm` mode a local Ollama model picks the next tool from those the
runtime allows (prerequisites + the checker), and words the summary. A disallowed pick is overruled; any failure hands
control to the fixed order; text whose rupee figures are not all in the facts it was given is discarded. No paid API
(the project spends nothing) and no data leaves the laptop.

## D-32 · DEFAULT · The checker runs in SQL before anything changes

`075_check.sql` writes six checks to `PH.CHECK_RESULT`. The change-making SQL files themselves refuse to act unless
check rows exist for the case and none failed, so a bug in the Python orchestration cannot bypass the checker.
Conservation compares the victim's root payment with the victim money found on the trail, to the paisa.

## D-33 · DECIDED · Cut: Lane B, notices, deadlines, victim view, investigator Q&A, FIFO on screen

Reasons in `docs/DESIGN.md`. `config/lanes.yaml`, `templates/notices/`, `PH.LANE_RULE`, `PH.DEADLINE` and
`PH.NOTICE` are gone (the schema drops them on rebuild). Every ticket is Lane A; the two generated Lane B tickets are
now Lane A. FIFO and pro rata are still computed by the UDF and tested; only LIBR is shown.

## D-34 · SUPERSEDED by D-46 · Onward entries and exits are filed automatically; KYC packets wait

Both are mandatory reports with no discretion in them, and the next bank's golden hour depends on the onward entry.
KYC packets (due within a week) are drafted at once and sent by the officer. KYC data is synthetic, derived from the
account ID (`SYNTH....` PAN placeholders, example.com e-mail).

## D-35 · DEFAULT · Today's desk is a replay in Exasol, cut at each account's desk time

For Compare, `090_today.sql` reruns the attribution UDF (LIBR) over the accounts the trail reached, cut at the moment
today's desk would reach each own-bank account (desk opens 10:00, 20 minutes for the first lookup, 15 per account,
whole-account freezes; `data/demo_case.yaml` `today:`). It does not re-trace accounts the money reached only after
the ticket, which can only flatter today's desk. Victim money held counts each account's widest hold once.

## D-36 · DEFAULT · The ticket queue and the simulated clock

All complaints load into `PH.TICKET` as QUEUED with their arrival time. The agent works a ticket "at" its arrival
time: holds and entries carry the simulated clock (arrival + `hold_minutes`), while the run's real duration is stored
separately (`AGENT_RUN.wall_seconds`). Officer decisions carry the real time they were made.

## D-37 · DEFAULT · AMLworld is background traffic that ends where the cases start

`make data` loads IBM AMLworld HI-Small (about 5 million transactions) into schema `PH_AML`, and `make seed` copies
it into `PH.TXN`, `PH.ACCOUNT` and `PH.BANK` next to the cases, so every step runs against a full-size table.

* **Time.** Every AMLworld timestamp moves forward by the same number of days so its last day is 17 Oct 2026, the
  day before the case day, as with the generated history (D-23). The trace starts at the victim's payment, so it
  never walks into the background, and the case-day numbers do not change.
* **Money.** AMLworld mixes 15 currencies. Each currency's rate to the US dollar is read from the file itself (the
  median of Amount Received / Amount Paid over rows that convert to or from dollars), then one fixed rate turns
  dollars into rupees (`config/amlworld.yaml`, ₹95, about the market rate in October 2026, the month of the case
  day). Illustrative, not live market data.
* **Channels.** ACH → NEFT, Wire → RTGS, Cheque → CHEQUE, Credit Card → CARD, Cash → CASH, Bitcoin → CRYPTO.
  Every AMLworld row names a receiving account, so none is an exit. Reinvestment rows (an account paying itself)
  are not loaded.
* **Accounts.** AMLworld has no opening dates or declared income. Opening dates are spread from 1 to 10 years
  before 17 Oct 2026, fixed per account, and every profile band is `medium`, so the account-age and
  credit-vs-profile signals stay neutral instead of inventing evidence. Holder type comes from the entity name
  (sole proprietorship → merchant, corporation or partnership → business, otherwise individual).
* **IDs.** `AML_<bank>_<account>`, `AML_<bank>`, `AML<n>` and `AMLW<n>`, so nothing collides with the cases.
  Bank codes lose their leading zeros, because the transactions file writes `010` where the accounts file writes
  `10`.
* **Persistence.** `PH_AML` survives `make db` and `make demo-reset`; the import runs once, the copy into `PH` on
  every reset (a few seconds in Exasol). `python -m pipeline.load_cases --no-amlworld` leaves it out.

## D-38 · SUPERSEDED by D-47 · The watch replays the day after the holds, in time order

The watch is a replay over the simulated clock, not a background process: after the agent works its tickets it reads
every later transaction on the watched accounts from `PH.TXN` and treats each as an attempt. A debit is refused when
it needs money under a lien active at that moment (or the account is frozen); a refused debit is dropped, so its
receiver never gets the money. Balances start from the LIBR balance at the first hold (`PH.CASE_ACCOUNT`). Holds that
officers place later carry the real clock, so they do not affect the replayed night. Accounts outside the trails are
named by their bank only, since a payer may be a victim whose ticket has not arrived yet. Alerts are recorded, not
sent: the build has no messaging channel. Running the watch again replaces its events and its marker holds.

## D-39 · SUPERSEDED by D-48 · The agent drafts a freeze's reasons; the officer signs them

Courts ask for the material behind a whole-account freeze, so approving one needs written reasons. Opening the
freeze form fetches a draft (`GET /api/accounts/{account}/reasons-draft`) built from the database: tickets naming the
account and the victims' money traced into it, the scoring step's signal values, and up to four watch events
(refused attempts first). The model may reword it only if every number survives unchanged, a stricter check than
for summaries (percentages, day counts and clock times count too). The draft is a starting point: the officer can
edit or replace it, and only the text they approve is stored. Each draft served is written to the audit log with
who wrote it (model or template).

## D-40 · SUPERSEDED by D-49 (dropped) · A mule network is a connected group of mule-like accounts that pay each other

Links are deliberately narrow, so each one can be stated as a fact: money moved directly between two candidate
accounts (any amount, 30 days back from the latest ticket) or both cashed out at the same ATM or wallet. Sharing a
victim is not a link on its own, because an honest receiver can share a victim with a mule. Only mule-like accounts
(MULE or REVIEW band, or a freeze waiting) can be members, so an honest customer such as Murugan is never pulled into a
network by the money that reached him. Grouping is union-find in Python over links found in Exasol. A network adds no
power: its single approval runs the same officer approval on each member's waiting freeze, with the officer's name and
the reasons on each.

## D-41 · SUPERSEDED by D-45 · Notices are checked, never acted on alone

A police notice that reaches the desk by e-mail or post is read, checked and either matched to its ticket or sent
back. It never places a hold by itself: holds come from tickets, whose trail the agent traces. A notice is sent back
when it gives no UTR, a UTR that is not a payment into this bank, an amount or date that differs from the transaction,
or no legal provision; the letter names each problem and, for a UTR that is not found, the nearest UTR received that
day (`EDIT_DISTANCE` of 2 or less) with its amount and time. A demand to freeze the whole account is noted for the
officer, since under the SOP the bank holds the amount and a whole-account freeze needs an officer's written reasons.
The model's reading counts only where the notice's own text bears it out (a UTR, reference or name found verbatim,
an amount or date the parser also finds), so a misread cannot make a wrong notice look right. Letters are recorded
as sent with the officer's name; the build sends no e-mail.

## D-42 · DECIDED · The officer decides four things; everything else is automatic

Narrow a hold to the victims' share, widen to a whole-account freeze, accept or leave an unmatched notice, release
after 75 days on the watch with no police direction. Mandatory holds, exits, onward entries, KYC packets, send-backs,
duplicate links, the victim split and the watch are automatic. The "confirm a borderline hold" decision is gone: a
mandatory hold stands on the notice, and borderline evidence only shapes the reasons. The freeze proposal now needs
the pattern courts accept from repeated tickets (`full_freeze_evidence.complaint_links_min` in `config/rules.yaml`)
at the first pass, or a refused cash-out on a mule-like watched account later; one ticket is not a pattern.

## D-43 · DECIDED · Maker proposes, checker acts, with separate database users

The agent is the maker: its database user can read the bank's schema and write only its work tables and
`PROPOSAL`. The checker is deterministic code with its own user: it reruns the checks, validates every proposal
against what the rules engine produced (same account, same action, same amount), and only then holds, files, links or
routes it to the officer. Whatever a model asks for is a proposal and can be refused; a model-only proposal is never
carried out. The approver is the officer, through the checker. The audit log is insert-only for both users.

## D-44 · DECIDED · One schema and two users per bank; the portal is the only link

Each simulated bank has its own schema (PH_X, PH_Y, PH_Z) built from the same SQL, and sees only rows touching its
own accounts. The simulated CFCFRMS is schema PH_PORTAL with its own service user: it delivers tickets into a bank's
inbox when the clock reaches them, collects each bank's outbox, and turns an onward entry into the next bank's ticket
a minute later. Bank X and Bank Y run the agent; Bank Z runs the same code in fixed mode when its desk opens at 10:00.
The narrator's view reads every schema as the admin and is captioned "Narrator's view: no single bank sees this."

## D-45 · DEFAULT · Intake matches the transaction, not just the account

A ticket is acted on only when its UTR is a payment into the account it names, for the amount (or less, for an
onward share) and within 15 minutes of the time it gives. A same-account, same-amount payment within two hours with
a different UTR is a near match: nothing is held, the ticket is sent back at once, and the officer may accept the
near match (the ticket is then worked as matched). Anything else is sent back. A ticket for a payment already in an
earlier case is a duplicate and is linked, never held twice; the investigating officer's notice after an FIR is the
modelled case.

## D-46 · DEFAULT · KYC packets are filed in the same run

The SOP allows a week; the agent files each held account's packet with the holds, so nothing waits on the officer.

## D-47 · DEFAULT · The watch acts on what core banking refuses, and on new money

Payments on held or watched accounts go through simulated core banking in time order. A refused cash-out on a
mule-like watched account produces a full-freeze proposal; a refused transfer to an account at the same bank puts that
account on the watch with no hold (no victim money reached it); a refused transfer to another bank is recorded for
the police. New money on a mule-like watched account is held under the existing notice up to the amount reported,
tagged "not linked to a reported victim yet"; when that victim's ticket arrives, the hold is linked to them and the
total does not change. New money on an honest receiver is never held. Only cases that share an account with the new
ticket are recalculated.

## D-48 · DEFAULT · The rules decide the reasons; the model only words them

Reasons are a structured list (the support, the case against, the numbers) built from the rules engine and the
evidence. The model's sentences must each cite the items they use, repeat no number those items do not hold, and
include the case against; otherwise the fixed template is used. Citations come from the fixed list. Mandatory holds
are always template-worded, and the holder is told only the authority, the notice and the amount.

## D-49 · DECIDED · Dropped: mule networks, the data Q&A, emailed letter reading

Networks are the mule detector we said we wouldn't build, and they sweep in honest receivers; cross-ticket evidence
stays as one signal. The Q&A had no pain behind it. Letters arrive through the portal as tickets.

## D-50 · DEFAULT · The checker's conservation check counts the bank's whole view

At a bank that sees only its own accounts, a victim's money can leave through another bank and come back into one
of its accounts by a hop it cannot see. The money is still the victim's and still in the bank's view, so
conservation sums the victim's money in every account and exit of the bank's attribution, not only on its own trail.

## D-51 · DEFAULT · One learned signal next to the eight, never in their place

Requirements section 9 allows a model to add one signal and never to replace the list. `python -m pipeline.learned`
fits a logistic regression (numpy, classes balanced) on AMLworld's labels: accounts that sent a payment marked as
laundering against those that did not. The model sees the case-independent signals that AMLworld really holds
(dwell, pass-through, payer share, withdrawals, credits) and five connection inputs (distinct payers and payees, money
out against in, number of payments, share of payees that also paid it), computed by the same SQL in training and at
the bank. The connection inputs feed only the model: they are never signals of their own. Account age and profile are
made up by the import, the complaint links and balance belong to a ticket, and the payment format is left out because
in AMLworld it goes with laundering in a way that says nothing about UPI mules. One account in five is held out by a hash of its ID, and the report
(docs/LEARNED_SIGNAL.md) sets the model's AUC next to the hand-set weights on the same accounts, whichever wins, and
next to gradient-boosted trees (200 trees of depth 2, plain numpy, the XGBoost family). Either can be deployed
(`--model logistic` or `--model trees`): the logistic regression is one formula an auditor can read; the trees rank
better but a rating is a sum of small trees. Both are scored in Exasol SQL, the trees from LEARNED_TREE, from the
inputs kept in LEARNED_INPUT so any rating can be checked. Split points sit between data values, so Exasol and Python
always take the same branch. A retrained model
replaces the saved one only if it ranks the held-out accounts better than both the hand-set weights and the saved
model; the feature list is fixed before the run, never tuned against the test.
The coefficients are loaded into each bank's LEARNED_COEF and scored in Exasol by the same step as the eight. It has
the smallest weight (0.05 of 1.05), so it can move a score a little but cannot flip a demo band on its own (tested),
and its sentence says it was trained on synthetic data. The AMLworld lab test leaves it out, since it was trained on
the same data. No model file: the eight signals alone, as before.