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

.PHONY: help setup check test schemas rubric-lint demo corpus clean

help:
	@echo "setup    create the virtual environment and install the project"
	@echo "check    lint, format check, types, and the offline test suite"
	@echo "demo     run the reviewer interface against the offline defaults"
	@echo "corpus   regenerate the adversarial document corpus"
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

clean:
	rm -rf .pytest_cache .mypy_cache .ruff_cache build dist *.egg-info
	find . -type d -name __pycache__ -not -path "./.venv/*" -exec rm -rf {} +
