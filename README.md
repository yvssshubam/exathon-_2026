# Proportionate Holds

A golden-hour copilot for the bank nodal officer. When a cyber-fraud ticket reaches the bank, at any hour, an agent
works it at once: it follows the victim's money through the bank, works out how much of each account is really the
victim's, places the holds the rules require (amount only), files the onward entries and exits, and leaves the
officer a short list of judgment calls, each sized to the victim's share and written up with its reasons.

Built for **ExaThon India 2026** (Track 01: Money, Risk & Regulation). Every number is computed in
**Exasol Personal**: the trace (`CONNECT BY NOCYCLE`), the victim's share (a Python UDF), the account evidence, the
rules engine, the checker, the hold ledger and the "today's desk" replay. The agent only orders the tools.

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
| Bank Y gets its onward ticket | 10:35 | 02:16 |

At 9 a.m. the officer sees what was done overnight and three decisions waiting: narrow Murugan's hold to the ₹500
that is traceable to the victim, and whole-account freezes proposed (never placed) for two mule-like accounts.

**The agent orchestrates, the database calculates, the rules permit, the officer decides.** The agent can place
only the holds the bank is required to place; a deterministic checker (conservation to the paisa, policy rules) must
pass first; there is no full-freeze tool. An optional local model can choose the order of the tools and word the
summary, and the fixed order takes over whenever it fails ([DESIGN.md](docs/DESIGN.md#where-ai-fits)).

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
# copy host:port (and fingerprint, if shown) from `exasol info` into .env (start from .env.example)
make doctor           # every check should say PASS
make check-db         # builds schema PH, loads the cases, runs the database tests (incl. the 2:13 a.m. run)
```

Without `make` (Windows PowerShell), the same steps are:

```sh
python -m pipeline.doctor
python -m pipeline.run_sql sql              # build schema PH
python -m pipeline.load_cases --verify      # load the cases; every ticket QUEUED
python -m pytest -q -m db
```

**AMLworld background (optional, about 10 minutes once).** From
[Kaggle](https://www.kaggle.com/datasets/ealtman2019/ibm-transactions-for-anti-money-laundering-aml) (free
account), download `HI-Small_Trans.csv`, `HI-Small_accounts.csv` and `HI-Small_Patterns.txt` into `data/raw/`.
Don't open and re-save them in Excel: it drops the leading zeros of the bank codes. Then:

```sh
python -m pipeline.load_amlworld --check    # files present and shaped as expected (no database)
make data                                   # or: python -m pipeline.load_amlworld
                                            #     python -m pipeline.load_cases --verify
```

The import runs once into schema `PH_AML`; `make db`, `make demo-reset` and the console's Reset keep it.

Exasol Personal is used under its EULA and is not redistributed here; the `exasol` launcher is MIT.

## Run it

```sh
python -m app.api                                     # API on http://127.0.0.1:8000 (docs at /docs)
cd app/web && npm install && npm run dev              # console on http://localhost:5173
```

In the console, **Review** opens with what the agent already did and a band of before/after figures for the day.
*Replay the day* (demo controls, bottom of the page) lets the agent work every queued ticket. The decisions are
grouped by account: honest customers whose hold can be cut to the victim's share, and mule accounts that could be
frozen whole (one approval covers every ticket that names the account). Open a ticket to see where the money went,
hop by hop, with the account's evidence and your buttons on the right; the agent's log (every tool call with its
Exasol statement IDs) and the paperwork are below. **Before / after** replays today's manual process on the same
ticket, or the whole day. *Reset* puts every ticket back in the queue.

From a terminal instead:

```sh
python -m pipeline.agent CASE_DEMO                    # the agent works the 2:13 a.m. ticket and prints its log
python -m pipeline.officer waiting                    # what is waiting for the officer
python -m pipeline.officer approve CASE_DEMO ACC_D PROPOSE_NARROW --officer priya
python -m pipeline.today CASE_DEMO                    # today's desk vs the agent, as JSON
```

**Optional local model** (free, offline): install [Ollama](https://ollama.com), `ollama pull qwen2.5:7b`, set
`PH_LLM=ollama` in `.env`, then `python -m pipeline.agent CASE_DEMO --mode llm` (or *With the model* in the ticket
queue). The holds come out the same; the log shows which tool the model chose at each step.

## Repository

```
config/      rules.yaml (automatic vs officer, legal bases), scoring.yaml (signals), banks.yaml, mcp_settings.json
data/        demo_case.yaml (the 2:13 a.m. ticket), extra_cases.yaml (generated, seed 42), loop_case.yaml
sql/         schema and views (make db); sql/steps/ the agent's tools, run inside Exasol
pipeline/    agent.py (the agent), steps.py, officer.py, today.py (Compare), llm.py (optional local model),
             loaders, generators, doctor
app/api/     FastAPI service the console talks to
app/web/     officer console (React + Vite)
tests/       oracles (attribution, scoring, rules) and their tests; -m db tests run against Exasol
docs/        PROBLEM_STATEMENT.md, PROBLEM_DISCOVERY.md, DESIGN.md, DECISIONS.md, REQUIREMENTS.md (first spec)
```

## Limits

- The tool cannot make victims report faster or skip police checks, FIRs or court orders.
- A real bank sees only its own accounts; other banks' hops arrive via CFCFRMS. The demo simulates that boundary
  with a shared synthetic dataset, and the next bank's outcome depends on how that bank works its ticket.
- Cash already withdrawn is gone; the tool records where it left.
- Today's desk in Compare is a stated model (desk hours, lookup times, whole-account freezes), not a measurement.
- Scoring weights are hand-set for the demo, not validated on real data; they inform the rules and are never shown
  as a verdict.
- Releasing a mandatory hold, notices to holders and the refund stage are out of scope.

## Licence

MIT, see [LICENSE](LICENSE). Third-party terms in [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md).