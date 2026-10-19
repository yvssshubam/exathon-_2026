# Proportionate Holds. `make help` lists targets.
PY ?= python3
CASE_FILE ?=

.DEFAULT_GOAL := help
.PHONY: help install test lint oracle gen check setup doctor db seed check-db demo-reset data run explain

help: ## List targets
	@grep -E '^[a-zA-Z0-9_-]+:.*## ' Makefile | awk -F':.*## ' '{printf "  %-14s %s\n", $$1, $$2}'

install: ## Install Python dev dependencies
	$(PY) -m pip install -r requirements-dev.txt

test: ## Unit, oracle and property tests (no DB)
	$(PY) -m pytest -q

lint: ## Ruff
	$(PY) -m ruff check pipeline tests

oracle: ## Print the oracle's LIBR/FIFO/PRO_RATA table (CASE_FILE=...)
	$(PY) -m tests.show_case $(CASE_FILE)

gen: ## Regenerate data/extra_cases.yaml (seed 42)
	$(PY) -m pipeline.gen_extra_cases

check: ## Lint + generated data fresh + all tests that need no database
	$(PY) -m ruff check pipeline tests
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

demo-reset: ## Restore the seeded demo state (< 60 s)
	$(PY) -m pipeline.run_sql sql
	$(PY) -m pipeline.load_cases --verify

data: ## Load AMLworld HI-Small (not built yet)
	@echo 'make data is not built yet (AMLworld HI-Small download, map, IMPORT)'; exit 1

run: ## Start API and console (not built yet)
	@echo 'make run is not built yet (API and console)'; exit 1

explain: ## Oracle vs Exasol for one case (not built yet)
	@echo 'make explain is not built yet (oracle vs Exasol, table by table)'; exit 1