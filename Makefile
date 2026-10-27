# Proportionate Holds. `make help` lists targets.
PY ?= python3
CASE_FILE ?=
UNTIL ?= 2026-10-18 09:00:00

.DEFAULT_GOAL := help
.PHONY: help install test lint oracle gen check setup doctor db seed check-db demo-reset data lab learn run explain \
        agent night desk today web web-build

help: ## List targets
	@grep -E '^[a-zA-Z0-9_-]+:.*## ' Makefile | awk -F':.*## ' '{printf "  %-14s %s\n", $$1, $$2}'

install: ## Install Python dev dependencies
	$(PY) -m pip install -r requirements-dev.txt

test: ## Unit, oracle and property tests (no DB)
	$(PY) -m pytest -q

lint: ## Ruff
	$(PY) -m ruff check pipeline tests app sql/udf

oracle: ## Print the oracle's LIBR/FIFO/PRO_RATA table (CASE_FILE=...)
	$(PY) -m tests.show_case $(CASE_FILE)

gen: ## Regenerate data/extra_cases.yaml (seed 42)
	$(PY) -m pipeline.gen_extra_cases

check: ## Lint + generated data fresh + all tests that need no database
	$(PY) -m ruff check pipeline tests app sql/udf
	$(PY) -m pipeline.gen_extra_cases --check
	$(PY) -m pytest -q
	@echo "check: all green"

setup: ## Install Exasol Personal + Python 3 SLC (macOS/Linux)
	sh scripts/setup.sh

doctor: ## Check Exasol, the SLC, the schemas and the database users
	$(PY) -m pipeline.doctor

db: ## Build one schema per bank (PH_X, PH_Y, PH_Z), the portal (PH_PORTAL) and the users (needs PH_ROLE_PASSWORD)
	$(PY) -m pipeline.run_sql

seed: ## Load the synthetic world: each bank gets only its own rows, the tickets wait on the portal
	$(PY) -m pipeline.load_cases --verify

check-db: ## Rebuild, reload, doctor, then every database test (resets the data; run `make night` after)
	$(PY) -m pipeline.run_sql
	$(PY) -m pipeline.load_cases --verify
	$(PY) -m pipeline.doctor
	$(PY) -m pytest -q -m db
	@echo "check-db: all green"

demo-reset: ## Reload the data: every ticket waits on the portal again (< 60 s)
	$(PY) -m pipeline.load_cases --verify

data: ## Load AMLworld HI-Small from data/raw/ into PH_AML, then give each bank its share
	$(PY) -m pipeline.load_amlworld
	$(PY) -m pipeline.load_cases --verify

lab: ## AMLworld trace and scoring tests in PH_LAB (needs `make data`); writes docs/AMLWORLD_TESTS.md
	$(PY) -m pipeline.lab

learn: ## Train the learned signal on AMLworld (needs `make data`), then reload the banks with it
	$(PY) -m pipeline.learned
	$(PY) -m pipeline.load_cases --verify

night: ## Reset, then run the demo night across the three banks to 09:00 (UNTIL=..., MODE=llm)
	$(PY) -m pipeline.night --until "$(UNTIL)" --mode $(or $(MODE),pipeline)

desk: ## Continue the night to 10:30: Bank Z's desk opens at 10:00 and works its ticket
	$(PY) -m pipeline.night --until "2026-10-18 10:30:00" --no-reset

agent: ## Bank X's agent works one ticket on its own (CASE=CASE_DEMO, MODE=llm)
	$(PY) -m pipeline.agent $(or $(CASE),CASE_DEMO) --bank BANK_X --mode $(or $(MODE),pipeline)

today: ## Today's desk vs the agent on the demo ticket (after `make night`)
	$(PY) -m pipeline.today CASE_DEMO

run: ## Start the API on http://127.0.0.1:8000 (docs at /docs)
	$(PY) -m app.api

web: ## Start the officer console on http://localhost:5173 (needs `make run` in another terminal)
	cd app/web && npm install --no-audit --no-fund && npm run dev

web-build: ## Build the console (what CI checks)
	cd app/web && npm ci --no-audit --no-fund && npm run build

explain: ## Oracle vs Exasol for one case (not built yet)
	@echo 'make explain is not built yet (oracle vs Exasol, table by table)'; exit 1