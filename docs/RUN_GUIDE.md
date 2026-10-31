# Run guide

Everything runs on one laptop, offline once installed, at no cost: Exasol Personal, Python and (for the console)
Node. Three simulated banks and a simulated national portal share one local database, each bank in its own schema.

## What you need

| | macOS | Linux | Windows |
| --- | --- | --- | --- |
| Exasol Personal | macOS 15+ on Apple Silicon, 8 GB RAM | amd64 or arm64 with Podman | amd64; the launcher offers to install Podman |
| Python | 3.11 or later | 3.11 or later | 3.11 or later |
| Node (console only) | 20.19+ or 22.12+ | 20.19+ or 22.12+ | 20.19+ or 22.12+ |
| `make` | built in | built in | optional: every step below also has a plain command |

No cloud account and no paid service. The optional AMLworld background data needs a free Kaggle account to download.

## 1. Get the code and the Python packages

```sh
git clone https://github.com/yvssshubam/exathon-_2026.git
cd exathon-_2026
python3 -m venv .venv && . .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -r requirements-dev.txt
python -m pytest -q                                   # needs no database; should be all green
```

## 2. Install Exasol Personal and the Python 3 container

**macOS and Linux:** `make setup` does all of this and is safe to re-run. By hand, the same steps are:

```sh
curl -fsSL https://downloads.exasol.com/exasol-personal/installer.sh | sh   # the `exasol` launcher (MIT)
exasol install local                    # the local database (Linux: needs Podman)
exasol slc install python3              # the Python 3 script language container (local installs ship without one)
exasol slc list                         # should list a Python 3 container
```

If `exasol` is not found after the installer, open a new terminal (the installer updates `PATH`).

**Windows:** install the launcher from the Exasol download portal, then in PowerShell run `exasol install local`
(accept the Podman install it offers) and `exasol slc install python3`.

The Python 3 container matters: the victim's share and the credit matching run as Python UDFs inside Exasol.

## 3. Connection settings

```sh
cp .env.example .env                    # Windows: copy .env.example .env
exasol info                             # shows the host, port and, if any, a certificate fingerprint
```

Fill in `.env`:

- `EXA_DSN`: `host:port` from `exasol info`. Don't assume the port: read it each time you reinstall.
- `EXA_FINGERPRINT`: the fingerprint, if `exasol info` shows one. Locally `EXA_VERIFY_CERT=false` is fine.
- `EXA_USER` / `EXA_PASSWORD`: the admin user that builds the schemas (`sys` / `exasol` by default).
- `PH_ROLE_PASSWORD`: any password you choose. `make db` creates each bank's maker and checker users and the portal
  user with it; it stays on your machine.
- `OWN_BANK_ID`: the bank whose console and commands you use (`BANK_X`).

## 4. Build the banks and load the demo

```sh
make db        # schemas PH_X, PH_Y, PH_Z (one per bank) and PH_PORTAL, plus the maker, checker and portal users
make seed      # each bank gets only its own accounts and transactions; the complaints wait on the portal
make doctor    # every line should say PASS
```

Without `make`:

```sh
python -m pipeline.run_sql
python -m pipeline.load_cases --verify
python -m pipeline.doctor
```

`make doctor` checks, in order: the launcher answers, a Python 3 container is installed, `.env` is filled in, Python
connects, a Python UDF actually runs, the schemas exist, and the maker, checker and portal users can log in. Read it
top down: later checks depend on earlier ones, and each FAIL prints its fix.

## 5. Run the demo

```sh
make night     # reset, then run the demo night across the three banks to 09:00 (python -m pipeline.night)
make run       # the API on http://127.0.0.1:8000, docs at /docs (python -m app.api)
make web       # in a second terminal: the officer console on http://localhost:5173
```

In the console, sign in with any name. The dark **Demo mode** bar drives the simulation:

- **Replay the night to 09:00** reloads the data and runs the night again. Use it to reset the demo.
- **Run the rest of the day** carries on to 23:59, so every complaint of the day arrives and is worked.
- **Manual vs system** and **The whole night** are the comparison and the narrator's view across all three banks.

To reset from a terminal instead: `make demo-reset` (under a minute), then `make night`.

## 6. Tests

```sh
make check     # lint, generated data up to date, every test that needs no database
make check-db  # rebuild, reload, doctor, then every database test (it resets the data: run `make night` after)
```

## 7. Optional: AMLworld background traffic (about 10 minutes once)

From [Kaggle](https://www.kaggle.com/datasets/ealtman2019/ibm-transactions-for-anti-money-laundering-aml), download
`HI-Small_Trans.csv`, `HI-Small_accounts.csv` and `HI-Small_Patterns.txt` into `data/raw/`. Don't open and re-save
them in Excel: it drops the leading zeros of the bank codes. Then:

```sh
python -m pipeline.load_amlworld --check    # files present and shaped as expected (no database)
make data      # imports into PH_AML once, then gives each bank its share
make lab       # trace and scoring tests on the labelled patterns (PH_LAB), report in docs/AMLWORLD_TESTS.md
make learn     # propose new score weights trained inside Exasol, and run the gate
make bench     # scale benchmark: 100 complaints at once (TICKETS=1000 for more), report in docs/BENCHMARK.md
```

A proposed weight version goes live only when a named person approves it:
`python -m pipeline.weights approve --version N --by "NAME"`. `python -m pipeline.weights show` lists every version.

## 8. Optional: the local model

Free and offline. Install [Ollama](https://ollama.com), then:

```sh
ollama pull qwen2.5:7b
```

Set `PH_LLM=ollama` in `.env` and run `make night MODE=llm` (or `python -m pipeline.night --mode llm`). The model
orders the agent's tools and words the reasons; the holds come out the same with it off. If it is slow, not running
or gives an unusable answer, the fixed pipeline takes over.

## Troubleshooting

| Symptom | What to do |
| --- | --- |
| Can't connect | `exasol status` (is it running?), then `exasol info` for the current host and port; update `EXA_DSN` |
| Certificate error | Put the fingerprint from `exasol info` in `EXA_FINGERPRINT`, or keep `EXA_VERIFY_CERT=false` locally |
| A UDF fails, or doctor's UDF check fails | The Python 3 container is missing: `exasol slc install python3`, then `exasol slc list` |
| The maker or checker user can't log in | Set `PH_ROLE_PASSWORD` in `.env` and run `make db` again |
| Anything else in the database | `exasol diag local`, or `python -m pipeline.doctor --full`, which runs it for you |
| Slow or out of memory on a 16 GB laptop | Close other heavy apps before `make data`; run `make bench` with the default 100 complaints first |
| Bank codes look wrong after `make data` | The CSVs were re-saved in Excel; download them again |
| The console won't start | Check `node --version` (20.19+ or 22.12+) and that ports 5173 and 8000 are free |