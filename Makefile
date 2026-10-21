# Proportionate Holds. `make help` lists targets.
PY ?= python3
CASE_FILE ?=

.DEFAULT_GOAL := help
.PHONY: help install test lint oracle gen check setup doctor db seed check-db demo-reset data run explain \
        agent night today web web-build

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

doctor: ## Check Exasol is up, SLC works, pyexasol connects
	$(PY) -m pipeline.doctor

db: ## Rebuild schema PH from numbered sql/ files
	$(PY) -m pipeline.run_sql sql

seed: ## Load demo, extra and loop cases into Exasol
	$(PY) -m pipeline.load_cases

check-db: ## Doctor + rebuild schema + load cases + database tests
	$(PY) -m pipeline.doctor
	$(PY) -m pipeline.run_sql sql
	$(PY) -m pipeline.load_cases --verify
	$(PY) -m pytest -q -m db
	@echo "check-db: all green"

demo-reset: ## Rebuild the schema and reload the cases: every ticket QUEUED again (< 60 s)
	$(PY) -m pipeline.run_sql sql
	$(PY) -m pipeline.load_cases --verify

data: ## Load AMLworld HI-Small (not built yet)
	@echo 'make data is not built yet (AMLworld HI-Small download, map, IMPORT)'; exit 1

run: ## Start the API on http://127.0.0.1:8000 (docs at /docs)
	$(PY) -m app.api

agent: ## The agent works the 2:13 a.m. demo ticket (MODE=llm to let a local model orchestrate)
	$(PY) -m pipeline.agent CASE_DEMO --mode $(or $(MODE),pipeline)

night: ## The agent works every queued ticket in the order they landed
	$(PY) -m pipeline.agent --queue --mode $(or $(MODE),pipeline)

today: ## Today's desk vs the agent on the demo ticket (needs `make agent` first)
	$(PY) -m pipeline.today CASE_DEMO

web: ## Start the officer console on http://localhost:5173 (needs `make run` in another terminal)
	cd app/web && npm install --no-audit --no-fund && npm run dev

web-build: ## Build the console (what CI checks)
	cd app/web && npm ci --no-audit --no-fund && npm run build

explain: ## Oracle vs Exasol for one case (not built yet)
	@echo 'make explain is not built yet (oracle vs Exasol, table by table)'; exit 1