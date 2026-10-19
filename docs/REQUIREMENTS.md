# Idea D — Proportionate Holds: Build Requirements

## 1. How to use this doc

Build a working, open-source application called **Proportionate Holds** that helps a bank's fraud desk decide, per account, how much of a cyber-fraud victim's money to hold and whether to freeze the whole account. Exasol Personal is the core data platform. The demo runs live in under three minutes.

**To the Claude instance building this:** read the whole doc before writing code. Sections 2 and 7 are hard constraints from the hackathon and from Exasol; do not trade them away. Sections 4 and 9 are the domain logic; implement them exactly. Where this doc marks something **Unverified**, check it before relying on it. Ask the team before changing scope.

**Scope in one line:** ingest synthetic transactions into Exasol, take a fraud complaint (a UTR), trace the money, attribute the victim's share per account under a chosen tracing rule, score each account mule-or-honest, recommend amount-hold or account-hold with written reasons, have an officer approve, generate the customer notice, track legal deadlines, and export the attribution report.

**Out of scope:** real bank data or any employer data; real integration with CFCFRMS, NPCI or core banking (simulate them); ML model training as the core feature (allowed only as a supporting signal); production security hardening beyond what section 12 lists.

**Hard constraints at a glance**

- Exasol Personal is the core data platform; all tracing, attribution and scoring run in Exasol (SQL and UDFs), not in the app tier.
- Only public or synthetic data.
- New work, built during the hackathon.
- Public GitHub repo under Apache 2.0 or MIT, with a README that runs end to end.
- Pitch deck and a demo video of 3 minutes or less.
- A human approves every hold; the tool recommends, it never freezes on its own.

## 2. Hackathon rules (verified on the official ExaThon page)

