# Proportionate Holds

A bank-side agent that works every cyber-fraud ticket the moment it lands, at any hour: it checks the ticket against
the bank's transactions, follows the victim's money through the bank, works out how much of each account is really
the victim's, has the holds the rules require placed (amount only), files the onward entries, exits and KYC packets,
and keeps watching the accounts it touched. The bank nodal officer handles five kinds of exception only, each written
up with its reasons and the case against.

Built for **ExaThon India 2026** (Track 01: Money, Risk & Regulation). Every number is computed in
**Exasol Personal**: the trace (`CONNECT BY NOCYCLE`), the victim's share (a Python UDF), the account evidence, the
rules engine, the checker's checks, the hold ledger and the "today's desk" replay, and every number on screen
traces to an Exasol statement ID. The agent only orders the tools and proposes.

## Problem statement

A bank's fraud desk can't act on a cyber-fraud ticket within the golden hour, because finding the payment, holding
it and reporting every onward hop is done by hand, one hop at a time, and stops when the desk closes; with no rule
for how much of a mixed balance is the victim's, the desk freezes whole accounts to be safe. If this worked, more of
each victim's money would be held at the first accounts it reached, and honest receivers further down the trail
would not be frozen.

How we got there: [docs/PROBLEM_DISCOVERY.md](docs/PROBLEM_DISCOVERY.md) (the journey, 36 pain points, why Phase 1).
What we built: [docs/DESIGN.md](docs/DESIGN.md).

## The 2:13 a.m. ticket

Lakshmi pays a fake officer ₹2,00,000 at 01:52; the ticket reaches Bank X at 02:13. Today it waits for the desk to
open at 10:00, by which time two accounts have cashed out and the desk freezes a fuel station's whole account over a
₹1,000 diesel sale. With the agent:

