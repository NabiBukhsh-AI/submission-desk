# Submission Desk
#
# Every target runs offline. Nothing here needs an API key or a network.

.DEFAULT_GOAL := help

# The interpreter used to create the virtual environment. Overridable:
#   make setup PYTHON=/usr/bin/python3.12
PYTHON ?= python3.11

# Windows puts the venv executables in Scripts/, everything else in bin/.
ifeq ($(OS),Windows_NT)
VENV_BIN := .venv/Scripts
else
VENV_BIN := .venv/bin
endif

PY     := $(VENV_BIN)/python
PYTEST := $(VENV_BIN)/pytest
RUFF   := $(VENV_BIN)/ruff
MYPY   := $(VENV_BIN)/mypy

# The offline suite. Anything touching a real service is marked and excluded.
OFFLINE := -m "not live"

.PHONY: help setup check test schemas rubric-lint demo corpus eval eval-holdout eval-accept eval-routing eval-calibration clean

help:
	@echo "setup    create the virtual environment and install the project"
	@echo "check    lint, format check, types, and the offline test suite"
	@echo "demo     run the reviewer interface against the offline defaults"
	@echo "corpus   regenerate the adversarial document corpus"
	@echo "eval     run the benchmark and check the regression gate"
	@echo "test     the offline test suite only"
	@echo "schemas  regenerate contracts/schemas/*.json"
	@echo "rubric-lint  validate every rubric"
	@echo "clean    remove caches and build artefacts"

setup:
	$(PYTHON) -m venv .venv
	$(PY) -m pip install --upgrade pip
	$(PY) -m pip install -e ".[dev]"
	@echo ""
	@echo "Done. Activate with: source $(VENV_BIN)/activate"

check:
	$(RUFF) check .
	$(RUFF) format --check .
	$(MYPY)
	$(PYTEST) $(OFFLINE) -q

test:
	$(PYTEST) $(OFFLINE) -q

schemas:
	$(PY) scripts/export_schemas.py

rubric-lint:
	$(PY) scripts/rubric_lint.py

# The reviewer interface, against the offline defaults: the fake model provider,
# blind mode on, no API key. A fresh clone runs this.
demo:
	$(PY) -m streamlit run app/main.py

# The adversarial corpus, regenerated. Never committed: the generator is the
# readable artefact, and a repository of files that look like real CVs invites
# somebody to treat them as real CVs.
corpus:
	$(PY) -m scripts.make_adversarial_corpus

# The benchmark. Runs every case in the dev split through the real use case,
# writes CSV, JSONL and HTML, and exits non-zero if a gated metric regressed
# beyond its tolerance.
eval:
	$(PY) -m eval.runner --split dev

# The holdout, run once at the end. The gap between this and the dev split is
# the honest estimate of how much dev performance was overfitting.
eval-holdout:
	$(PY) -m eval.runner --split holdout

# Accept the current result as the thing future runs are compared against.
eval-accept:
	$(PY) -m eval.runner --split dev --accept

# The three routing policies over the same cases, so the cost claim has a
# number behind it and two controls either side.
eval-routing:
	$(PY) -m eval.experiments.routing

# With and without calibration, over the same cases. The experiment the
# disabled-by-default flag exists to wait for.
eval-calibration:
	$(PY) -m eval.experiments.calibration

clean:
	rm -rf .pytest_cache .mypy_cache .ruff_cache build dist *.egg-info
	find . -type d -name __pycache__ -not -path "./.venv/*" -exec rm -rf {} +