Every rule below comes from the [ExaThon India 2026 page](https://www.exasol.com/events/exathon-india-2026/), which is the only official rules and FAQ source. Breaking any "Rule" row disqualifies the entry.

| Area | Requirement | How this build meets it |
| --- | --- | --- |
| Rule | Exasol Personal must be the project's data platform | All data, tracing, attribution, scoring and audit live in Exasol Personal; the app tier only calls SQL and UDFs |
| Rule | Public repo, Apache 2.0 or MIT (OSI-approved) | MIT `LICENSE` at repo root; public before the submission deadline |
| Rule | Only public or synthetic data; no employer data | IBM AMLworld (public, Kaggle) plus a generated case; README states the data is synthetic and why |
| Rule | Original work created during the challenge window | Fresh repo; commit history from the build window only |
| Rule | Teams of 1 to 3 | Team list in README |
| Rule | Any language, framework, AI model or LLM | Python, FastAPI, React; Claude allowed |
| Deliverable | Problem statement: who, why it matters | `docs/PROBLEM_STATEMENT.md`, in the template below |
| Deliverable | Public code repository | GitHub |
| Deliverable | Pitch deck (PDF or PPT) | `docs/deck.pdf` |
| Deliverable | Demo video, 3 minutes maximum | Linked in README; target 2:45 |
| Deliverable | Clean README: overview, setup, usage | Section 11 |
| Deliverable | Deployment instructions and run guide | `docs/RUN_GUIDE.md` |
| Deliverable | Licence file | `LICENSE` (MIT) |
| Deliverable | Submit through the official form | [Submission form](https://www.exasol.com/events/exathon-india-2026/submit-your-project/) |
| IP | You keep ownership; Exasol may review and feature it | No action |

**Problem statement template (official):** "\[Who\] can't \[do what\] because \[why today's approach fails\]. If this worked, \[what changes for them\]." It must not name any technology, tool, model or database.

**Our statement:** Bank fraud desks can't hold exactly the money a cyber-fraud victim lost in each account it passed through, because stolen and honest money mix and the split is worked out by hand, one hop at a time. If this worked, victims would get more back sooner and honest customers would keep using their accounts.

**Judging and what each criterion means for the build**

| Stage | Criterion | Weight | Build implication |
| --- | --- | --- | --- |
| 1 | Innovation and problem impact | 25% | Lead with restoration and honest receivers, not "fraud detection" |
| 1 | Effective use of Exasol Personal | 25% | Graph trace with `CONNECT BY NOCYCLE`, Python UDFs for attribution, set-based scoring, MCP for investigators, statement IDs in the audit trail |
| 1 | Technical excellence | 20% | Tested algorithms with a hand-checked oracle; reproducible pipeline |
| 1 | Solution design and UX | 15% | Officer console, plain-language notices |
| 1 | Documentation and reproducibility | 10% | One-command setup; numbered SQL; run guide |
| 1 | Open-source quality | 5% | Licence, CONTRIBUTING, CI, clean structure |
| 2 | Live demo | 40% | The demo must run live; slides with no demo are not scored |
| 2 | Depth under Q&A | 30% | Rehearse section 4 and the limits list |
| 2 | Real-world applicability | 20% | Map every feature to the MHA SOP or RBI draft |
| 2 | Communication | 10% | 15-minute presentation, then 5 minutes of jury questions |

The separate ₹15,000 Biggest Impact Idea award goes to the most valuable problem, even with a modest build.

**Exasol Personal facts that shape the build** (details in section 7)

- Runs locally on macOS, Windows and Linux with no cloud account; AWS or Azure is optional.
- Local deployments are single-node and support a single user.
- Local UDFs need a script language container installed first: `exasol slc install python3`.

## 3. Problem, users and the demo case

The tool's job: when a complaint reaches a bank, hold exactly the victim's rupees in each account, fast, with written reasons, and leave honest money alone.

**Why it matters (sourced)**

- ₹22,495 crore lost to cyber fraud in India in 2025, across 28.15 lakh cases ([ThePrint](https://theprint.in/india/cybercrime-saw-24-spike-in-2025-indians-lost-rs-22495-crore-mainly-in-investment-scams/2859930/)).
- Of ₹7,647 crore put on hold from April 2021 to November 2025, only ₹167 crore (2.18%) had been restored to victims ([MHA SOP, para 2](https://the420.in/wp-content/uploads/2026/01/sop.pdf)).
- Rajasthan's High Court settled 105 writ petitions against account freezes in one judgment on 20 August 2026 ([MediaNama](https://www.medianama.com/2026/08/223-freeze-amount-rajasthan-hcs-cybercrime/)).
- An Andhra Pradesh wine shop's ₹8.26 lakh account was frozen over one ₹1,000 UPI payment; the High Court ordered it unfrozen on 22 June 2026 ([MediaNama](https://www.medianama.com/2026/07/223-merchants-bank-accounts-frozen-upi-payments-fraudsters-andhra-hc/)).
- Tracing tooling exists: CFCFRMS has run an automated account-to-account "money hunt" since 2021 ([LiveLaw](https://www.livelaw.in/articles/cfcfrms-reading-mha-new-account-freeze-sop-537503)). The gap is attribution of mixed money and the hold decision, not tracing alone.

**Users**

| Persona | Role | Uses the tool? | What they need |
| --- | --- | --- | --- |
| Priya | Bank nodal officer | Yes, primary user | Trace within the bank, the victim's share per account, a recommended action with reasons, deadlines, an export for police or court |
| Investigator | Bank fraud analyst or police liaison | Yes, via plain-English questions (MCP) | Ad-hoc questions over the trail and repeat-offender accounts |
| Lakshmi | Victim | No; receives a status | Where her money is, how much is held, the next step |
| Murugan | Honest receiver | No; receives a notice | Which payment, how much is held, who asked, how to respond and by when |

**The demo case (must be reproduced exactly by the seeded data)**

Invented people and numbers. Bank X runs the tool; Bank Y and Bank Z are other banks.

| Time | Event | From | To | Amount (₹) |
| --- | --- | --- | --- | --- |
| 10:02 | Victim pays the fake officer by IMPS | Lakshmi (Bank Z) | A (Bank X) | 2,00,000 |
| 10:06 | Split | A | B (Bank Y) | 1,20,000 |
| 10:06 | Split | A | C (Bank X) | 80,000 |
| 10:21 | Diesel by UPI | B | D, Murugan's pump (Bank Z) | 1,000 |
| 10:40 | ATM withdrawal | B | Exit | 50,000 |
| 11:10 | Complaint on 1930, ticket reaches Bank X |  |  |  |
| 12:30 | Cash-out | C | Exit | 80,000 |

Opening balances: A ₹500, B ₹0, C ₹0, D ₹8,19,000 of its own money, in an account 3 years old with about 300 payments a day.

**Expected outcomes the demo must show**

| Measure | Without the tool | With the tool |
| --- | --- | --- |
| Victim's money held | ₹69,500 (A ₹500 + B ₹69,000) | ₹1,49,500 (A ₹500 + B ₹69,000 + C ₹80,000) |
| Honest money locked | ₹8,19,000 (D's whole account) | ₹0 (lien on D's ₹1,000 only) |
| Time to trace every hop inside Bank X | about 2 hours (hop by hop, C actioned at 13:05) | seconds (C held at 11:13) |

The "without" path is a simulated baseline: one ticket per hop, each processed after a configurable delay, holding up to the reported amount on whatever account the trail lands, and debit-freezing any account named in a police notice.

## 4. Domain rules the tool must encode

Every hold belongs to one of two legal lanes, and each lane has its own clock. Encode both as configuration (`config/lanes.yaml`), not hard-coded constants, because the RBI text is still a draft.

**Lane A: police-instructed hold (in force).** Source: [MHA I4C SOP for NCRP-CFCFRMS, 2 Jan 2026](https://the420.in/wp-content/uploads/2026/01/sop.pdf), made judicially monitored by the Supreme Court on 9 Feb 2026 ([Razorpay summary](https://razorpay.com/blog/how-payment-gateways-handle-law-enforcement-agency-lea-hold-requests-in-india/)).

| Rule | Value | SOP reference |
| --- | --- | --- |
| Legal basis of a hold | S.168 read with S.94 BNSS; seizure of an account under S.106 BNSS needs an FIR | Para 5(i), 5(iv) |
| What is held | The reported amount, or the part of it available in the account | Para 6.1(j), 9.1(i) |
| Digital banking suspended | Only if the account is reported multiple times, on lawful direction | Para 9.1(i) |
| Insufficient balance | First-layer accounts only: a hold marked so later proceeds are caught; never on nodal, pool or escrow accounts | Para 9.1(i) |
| Money left the system | Record the exit and its identifiers (ATM ID, device, wallet) | Para 9.1(i) |
| KYC and trail update to CFCFRMS | Within 1 week | Para 6.1(k) |
| Bank forwards a grievance | Within 7 calendar days | Para 10.1(a) |
| Officer decides | 15 calendar days, else auto-escalation to the District Grievance Officer | Para 10.1(d)-(f) |
| District, then State review | 15 + 15 calendar days; bank info within 2 days | Para 10.1(f), 10.2(g) |
| Hold removal | If no lawful direction within 90 days of the grievance; bank intimates police 15 days before; police may extend by 90 days | Para 10.1 |
| Victim's interim custody | Below ₹50,000 under S.106(3); above ₹50,000 needs an FIR | Para 11, Process 1 |
| Multiple victims, mixed money | Attribute where reasonably possible; otherwise equitable or pro-rata distribution | Para 5(xiii) |
| Possibly innocent holder | The officer must consider that the account may be used without the holder's knowledge | Para 5(xii) |
| What the bank may tell the holder | The ordering police unit's address and contact; never the victim's details | Para 9.1(ii) |

**Lane B: the bank's own suspicion (draft).** Source: RBI draft KYC Amendment Directions, 11 Sep 2026, effective 1 April 2027 if finalised; comments closed 2 Oct 2026 ([MediaNama](https://www.medianama.com/2026/09/223-rbi-cyber-fraud-account-freeze-rules/), [Vilva](https://www.vilvabusiness.com/blog/mule-accounts-rbi-debit-hold-bank-account-verification-payouts)). It does not govern police-instructed holds.

| Rule | Value |
| --- | --- |
| Trigger | Monitoring, including AI/ML tools, flags a transaction of ₹1,000 or more that is out of profile or linked to a reported account |
| What is held | The flagged transaction; the whole account only for a suspected mule account, and policy must make that a last resort |
| Notice | Immediately, with reasons and an officer's contact |
| Customer explains | 20 days from the hold |
| Bank decides | 10 days after the explanation, or 30 days from the hold if none |
| Police referral | Via NCRP; police have 30 days to instruct |
| Maximum hold without police | 60 days |
| Records | At least 5 years from the hold, or 10 years from account closure |

**Court standards the reasons must satisfy**

- A label such as "mule account" or "Layer-1 account" is not, by itself, a reason to immobilise a whole account; the material behind it must be stated. The bank's nodal officer should not block mechanically and should seek clarification of unclear notices (Rajasthan HC, 20 Aug 2026).
- A police notice should carry the officer, station, reference number, legal provision, account, UTR and date, the disputed amount or the basis for a whole-account restraint, and the type of action (Rajasthan HC).
- A merchant cannot verify a UPI payer; receiving one tainted payment is not complicity (Andhra Pradesh HC, 22 Jun 2026).
- A lien on the disputed amount should be the first option (Delhi HC, Neelkanth Pharma, Feb 2025).

**Tracing rules** for money mixed with other funds ([Schneider Downs](https://schneiderdowns.com/our-thoughts-on/tracing-commingled-assets-separating-beans/))

- **FIFO:** withdrawals come out of the earliest deposits first.
- **Pro rata:** each withdrawal draws from every contributor in proportion to their share of the balance. The SOP mandates this when victims' money cannot otherwise be told apart.
- **LIBR (lowest intermediate balance):** the account holder spends their own money first; the traceable victim amount can never exceed the lowest balance the account fell to after the deposit.

Default rule: LIBR for deciding the hold (it protects honest receivers), pro rata for splitting among several victims. The rule is a per-case setting and is printed on every report.

**Customer notice content** (required fields, from the Rajasthan HC list and the RBI draft): transaction UTR, date and time; amount held; whole account or amount only; lane and legal basis; ordering authority and reference number; reason in plain words; how to respond and by when; grievance officer name and contact. Languages for the demo: English, Hindi, Tamil, with templates in `templates/notices/`.

## 5. Functional requirements

Must = needed for submission and the live demo. Should = build once every Must works. Each ID maps to a test in section 12.

| ID | Priority | Requirement |
| --- | --- | --- |
| FR-01 | Must | Load AMLworld HI-Small and the demo case into Exasol with one command, using native `IMPORT` or pyexasol |
| FR-02 | Must | Accept a complaint ticket with the fields in section 13 (acknowledgement number, victim, UTR, amount, time, beneficiary, lane); validate required fields and flag a notice that lacks the amount or legal basis |
| FR-03 | Must | Trace the victim's money forward from the UTR through every hop inside the bank's own accounts, with cycle safety, a hop limit (default 8) and a time window (default 72 h) |
| FR-04 | Must | For hops leaving the bank, emit an outbound "next hop" record (bank, account, UTR, amount, time) as the simulated CFCFRMS update |
| FR-05 | Must | Record exits (ATM, cash, crypto, card, merchant) with their identifiers; they are never held |
| FR-06 | Must | Attribute the victim's traceable amount in each account under FIFO, pro rata and LIBR; the officer picks one per case; all three are shown side by side |
| FR-07 | Must | Split a mixed account among several victims pro rata, per SOP para 5(xiii) |
| FR-08 | Must | Score each account mule or honest from explainable signals (section 9); show each signal's value and contribution |
| FR-09 | Must | Recommend one action per account: amount hold, account hold, outbound referral, or no action; attach written reasons built from the signals and the attribution |
| FR-10 | Must | Officer approves, edits or rejects each recommendation; nothing is "held" until approved; every decision is logged with user, time and reason |
| FR-11 | Must | Generate the customer notice with all required fields (section 4) in English, Hindi and Tamil |
| FR-12 | Must | Tag each hold with its lane and run that lane's deadlines; show countdowns and flag holds due within 3 days |
| FR-13 | Must | Export an attribution report (PDF and CSV) per case: trail, rule used, per-account victim share, decisions, reasons, Exasol statement IDs |
| FR-14 | Must | Run the simulated baseline on the same case and show the three comparison numbers from section 3 |
| FR-15 | Must | Victim status view: amount held per account, amount lost to exits, next step |
| FR-16 | Should | Investigator questions in plain English through the Exasol MCP server, read-only |
| FR-17 | Should | Repeat-offender view: accounts receiving money from 3 or more complaints in 7 days |
| FR-18 | Should | Grievance workflow: holder uploads an explanation; bank forwards; decision recorded within the lane's clock |
| FR-19 | Should | Scale demo: trace and attribute over HI-Medium (about 32M rows) and show run time |
| FR-20 | Could | Lane B auto-flagging: in-profile check for transactions of ₹1,000 or more |

**Rules every requirement obeys**

- The tool never moves money and never places a hold without officer approval.
- Victim details are never shown on a receiver's notice (SOP para 9.1(ii)).
- Every number on screen can be traced to the SQL statement that produced it.

## 6. System architecture

Exasol Personal holds the data and runs all four case steps; a thin FastAPI service calls them and a React console shows the results for officer approval.

```
                 +------------------- Exasol Personal (local), schema PH -------------------+
Complaint ticket --> | 1 Trace      CONNECT BY NOCYCLE over TXN                              | <--> FastAPI service (pyexasol, runs steps)
(UTR, amount, lane)  |      v                                                                 |          |
                     | 2 Attribute  Python3 SET UDF: LIBR, FIFO, pro rata                     |          v
Synthetic data ----> |      v                                                                 |      Officer console (approve, edit, reject;
(AMLworld HI-Small,  | 3 Score      SQL signals, band MULE / REVIEW / HONEST                  |                       notices, deadlines)
 demo case, IMPORT)  |      v                                                                 |
                     | 4 Recommend  action and reasons per account and lane                   | <--> Exasol MCP server (read-only, row limit,
                     |  Tables: TXN, COMPLAINT, ATTRIBUTION, DECISION, AUDIT_LOG               |                         investigator questions)
                     +-----------------------------------+-------------------------------------+
                                                         |
                       +---------------------------------+---------------------------------+
                       v                                 v                                 v
              Customer notice                   Attribution report                Outbound referral
              (English, Hindi, Tamil)           (PDF, CSV, statement IDs)         (next hop to CFCFRMS, simulated)

Nothing is held until an officer approves it in the console.
```

| Component | Technology | Responsibility |
| --- | --- | --- |
| Database | Exasol Personal 2.2+, local, Python 3 SLC installed | Storage, trace, attribution UDF, scoring, recommendations, audit |
| Pipeline | Python 3.11, pyexasol, `make data` | Download and check AMLworld, map columns, generate profiles and the demo case, bulk `IMPORT` |
| API | FastAPI, pyexasol | Case endpoints: create ticket, run steps, approve, notices, deadlines, report; writes `AUDIT_LOG` |
| UI | React + TypeScript | Officer console, compare, report, victim and receiver views |
| MCP | `exasol-mcp-server`, read-only settings | Investigator questions in plain English |
| Baseline simulator | Python, same database | Hop-by-hop ticket replay with delays, for the compare screen |

**Request flow for one case:** ticket created → API calls `sql/040_trace.sql` → `PH.ATTRIBUTE_CASE` UDF for the three rules → signal views refresh → `sql/070_recommend.sql` → console shows recommendations → officer approves → `DECISION`, `DEADLINE` and `NOTICE` rows written → report export.

**Deployment:** everything runs on one laptop. Exasol runs under the `exasol` launcher (Podman); the API and UI run locally (`make run`). No cloud account is needed; an optional AWS deployment via `exasol install aws` is documented but not required.

## 7. Exasol requirements (verified against official Exasol sources)

Exasol must do the heavy work: tracing, attribution, scoring and audit run inside the database. The app tier only sends SQL, calls UDFs and renders results. That is what the 25% "effective use of Exasol Personal" criterion rewards.

**Setup (Exasol Personal 2.2 or later)** — [GitHub](https://github.com/exasol/exasol-personal), [Quick start](https://docs.exasol.com/db/latest/get_started/quick_start_guide.htm), [2.2 release notes](https://community.exasol.com/t/exasol-personal-2-2-is-here/303)

| Item | Verified fact | Requirement |
| --- | --- | --- |
| Launcher | CLI is `exasol`; install with `curl https://downloads.exasol.com/exasol-personal/installer.sh \| sh` on macOS or Linux; Windows binary from the download portal | `scripts/setup.sh` and `scripts/setup.ps1` wrap it |
| Database | `exasol install local`; then `exasol connect` | Run guide lists these exact commands |
| Platforms | macOS (Apple Silicon, 8 GB RAM min, macOS 15+ for the local DB), Linux amd64/arm64 with Podman, Windows amd64 (Podman offered by the launcher) | README states these prerequisites |
| Topology | Local is single-node; supports a single user | Do not depend on multiple DB users; use one schema-owning user plus app-level roles |
| Default credentials | Local user `sys`, password `exasol` (cloud: `secrets.json` in the deployment directory) | Read from `.env`; never commit credentials |
| Diagnostics | `exasol status`, `exasol info`, `exasol diag local` | `make doctor` calls `exasol diag local` |
| UDFs locally | Local deployments ship without a script language container; `exasol slc install python3` enables `PYTHON3` UDFs; `exasol slc list` shows installed | Setup installs the Python 3 SLC before creating scripts |
| Script bodies via CLI | `exasol connect` ends `CREATE SCRIPT` at a line containing only `/` | Every `.sql` file with a UDF ends its body with `/` on its own line |
| Licence | Database is proprietary, free for personal use under the Exasol Personal EULA; launcher is MIT | README says Exasol is used under its EULA and is not redistributed |

**SQL features to use** — [SELECT reference](https://docs.exasol.com/db/latest/sql/select.htm), [CONNECT\_BY\_ISCYCLE](https://docs.exasol.com/sql_references/functions/alphabeticallistfunctions/connect_by_iscycle.htm)

- `CONNECT BY NOCYCLE PRIOR ... START WITH ...` for the forward trace. `NOCYCLE` returns results even when money loops back.
- `LEVEL` for hop number; `CONNECT_BY_ISCYCLE` (only with `NOCYCLE`) to flag loops; `CONNECT_BY_ISLEAF` for terminal accounts; `CONNECT_BY_ROOT` for the originating UTR; `SYS_CONNECT_BY_PATH` for the printable path.
- Never put `LEVEL` alone in the `CONNECT BY` condition without `NOCYCLE`: cycle detection is disabled when `LEVEL` is used there, and an always-true condition hangs. Bound depth with `LEVEL <= :max_hops` together with `NOCYCLE`.
- `CONNECT BY` is evaluated before `WHERE`, so put time-window and amount filters in the `CONNECT BY` condition (or a pre-filtered CTE), not in `WHERE`.
- Window functions for balances and velocity (running balance, minutes-in-account, distinct counterparties).

**Python UDFs** — [Python 3 UDF docs](https://docs.exasol.com/db/latest/database_concepts/udf_scripts/python3.htm)

- Syntax: `CREATE OR REPLACE PYTHON3 SET SCRIPT ... EMITS (...) AS` with `def run(ctx):`. SET scripts read a group with `ctx.next()` and `ctx.reset()`; `ctx.emit()` writes rows; `cleanup()` is optional.
- The standard container lists pandas, NumPy and SciPy among its libraries. Check the installed set with a one-line UDF on first setup, because the local SLC version may differ.
- Prefer `DOUBLE` over `DECIMAL` inside UDFs for speed; store money in tables as `DECIMAL(18,2)` and round on emit.

**MCP server (FR-16)** — [GitHub](https://github.com/exasol/mcp-server), [settings](https://raw.githubusercontent.com/exasol/mcp-server/main/doc/user_guide/tool_setup.rst), [tool list](https://raw.githubusercontent.com/exasol/mcp-server/main/doc/user_guide/tool_list.rst)

- Package `exasol-mcp-server`, run with `uvx exasol-mcp-server@latest`; Python 3.11 or later; MIT.
- Connection env vars: `EXA_DSN`, `EXA_USER`, `EXA_PASSWORD`. Settings via `EXA_MCP_SETTINGS` (inline JSON or a file path).
- Read queries are off by default. Ship `config/mcp_settings.json` with `"enable_read_query": true`, `"enable_write_query": false`, `"default_row_limit": 500`, and schema filter `PH%`.
- Tools used in the demo: `execute_exasol_query`, `describe_exasol_tables_and_views`, `list_exasol_user_defined_functions`. Never set `disable_elicitation`.
- Add table and column `COMMENT`s on every object so the model can describe them correctly.

**Python driver** — [pyexasol](https://github.com/exasol/pyexasol): `pip install pyexasol[pandas]`; Python 3.10 or later; Exasol 7.1 or later; MIT. Use `export_to_pandas` for result sets and bulk `IMPORT` for loads.

**Optional for building** — [exasol-agent-skills](https://github.com/exasol-labs/exasol-agent-skills): Claude Code skills from Exasol Labs covering Personal setup, UDFs and SLCs, and `IMPORT`/`EXPORT`. Install with `claude plugin marketplace add exasol-labs/exasol-agent-skills` then `claude plugin install exasol@exasol-skills`.

**Unverified, check on first setup**

- The default local port and DSN format: read them from `exasol info`; do not hard-code 8563.
- The exact package set in the locally installed Python 3 SLC.

## 8. Data model and synthetic data

All tables live in schema `PH` (Proportionate Holds). Raw data lands in `PH_STAGE`; nothing in `PH` is edited by hand. Every table and column carries a `COMMENT` (needed by the MCP server).

**Data sources**

| Source | Use | Licence and handling |
| --- | --- | --- |
| [IBM AMLworld, HI-Small](https://www.kaggle.com/datasets/ealtman2019/ibm-transactions-for-anti-money-laundering-aml) | Background traffic: about 5M transactions, 515K accounts; Must | Community Data License Agreement ([NeurIPS 2023 paper](https://papers.neurips.cc/paper_files/paper/2023/file/5f38404edff6f3f642d6fa5892479c42-Supplemental-Datasets_and_Benchmarks.pdf)). Do not commit the CSVs; `scripts/download_data.sh` fetches them and checks a SHA-256. Confirm the exact CDLA variant on Kaggle and quote it in `THIRD_PARTY_NOTICES.md` |
| AMLworld HI-Medium | Scale demo: about 32M transactions; Should | Same |
| `data/demo_case.yaml` | The section 3 case plus 20 extra generated victims and mules; Must | MIT, ours |
| Generated KYC and profile | Account age, type, declared income band, branch; Must | Seeded generator `pipeline/gen_profiles.py` (fixed seed 42) |

AMLworld's laundering patterns (fan-out, fan-in, cycle, scatter-gather, gather-scatter, bipartite, stack, random) give realistic mule shapes for testing FR-03 and FR-08. Its CSV headers must be read from the file on download; map them in `pipeline/map_amlworld.py` and fail loudly if they change. Convert its amounts to INR at a fixed documented rate and note it in the README.

**Core tables**

| Table | Key columns | Purpose |
| --- | --- | --- |
| `BANK` | bank\_id, name, is\_own\_bank | Marks which bank runs the tool (Bank X) |
| `ACCOUNT` | account\_id, bank\_id, opened\_on, account\_type (savings, current, nodal, pool, escrow), holder\_type, profile\_band | Nodal, pool and escrow accounts are never fully held (SOP 9.1(i), 9.1(iii)) |
| `TXN` | txn\_id, utr, ts, from\_account, to\_account, amount\_inr, channel (IMPS, UPI, NEFT, RTGS, ATM, CARD, CRYPTO, CASH), is\_exit, exit\_identifier | Every money movement; `DISTRIBUTE BY from_account` |
| `BALANCE_SNAPSHOT` | account\_id, ts, balance\_inr | Derived running balance per account, for LIBR |
| `COMPLAINT` | ack\_no (14 digits), victim\_account, utr, amount\_inr, reported\_at, lane, source (1930, NCRP, branch), notice\_legal\_basis, notice\_has\_amount | One per ticket; FR-02 validation flags |
| `TRACE_EDGE` | case\_id, hop, from\_account, to\_account, txn\_id, amount\_inr, is\_cycle, is\_exit, path | Output of the trace |
| `ATTRIBUTION` | case\_id, account\_id, rule (FIFO, PRO\_RATA, LIBR), victim\_amount\_inr, other\_victims\_inr, own\_funds\_inr, balance\_now\_inr | Output of the attribution UDF |
| `ACCOUNT_SIGNALS` | account\_id, as\_of, signal\_name, value, weight, contribution | One row per signal, so reasons are explainable |
| `RECOMMENDATION` | case\_id, account\_id, action (AMOUNT\_HOLD, ACCOUNT\_HOLD, REFER\_OUT, NONE), amount\_inr, reasons\_json | Tool output, never a hold by itself |
| `DECISION` | case\_id, account\_id, action, amount\_inr, decided\_by, decided\_at, reason\_text, statement\_ids | Officer-approved holds; append-only |
| `DEADLINE` | hold\_id, lane, rule\_name, due\_at, status | From `config/lanes.yaml` |
| `NOTICE` | hold\_id, language, body, sent\_at | Generated text, stored for audit |
| `AUDIT_LOG` | event\_id, at, actor, event, case\_id, session\_id, stmt\_id | Links every screen number to the Exasol statement that produced it |

**Rules for the data layer**

- Load with `IMPORT` from local CSV (or pyexasol bulk import), never row-by-row `INSERT`.
- SQL files are numbered and idempotent: `sql/010_schema.sql` to `sql/090_views.sql`; `make db` runs them in order.
- Keep demo-case IDs stable (`ACC_A`, `ACC_B`, `ACC_C`, `ACC_D`, UTR `IMPS2026101810020001`) so tests and the video match.
- Record `CURRENT_SESSION` and `CURRENT_STATEMENT` into `AUDIT_LOG` for each pipeline step.

## 9. Algorithms

Four steps run per case, all inside Exasol: trace, attribute, score, recommend. Each writes its own table, so every step can be inspected and tested alone.

**Step 1 — Trace (SQL, `sql/040_trace.sql`)**

Find every candidate edge reachable from the complaint's UTR, forward in time.

```sql
SELECT :case_id, LEVEL AS hop, t.from_account, t.to_account, t.txn_id, t.amount_inr, t.ts,
       CONNECT_BY_ISCYCLE AS is_cycle, CONNECT_BY_ISLEAF AS is_leaf,
       SYS_CONNECT_BY_PATH(t.to_account, '>') AS path
FROM PH.TXN t
START WITH t.utr = :utr
CONNECT BY NOCYCLE PRIOR t.to_account = t.from_account
       AND t.ts >= PRIOR t.ts
       AND t.ts <= PRIOR t.ts + INTERVAL '72' HOUR
       AND LEVEL <= :max_hops;
```

- Filters belong in `CONNECT BY` because Exasol evaluates it before `WHERE`.
- Edges into accounts at other banks are kept but not expanded further; they become outbound referrals (FR-04).
- Exit transactions (ATM, cash, crypto) end their branch.
- The trace finds candidate paths only. It over-reaches on purpose; step 2 decides how much victim money actually moved along each edge.

**Step 2 — Attribute (Python SET UDF, `sql/050_attribute_udf.sql`)**

`PH.ATTRIBUTE_CASE(case_id, rule, ts, from_account, to_account, amount_inr, victim_tag)` grouped by case and rule, ordered by `ts`, emits `(case_id, rule, account_id, victim_tag, tainted_inr, own_inr)`.

It replays every transaction of the traced accounts in the window, in time order. Each account keeps an "own funds" pool (its balance before the first tainted credit) and tainted lots tagged by victim. On each outflow:

| Rule | Which money leaves first |
| --- | --- |
| LIBR (default) | Own funds first, then tainted lots. Tainted balance never exceeds the lowest balance after the tainted credit |
| FIFO | Earliest lots first, own funds counted as the earliest lot |
| Pro rata | Every lot in proportion to its share of the current balance |

The tainted part of an outflow becomes a tainted lot in the receiving account, tagged with the same victim. Several victims in one account split by pro rata per SOP para 5(xiii). The UDF must conserve money: for each victim, held + exited + in other banks + still in own-bank accounts = amount reported, to the rupee.

**Expected LIBR result for the demo case** (transaction order A→B at 10:06:00, A→C at 10:06:30)

| Account | Victim's money (₹) | Why |
| --- | --- | --- |
| A | 500 | A's own ₹500 went out first, inside the ₹1,20,000 to B; ₹500 of the victim's money stays |
| B | 69,000 | Received ₹1,19,500 victim + ₹500 of A's float; the float left first in the ₹1,000 to D |
| C | 80,000 | All victim money; with the tool it is held before the 12:30 cash-out |
| D | 500 | Only ₹500 of the ₹1,000 is traceable to the victim; the other ₹500 is A's float. D's own ₹8,19,000 is untouched |
| ATM exit | 50,000 | Gone; recorded with ATM ID |
| Total | 2,00,000 | Conserved |

Victim money held with the tool = A + B + C = ₹1,49,500, matching section 3. D's case shows the point of attribution: the police notice names ₹1,000; the tool holds ₹1,000 as instructed (Lane A) and drafts a request to narrow it, citing that ₹500 is traceable to the victim. FIFO and pro rata results are computed and shown beside it.

**Step 3 — Score mule or honest (SQL views + `ACCOUNT_SIGNALS`)**

Each signal is computed in SQL over the 90 days before the complaint, stored as one row, and weighted from `config/scoring.yaml`. Weights are hand-set and documented; any ML model may only add one more signal, never replace the list.

| Signal | Mule-like value | Honest-like value |
| --- | --- | --- |
| Account age | Under 90 days | Over 1 year |
| Median dwell time of credits | Under 30 minutes | Days or weeks |
| Pass-through ratio (outflow within 60 min / inflow) | Over 0.8 | Under 0.2 |
| Distinct complaint-linked credits in 7 days | 2 or more | 0 or 1 |
| Counterparty breadth (distinct payers per day) | A few large payers | Many small payers (merchant pattern) |
| Balance retained after the tainted credit | Near zero | Stays well above the credit |
| Cash or crypto exits | Present | Absent |
| Credit vs declared profile band | Far above | Within |
| Account type | — | Nodal, pool or escrow (never fully held) |

Output: a score from 0 to 1, a band (MULE ≥ 0.7, REVIEW 0.4–0.7, HONEST < 0.4), and the top three signals as plain sentences.

**Step 4 — Recommend (SQL, `sql/070_recommend.sql`)**

| Condition | Recommended action | Notes |
| --- | --- | --- |
| Exit transaction | NONE, record exit | Identifiers sent outbound |
| Account at another bank | REFER\_OUT | Simulated CFCFRMS update within minutes |
| Nodal, pool or escrow account | AMOUNT\_HOLD | Never ACCOUNT\_HOLD (SOP 9.1(iii)) |
| Lane A, band HONEST or REVIEW | AMOUNT\_HOLD of the notice amount, or the attributed amount if no amount was given | If attributed < notice amount, draft a narrowing request |
| Lane A, band MULE | AMOUNT\_HOLD now + draft a request to police for S.106 seizure | Account-level action needs a lawful direction or FIR in Lane A |
| Lane B, band MULE | ACCOUNT\_HOLD, flagged "last resort" with reasons | RBI draft allows it for suspected mules |
| Lane B, otherwise | AMOUNT\_HOLD of the flagged transaction |  |
| Zero balance, first-layer account | Hold marker for future credits | SOP 9.1(i); first layer only |

Every recommendation carries `reasons_json`: the rule used, the attributed amount, the band, the top signals, and the legal basis. A REVIEW band always needs a written officer comment before approval.

## 10. Screens and the 3-minute demo

One web app for the officer, plus two read-only views that stand in for what the victim and the receiver would get. Build for a 1440 px laptop screen first; it must stay usable at 390 px.

**Screens**

| Screen | Who | Shows | Actions |
| --- | --- | --- | --- |
| Case inbox | Officer | Open tickets, lane, age, deadline badge, notice validity flag | Open case; import ticket |
| Case trail | Officer | Graph of the money from the victim outwards: own-bank accounts expanded, other banks as referral nodes, exits as end nodes; edge labels with amount and time | Click an account for detail |
| Account panel | Officer | Victim's share under LIBR, FIFO and pro rata side by side; band and score; top three signals as sentences; recommendation with reasons | Approve, edit amount, reject (reason required) |
| Decisions and deadlines | Officer | Every approved hold, its lane, countdowns, items due within 3 days | Mark grievance received; extend (with police reference) |
| Compare | Officer, judges | The section 3 table computed live: baseline vs tool on the same case | Re-run baseline with a different delay |
| Report | Officer | Printable attribution report with statement IDs | Export PDF and CSV |
| Ask | Investigator | Chat box over the Exasol MCP server, read-only | Ask in plain English; shows the SQL it ran |
| Victim status | Victim (simulated) | Held per account, lost to exits, next step, in plain words | None |
| Receiver notice | Receiver (simulated) | The generated notice in English, Hindi or Tamil | Upload an explanation (FR-18) |

**Design rules**

- Plain words over jargon: "Hold ₹1,000 only" not "Lien – partial".
- Every number has a source link that opens the SQL and its statement ID.
- Colour is never the only signal: bands carry a word (MULE, REVIEW, HONEST).
- No auto-approve button anywhere.

**Demo script (target 2:45, recorded and also run live on demo day)**

| Time | Show | Say |
| --- | --- | --- |
| 0:00–0:20 | Murugan's notice from today's system (a failed supplier payment) | "A fuel-station owner sold ₹1,000 of diesel. His whole ₹8 lakh account was frozen." |
| 0:20–0:35 | Case inbox; open Lakshmi's ticket | "A retired teacher lost ₹2 lakh. The complaint reaches the bank 68 minutes late." |
| 0:35–1:05 | Case trail renders; C lights up inside the bank; B referred out; ATM exit | "Exasol follows every hop inside the bank in seconds, with loop protection." |
| 1:05–1:40 | Account panel for D, then for C | "Lowest-balance tracing says only ₹500 here is hers. D is a three-year-old merchant with 300 payments a day, so hold the amount, not the account. C is two weeks old and passed money on in minutes: hold it now." |
| 1:40–1:55 | Approve; notice appears in Tamil | "An officer approves every hold. Murugan is told the same day what is held and why." |
| 1:55–2:15 | Compare screen | "35% recovered becomes 75%, and ₹8.19 lakh of honest money is never locked." |
| 2:15–2:35 | Ask: "which accounts got money from 3 or more complaints this week?" | "Investigators ask Exasol in plain English through its MCP server." |
| 2:35–2:45 | Report with statement IDs; deadlines | "Every number is traceable to the query that made it, ready for the police or the court." |

Live-demo safety: a `make demo-reset` target restores the seeded state in under 60 seconds; the video is the fallback if the network fails.

## 11. Repository, licence and submission checklist

The repo must let a judge go from clone to a running demo with the commands in the README, on a laptop with no cloud account.

**Repository layout**

```
proportionate-holds/
  README.md                 overview, problem statement, setup, usage, screenshots
  LICENSE                   MIT
  THIRD_PARTY_NOTICES.md    AMLworld (CDLA), Exasol Personal (EULA, not included), libraries
  CONTRIBUTING.md
  Makefile                  setup, db, data, run, test, demo-reset, doctor
  .env.example              EXA_DSN, EXA_USER, EXA_PASSWORD, OWN_BANK_ID
  config/                   lanes.yaml, scoring.yaml, mcp_settings.json
  sql/                      010_schema.sql … 090_views.sql, numbered and idempotent
  pipeline/                 download, map_amlworld, gen_profiles, load, baseline
  app/api/                  FastAPI service (Python 3.11+, pyexasol)
  app/web/                  React + TypeScript UI
  templates/notices/        en, hi, ta
  data/demo_case.yaml       the section 3 case
  tests/                    unit, oracle, end-to-end
  docs/                     PROBLEM_STATEMENT.md, RUN_GUIDE.md, ARCHITECTURE.md, deck.pdf, screenshots/
  .github/workflows/ci.yml  lint + unit tests + oracle tests (no live DB needed)
```

**README must contain**

1. One-paragraph overview and the problem statement in the official template.
2. Architecture picture and a one-line description of what runs in Exasol.
3. Prerequisites per OS (from section 7) and the exact setup commands.
4. "Data is synthetic": AMLworld plus a generated case; no employer or real customer data.
5. Usage: open the demo case, approve holds, export the report, use Ask.
6. Demo video link (3:00 or less) and deck link.
7. Limits (section 12) and the legal-status note: Lane B follows a draft that is not in force.
8. Licence and third-party notices.

**Submission checklist**

- [ ] Repo public, MIT `LICENSE` at root
- [ ] Fresh clone runs end to end on a second machine using only the README
- [ ] `docs/PROBLEM_STATEMENT.md` follows the template and names no technology
- [ ] Pitch deck PDF in `docs/` and linked
- [ ] Demo video 3:00 or less, linked in README
- [ ] `docs/RUN_GUIDE.md` covers local setup, demo reset and troubleshooting
- [ ] No credentials, no AMLworld CSVs, no Exasol binaries committed
- [ ] CI green on `main`
- [ ] Form submitted at the [official submission page](https://www.exasol.com/events/exathon-india-2026/submit-your-project/)

**For demo day (top 5)**: live demo is mandatory, 15 minutes plus 5 minutes of jury questions over video. Prepare a 15-minute version of the section 10 script that adds the two-lane explanation and the limits.

## 12. Quality bar, tests and build plan

The build is done when every acceptance test below passes on a fresh clone and the demo runs live in under 3 minutes.

**Non-functional requirements**

| ID | Requirement |
| --- | --- |
| NFR-01 | Trace + attribute + score + recommend for the demo case completes in under 5 seconds on a laptop with HI-Small loaded |
| NFR-02 | Setup from a clean machine (excluding downloads) in under 20 minutes with the README alone |
| NFR-03 | Money is `DECIMAL(18,2)` in tables; attribution conserves to the rupee |
| NFR-04 | Deterministic: same input and seed give the same output |
| NFR-05 | Credentials only in `.env`; MCP is read-only with a row limit |
| NFR-06 | Every officer action and pipeline step is in `AUDIT_LOG` with the Exasol statement ID |
| NFR-07 | Accessible UI: keyboard reachable, text contrast 4.5:1, bands labelled in words |
| NFR-08 | Runs with one Exasol user (Personal supports a single user) |

**Acceptance tests**

| Test | Checks | Pass condition |
| --- | --- | --- |
| AT-01 | FR-01 | `make data` loads HI-Small and the demo case; row counts printed and match the file |
| AT-02 | FR-02 | A ticket with no amount is flagged "incomplete notice"; a valid one is accepted |
| AT-03 | FR-03 | Demo trail finds A, B, C, D and the ATM exit; a seeded A→E→A loop is returned with `is_cycle = 1` and does not hang |
| AT-04 | FR-06 | LIBR results equal the section 9 table exactly; FIFO and pro rata equal the oracle |
| AT-05 | FR-06 | Conservation holds for all 21 generated victims under all three rules |
| AT-06 | FR-07 | A seeded account with two victims splits pro rata, matching the oracle |
| AT-07 | FR-08 | D is HONEST; A, B, C are MULE; each shows three reason sentences |
| AT-08 | FR-09 | D gets AMOUNT\_HOLD ₹1,000 with a drafted narrowing request; C gets AMOUNT\_HOLD ₹80,000 plus a seizure request (Lane A); B gets REFER\_OUT; ATM gets NONE |
| AT-09 | FR-10 | No row in `DECISION` exists without an approving user; reject without a reason is refused |
| AT-10 | FR-11 | Notice for D has every required field in en, hi and ta and never shows Lakshmi's details |
| AT-11 | FR-12 | A Lane A hold with a grievance shows 7, 15 and 90-day dates; a Lane B hold shows 20, 30 and 60-day dates |
| AT-12 | FR-14 | Compare shows ₹69,500 vs ₹1,49,500 held and ₹8,19,000 vs ₹0 of D's own money locked |
| AT-13 | FR-13 | Report PDF lists rule, per-account amounts, reasons and statement IDs that exist in Exasol's audit tables |
| AT-14 | FR-16 | MCP read query works; a write query is refused |
| AT-15 | Hackathon | Checklist in section 11 fully ticked |

The **oracle** is a separate pure-Python implementation in `tests/oracle.py` that replays the same transactions without Exasol. Tests compare the UDF output to it; property tests (Hypothesis) generate random small graphs and check conservation.

**Limits to state openly**

- The tool cannot make victims report faster or skip police checks, FIRs or court orders.
- A real bank sees only its own accounts; other banks' hops arrive via CFCFRMS. The demo uses multi-bank synthetic data and simulates that boundary with `is_own_bank`.
- Cash already withdrawn is gone; the tool records where it left.
- Lane B rules come from a draft that is not yet in force.
- Scoring weights are hand-set for the demo, not validated on real data.

## 13. Answers to exasol\_questions.docx

Most of the questions doc holds up, but three claims conflict with the official MHA SOP and two figures have no source. Use the verified answers below in the build, the deck and Q&A.

**Corrections**

| The questions doc says | Verified position | Source |
| --- | --- | --- |
| Freezes are issued under Section 105 BNSS | Holds are under S.168 read with S.94 BNSS; account seizure is under S.106 BNSS and needs an FIR | SOP para 5(i), 5(iv) |
| After cash-out the IO "files an FIR under Section 106 BNSS" | An FIR is registered under S.173 BNSS; S.106 is the power to seize property | SOP para 6.1(l), Annexure III |
| The customer registers a dispute on the grievance module within 7 days | The customer goes to the bank branch; the bank checks and submits the grievance to the module within 7 days | SOP para 10.1(a) |
| Golden-hour success: 90–100% in 1 hour, \~60% by 6 hours, under 20% after | No official source found. Do not show these numbers. Use: police say the first hour matters most; national recovery is about 14% | [The Week](https://www.theweek.in/theweek/current/2026/08/14/how-gaps-in-banking-and-policing-are-fuelling-cyber-fraud.html), [The420](https://the420.in/mumbai-1930-cyber-helpline-saves-202-crore-2025) |
| NOC to unfreeze in 3–7 working days | No source. The SOP sets 15 days for the officer's decision; the bank then lifts the hold and updates the module | SOP para 10.1(d) |

**Answers**

| Question | Verified answer | What the build does |
| --- | --- | --- |
| What is in a ticket? | Matches the doc: victim details, debited account, transaction date, time, amount, UTR, beneficiary account, suspect identifiers, description, evidence. NCRP acknowledgement is 14 digits starting with 2; via 1930 it starts with 3 (SMS header XXNCRP) | `COMPLAINT` table and FR-02 validation |
| What is the golden hour? | The first hour or so after the transfer, while money sits in the first one or two accounts. Speed is the biggest factor | Not a tool feature; the tool shortens the bank's own reaction time |
| What happens when money is withdrawn? | The bank records the exit and its identifiers (ATM ID, device, wallet). It is not expected to return money that moved on; holds placed later from other complaints follow para 11 | Exits are end nodes, recorded, never held |
| Is the trail different from the withdrawal? | Yes. The trail is account-to-account; the exit is where money leaves banking (ATM, cheque, AePS, POS, crypto, gift cards) | `channel` and `is_exit` in `TXN` |
| How is a ticket judged valid, and an account added to the chain? | Police push only prima facie cyber-fraud cases to CFCFRMS; the bank holds the reported amount if available and reports onward transfers. Rajasthan HC: unclear notices should be clarified, not blocked mechanically | FR-02 flags notices missing amount or legal basis; the trace adds accounts only on real money movement |
| Is benefit of doubt given? | In practice no: holds come first and the account holder must explain (LiveLaw). On paper the SOP tells the officer to consider that an account may be used without the holder's knowledge and to hear them before S.106(3) action | Honest-receiver scoring and amount-only holds put that principle into practice |
| How many honest receivers can be unblocked? | No public count exists; blocked accounts are not reported (MediaNama). Proxy: 105 petitions settled by one High Court in one judgment | The tool reports, per case, accounts kept usable and rupees of own money not locked (demo: 1 account, ₹8,19,000) |
| What is communicated to the account holder? | The SMS in the doc is illustrative, not an official format. Lane A has no prescribed notice to the holder; the bank may share the ordering police unit's contact. Lane B (draft) requires immediate notice with reasons and an officer contact | FR-11 notice with every field from section 4 |
| How is a "fraud" decision communicated? | If the officer rejects the explanation, reasons are recorded in the grievance module and sent to the holder by SMS or email | Decision and reason stored; notice regenerated |
| How much money is stuck in blanket freezes? | No official total. Known cases: ₹8.26 lakh (Sri Sai Wines), ₹13.5 lakh (a Kochi architecture firm), about ₹80 lakh (Malabar Gold). Total held under CFCFRMS since 2021: ₹7,647 crore, all types | Compare screen shows the per-case figure |
| How can they approach the court? | Holder: grievance ladder (officer, district, state), then the jurisdictional court, often a High Court writ. Victim: Money Restoration Module, or an application to the magistrate under S.497, 498 or 503 BNSS; Lok Adalat where feasible | Report export is built as the evidence bundle for either route |

## Sources

**Hackathon**

- [ExaThon India 2026 — official page (rules, deliverables, judging)](https://www.exasol.com/events/exathon-india-2026/)
- [ExaThon submission form](https://www.exasol.com/events/exathon-india-2026/submit-your-project/)

**Exasol**

- [Exasol Personal on GitHub](https://github.com/exasol/exasol-personal)
- [Quick start with Exasol Personal](https://docs.exasol.com/db/latest/get_started/quick_start_guide.htm)
- [Exasol Personal 2.2 release notes (local UDFs, slc install)](https://community.exasol.com/t/exasol-personal-2-2-is-here/303)
- [SELECT statement and CONNECT BY](https://docs.exasol.com/db/latest/sql/select.htm)
- [CONNECT\_BY\_ISCYCLE](https://docs.exasol.com/sql_references/functions/alphabeticallistfunctions/connect_by_iscycle.htm)
- [Python 3 UDFs](https://docs.exasol.com/db/latest/database_concepts/udf_scripts/python3.htm)
- [Exasol MCP server](https://github.com/exasol/mcp-server), [settings](https://raw.githubusercontent.com/exasol/mcp-server/main/doc/user_guide/tool_setup.rst), [tool list](https://raw.githubusercontent.com/exasol/mcp-server/main/doc/user_guide/tool_list.rst)
- [pyexasol](https://github.com/exasol/pyexasol), [getting started](https://exasol.github.io/pyexasol/master/user_guide/getting_started.html)
- [Exasol agent skills for Claude Code](https://github.com/exasol-labs/exasol-agent-skills)
- [VoltProof, a public ExaThon 2026 entry (reference for repo shape)](https://github.com/stredevirad/voltproof-exathon-2026)

**Data**

- [IBM AMLworld on Kaggle](https://www.kaggle.com/datasets/ealtman2019/ibm-transactions-for-anti-money-laundering-aml)
- [Realistic Synthetic Financial Transactions for AML Models, NeurIPS 2023](https://papers.neurips.cc/paper_files/paper/2023/file/5f38404edff6f3f642d6fa5892479c42-Supplemental-Datasets_and_Benchmarks.pdf)

**Law and policy**

- [MHA I4C SOP for NCRP-CFCFRMS, 2 Jan 2026](https://the420.in/wp-content/uploads/2026/01/sop.pdf)
- [RBI draft on money mule holds — MediaNama](https://www.medianama.com/2026/09/223-rbi-cyber-fraud-account-freeze-rules/)
- [RBI draft timelines — Vilva Business](https://www.vilvabusiness.com/blog/mule-accounts-rbi-debit-hold-bank-account-verification-payouts)
- [Supreme Court order, 4 Aug 2026 — Verdictum](https://www.verdictum.in/supreme-court/digital-arrest-scams-interim-directions-rbi-1619172)
- [Rajasthan HC, 105 petitions — MediaNama](https://www.medianama.com/2026/08/223-freeze-amount-rajasthan-hcs-cybercrime/)
- [Andhra Pradesh HC, Sri Sai Wines — MediaNama](https://www.medianama.com/2026/07/223-merchants-bank-accounts-frozen-upi-payments-fraudsters-andhra-hc/)
- [Delhi HC, Neelkanth Pharma — SCC Online](https://www.scconline.com/blog/post/2025/03/01/delhi-high-court-freezing-bank-accounts-cyber-crime-policy-reform-legal-news/)
- [Problem with CFCFRMS — LiveLaw](https://www.livelaw.in/articles/cfcfrms-reading-mha-new-account-freeze-sop-537503)
- [LEA hold requests — Razorpay](https://razorpay.com/blog/how-payment-gateways-handle-law-enforcement-agency-lea-hold-requests-in-india/)
- [Tamil Nadu petrol pumps — MediaNama](https://www.medianama.com/2026/08/223-tamil-nadu-petrol-pumps-upi-boycott/)
- [Gaps in banking and policing — The Week](https://www.theweek.in/theweek/current/2026/08/14/how-gaps-in-banking-and-policing-are-fuelling-cyber-fraud.html)
- [2025 cyber-fraud losses — ThePrint](https://theprint.in/india/cybercrime-saw-24-spike-in-2025-indians-lost-rs-22495-crore-mainly-in-investment-scams/2859930/)
- [Tracing commingled assets — Schneider Downs](https://schneiderdowns.com/our-thoughts-on/tracing-commingled-assets-separating-beans/)