# Proportionate Holds

When a cyber-fraud complaint reaches a bank, Proportionate Holds traces the victim's money through every account
it touched, works out how much of each account's balance is really the victim's, scores each account as likely
mule or honest receiver, and recommends either holding that exact amount or (rarely, with reasons) the whole
account. An officer approves every hold. Honest customers keep using their accounts.

Built for **ExaThon India 2026** (Track 01: Money, Risk & Regulation). Tracing, attribution, scoring and the audit
trail all run inside **Exasol Personal**: SQL `CONNECT BY NOCYCLE` for the trace, a Python 3 UDF for the
attribution rules, set-based SQL for scoring.

> **Status: work in progress.** The attribution rules are implemented and tested in a reference oracle;
> the Exasol pipeline, API and console are being built.

## Problem statement

Bank fraud desks can't hold exactly the money a cyber-fraud victim lost in each account it passed through,
because stolen and honest money mix and the split is worked out by hand, one hop at a time. If this worked,
victims would get more back sooner and honest customers would keep using their accounts.

## The demo case in one table

| | Without the tool | With the tool |
| --- | --- | --- |
| Victim's money held | ₹69,500 | ₹1,49,500 |
| Honest money locked | ₹8,19,000 (a fuel station's whole account) | ₹0 (lien on ₹1,000 only) |
| Time to trace every hop inside the bank | about 2 hours | seconds |

## Data is synthetic

No real bank data, customer data or employer data is used. Background traffic is IBM's public, synthetic
AMLworld dataset (downloaded by a script, never committed); the demo case and 20 extra victims are invented and
generated with a fixed seed. See [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md).

## Quick start (no database needed)

```sh
python3 -m venv .venv && . .venv/bin/activate
make install
make check           # lint + 64 tests: the section 9 numbers, conservation for 21 victims, property tests
make oracle          # the demo case under LIBR, FIFO and pro rata, side by side
```

## Full setup (Exasol Personal)

Prerequisites: macOS 15+ on Apple Silicon with 8 GB RAM, Linux amd64/arm64 with Podman, or Windows amd64
(the launcher offers Podman). Python 3.11+. No cloud account.

```sh
make setup           # installs the exasol launcher, `exasol install local`, `exasol slc install python3`
# copy host:port (and fingerprint, if shown) from `exasol info` into .env
make doctor          # every check should say PASS
make check-db        # builds schema PH, loads the cases, runs the DB tests
```

Exasol Personal is used under its EULA and is not redistributed here; the `exasol` launcher is MIT.

## Repository

```
config/      lanes.yaml (legal clocks), scoring.yaml (signals, weights), mcp_settings.json (read-only)
data/        demo_case.yaml, extra_cases.yaml (generated, seed 42), loop_case.yaml
sql/         numbered, idempotent SQL run in order by `make db`
pipeline/    loaders, SQL runner, doctor, generators
tests/       oracle.py (reference implementation) and its tests
docs/        REQUIREMENTS.md, DECISIONS.md, PROBLEM_STATEMENT.md
```

## Limits

- The tool cannot make victims report faster or skip police checks, FIRs or court orders.
- A real bank sees only its own accounts; other banks' hops arrive via CFCFRMS. The demo simulates that boundary.
- Cash already withdrawn is gone; the tool records where it left.
- Lane B follows an RBI **draft** (11 Sep 2026) that is not in force.
- Scoring weights are hand-set for the demo, not validated on real data.

## Licence

MIT, see [LICENSE](LICENSE). Third-party terms in [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md).