| On this ticket, at Bank X | Today's desk | With the agent |
| --- | --- | --- |
| Victim's money held | ₹500 | ₹80,000 |
| Honest money locked | ₹8,19,500 (Murugan's whole account) | ₹500 overnight, ₹0 after the officer narrows it |
| Ticket to first action | 8 h 7 min | 3 min |
| Bank Y gets its onward ticket | 10:35 | 02:17, and its own agent holds ₹40,000 on B |

The night goes on. At 03:20 a ticket arrives with a UTR one digit off: nothing is held, it goes back to the police at
once, and the near match waits for the officer. At 04:30 account C tries an ATM; the hold refuses it and the watch
proposes a full freeze. At 04:31 C tries to send the money to E at the same bank; refused, and E goes on the watch
with no hold. At 05:45 a second victim's money lands on A and is held as "not linked to a reported victim yet"; at
06:00 that victim's ticket arrives and the held money is linked to them, the total unchanged. At 07:30 a customer
pays Murugan for diesel: not held, and the log says why. Bank Z, which has no agent, opens its desk at 10:00 and
finds G already emptied at 03:40.

At 9 a.m. Bank X's officer sees two lists: everything done automatically overnight, and three decisions: narrow
Murugan's hold to the ₹500 traceable to the victim, a full freeze on C (proposed, never placed), and the unmatched
ticket.

**The agent proposes, the checker acts, the officer decides four things.** The maker (the agent, optionally led by
a local model) has no tool that changes data: it writes proposals. A deterministic checker, with its own database
user, validates each one against the rules engine and the checks (conservation to the paisa, holds within balance,
trail and report) and only then holds, files or routes it. The officer decides only: narrow a hold, widen it to a
full freeze, accept or leave an unmatched notice, release after 75 days on the watch. Each bank has its own schema
and users and cannot read another bank's data; the simulated portal is the only link between them
([DESIGN.md](docs/DESIGN.md)).

## Data is synthetic

No real bank, customer or employer data.

- **Background traffic:** IBM AMLworld HI-Small, about 5 million simulated transactions between simulated banks
  (public, on Kaggle). It is converted to rupees at the dataset's own exchange rates and ₹95 per US dollar (about the October 2026 rate), its
  payment formats are mapped to Indian channels (ACH → NEFT, wire → RTGS, ...), and it is moved to the weeks
  before the case day, so the agent works every ticket against a full-size transaction table
  ([DECISIONS.md, D-37](docs/DECISIONS.md)).
- **The tickets:** the 02:13 a.m. demo ticket and 21 more are invented (fixed seed 42); their account histories and
  KYC details are generated, with obviously fake identifiers.

See [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md).

## Quick start (no database needed)

```sh
python3 -m venv .venv && . .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -r requirements-dev.txt
python -m pytest -q                                   # oracle, rules, agent and API tests
python -m tests.show_case                             # the demo case under LIBR, FIFO and pro rata
```

## Full setup (Exasol Personal)

Prerequisites: macOS 15+ on Apple Silicon with 8 GB RAM, Linux amd64/arm64 with Podman, or Windows amd64 (the
launcher offers Podman). Python 3.11+, Node 20.19+ or 22.12+ for the console. No cloud account, nothing to pay for.

```sh
make setup            # exasol launcher, `exasol install local`, `exasol slc install python3` (macOS/Linux)
cp .env.example .env  # fill EXA_DSN (and fingerprint, if shown) from `exasol info`; pick a PH_ROLE_PASSWORD
make db               # schemas PH_X, PH_Y, PH_Z (one per bank), PH_PORTAL, and the maker/checker/portal users
make seed             # each bank gets only its own accounts and transactions; the tickets wait on the portal
make doctor           # every check should say PASS
make night            # the demo night across the three banks, to 09:00
make check-db         # every database test (it resets the data: run `make night` again afterwards)
```

Without `make` (Windows PowerShell), the same steps are:

```sh
python -m pipeline.run_sql                  # schemas, users and grants
python -m pipeline.load_cases --verify      # the synthetic world, cut into each bank's share
python -m pipeline.doctor
python -m pipeline.night                    # reset, then run the night to 09:00
python -m pytest -q -m db
```

**AMLworld background (optional, about 10 minutes once).** From
[Kaggle](https://www.kaggle.com/datasets/ealtman2019/ibm-transactions-for-anti-money-laundering-aml) (free
account), download `HI-Small_Trans.csv`, `HI-Small_accounts.csv` and `HI-Small_Patterns.txt` into `data/raw/`.
Don't open and re-save them in Excel: it drops the leading zeros of the bank codes. Then:

```sh
python -m pipeline.load_amlworld --check    # files present and shaped as expected (no database)
make data                                   # imports into PH_AML once, then gives each bank its share
make lab                                    # trace and scoring tests on the labelled patterns (PH_LAB)
make learn                                  # propose new scoring weights trained in Exasol, and run the gate
make bench                                  # scale benchmark: 100 tickets at once (TICKETS=1000 for more)
```

**Scale benchmark (optional).** `python -m pipeline.bench --tickets 1000` fires a burst of tickets at the full
AMLworld table and works each through every step, up to the holds, in its own schemas (`PH_BENCH_*`, never the demo
banks). It writes `docs/BENCHMARK.md`: time per step and how many tickets were held within the golden hour, with the
machine it ran on.

**Scoring weights (optional).** Every account gets two scores, pass-through and fraud exposure, which give one of
four account types or the grey zone ([D-57](docs/DECISIONS.md)). Each signal starts at a hand-set weight.
`make learn` trains a logistic regression inside Exasol on AMLworld's labels and proposes a new set of weights, each
kept between 0.05 and 0.50, then runs the gate: bounds, the five demo accounts keep their types, and no more than
0.02 AUC lost. A proposal goes live only when a named person approves it:
`python -m pipeline.weights approve --version N --by "NAME"` ([D-58](docs/DECISIONS.md)). The report is
`docs/SCORING_WEIGHTS.md`; `python -m pipeline.weights show` lists every version. AMLworld is synthetic, so the test
says how well the weights rank AMLworld accounts, not real mule accounts.

Each AMLworld bank becomes a branch of Bank X, Y or Z, and each bank's schema gets only the AMLworld rows touching
its own branches. The import into `PH_AML` runs once; `make seed` and the console's Reset keep it.

Exasol Personal is used under its EULA and is not redistributed here; the `exasol` launcher is MIT.

## Run it

```sh
python -m app.api                                     # API on http://127.0.0.1:8000 (docs at /docs)
cd app/web && npm install && npm run dev              # console on http://localhost:5173
```

The console is Bank X's nodal desk. **This morning** has the two lists: done automatically overnight (the morning
log, every ticket worked, what the watch saw) and waiting for you (the officer's decisions only, each with its reasons,
the case against, its fixed legal citation and the agent's draft for you to sign). The demo controls at the bottom
replay the night to 09:00, continue it to 10:30 so Bank Z's desk opens, or reset. Every account opens a **hold
record**: the notice, the path, the lowest-balance steps, the rule, who decided, every change and the Exasol
statement behind each number, also as a text file to attach. **The whole night** is the narrator's view across all
three banks, captioned "Narrator's view: no single bank sees this."; no bank's user can produce it.

From a terminal instead:

```sh
python -m pipeline.night                              # reset, run the night to 09:00, print what happened where
python -m pipeline.night --until "2026-10-18 10:30:00" --no-reset     # Bank Z's desk opens at 10
python -m pipeline.officer waiting                    # what waits for Bank X's officer (approve/reject <id>)
python -m pipeline.record ACC_A                       # the hold record for one account
python -m pipeline.today CASE_DEMO                    # today's desk vs the agent, as JSON
```

**Optional local model** (free, offline): install [Ollama](https://ollama.com), `ollama pull qwen2.5:7b`, set
`PH_LLM=ollama` in `.env`, then `python -m pipeline.night --mode llm`. The model chooses the order of the agent's
tools and words the narrow and freeze reasons and the morning log; every sentence must cite the reasons it uses and
repeat no number it was not given, or the fixed template is used. The holds come out the same with the model off.

## Repository

```
config/      rules.yaml (automatic actions, officer decisions, legal bases, proactive holds), scoring.yaml,
             scoring_weights.yaml (weight versions), banks.yaml
data/        demo_case.yaml (the night), extra_cases.yaml (generated, seed 42), loop_case.yaml,
             proactive_case.yaml (proactive holds, scenarios A to G)
sql/bank/    one bank's schema and views (run once per bank); sql/portal/ the simulated CFCFRMS;
             sql/steps/ the agent's analysis tools and the checker's actions, run inside Exasol;
             sql/amlworld/, sql/lab/ the AMLworld import and tests
pipeline/    agent.py (the maker), checker.py, night.py (the simulation), portal.py, core.py (simulated core
             banking), watch.py, scoring.py (account types), proactive.py (proactive holds), weights.py,
             recalc.py, reasons.py, record.py, morning.py, narrator.py, users.py, loaders
app/api/     FastAPI service the console talks to
app/web/     officer console (React + Vite)
tests/       oracles (attribution, scoring, rules) and their tests; -m db tests run against Exasol
docs/        PROBLEM_STATEMENT.md, PROBLEM_DISCOVERY.md, DESIGN.md, DECISIONS.md, REQUIREMENTS.md (first spec)
```

## Assumptions

Stated as assumptions, not as how banks work. Each one is a question for anyone who has worked a bank fraud desk.

- CFCFRMS, core banking and the other banks are simulated, and the demo says so. The portal turns an onward entry
  into the next bank's ticket within a minute.
- Murugan's account is at the same bank as account A.
- A hop to another account in the same bank is handled in the same pass, not as a fresh ticket.
- Declined ATM and UPI attempts reach Exasol within minutes. In a real bank they come from the card or UPI switch or
  the fraud-monitoring system.
- A hold larger than the balance catches later credits until it is filled (bank practice varies). Here the watch
  holds new money on a mule-like account up to the amount the notice reported.
- **Proactive holds** ([D-60 to D-65](docs/DECISIONS.md)). On an account a complaint names directly, or one in the
  Suspect Registry, typed Pipe for fraud or Fast mover, a first-time payment of Rs 1,000 or more is held the minute
  it lands, before anyone complains, and someone who can reach the payer is told (our fraud team for our own
  customer, a CFCFRMS pre-alert otherwise). Two modes: `rbi_draft` (default) follows the RBI draft KYC Amendment
  Directions of 11 Sep 2026, with the holder told and 20/30/60-day clocks; `current_sop` holds only in the room
  under an existing notice. Switch with `python -m pipeline.night --hold-mode current_sop` or the demo bar. The draft
  directions are not final law. The Suspect Registry, CFCFRMS, holder notices and the payers' answers are simulated:
  every message is a row in a table, nothing is sent. The demo cases are in `data/proactive_case.yaml`.
- An investigating officer's notice after an FIR can repeat a transaction already ticketed on the portal; it is
  linked to the first case, not held twice. Notices also arrive by e-mail or as PDF letters (four synthetic ones in
  `data/notices/` and `data/notices.yaml`); `python -m pipeline.notices read FILE` shows what is read from one
  ([D-52](docs/DECISIONS.md)). A scanned letter needs `pytesseract` and `pdf2image` (optional).
- The split between several victims is a record for the refund stage. The investigating officer decides the actual
  payout.
- Every referral between banks is signed with a SHA-256 hash chain (DECISIONS D-17); `python -m pipeline.chain`
  checks every hop. A real portal would also sign with each bank's key; a hash chain shows tampering, not who did it.
- AMLworld is synthetic and labels laundering, not cyber-fraud mule accounts.
- The scoring weights are the hand-set start weights until a version trained on AMLworld is approved. AMLworld's
  labels are synthetic laundering, so its test shows how well the weights rank AMLworld accounts, not real mule
  accounts.
- The model runs locally, so account data doesn't leave the bank.
- Bank Z works tickets by hand: its desk opens at 10:00 and takes about 20 minutes for a ticket. Today's desk in
  Before / after is the same kind of stated model (desk hours, lookup times, whole-account freezes), not a
  measurement.
- AMLworld amounts are converted at the dataset's own exchange rates and ₹95 per US dollar.

## Limits

- The tool cannot make victims report faster or skip police checks, FIRs or court orders.
- A real bank sees only its own accounts. Here each bank's schema holds only its own rows and its users cannot read
  another bank's, but all three banks run on one Exasol Personal for the demo.
- Cash already withdrawn is gone; the tool records where it left.
- Releasing a mandatory hold without the officer, the refund stage and court orders are out of scope.

## Licence

MIT, see [LICENSE](LICENSE). Third-party terms in [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md).