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

# The offline suite. Anything touching a real service is marked and excluded,
# and so is the smoke assertion that only makes sense after `make seed`.
OFFLINE := -m "not live and not smoke"

# No recipe sets an environment variable inline. From PowerShell, make runs
# recipes through cmd.exe, where `VAR=value command` is not a thing; from Git
# Bash it is. Demo mode is therefore a flag the commands take, so every target
# runs the same way from either shell.

.PHONY: help setup check test schemas rubric-lint demo run seed doctor api api-live web web-check corpus eval eval-holdout eval-accept eval-routing eval-calibration eval-fairness tune-thresholds check-pii check-secrets smoke clean

help:
	@echo "setup         create the virtual environment and install the project"
	@echo "seed          generate the synthetic candidates and assess them offline"
	@echo "demo          run the reviewer interface against the offline defaults"
	@echo "doctor        check this deployment and say what to fix"
	@echo "api           the HTTP API on :8000 (demo mode), for the React frontend"
	@echo "web           the React frontend on :5173, proxying /api to the API"
	@echo "api-live      the HTTP API on real documents (no demo mode)"
	@echo "web-check     lint and build the frontend"
	@echo "check         lint, format, types, both scanners, and the offline suite"
	@echo "test          the offline test suite only"
	@echo "check-pii     refuse candidate contact details in the tree"
	@echo "check-secrets refuse credentials and .env files in the tree"
	@echo "smoke         setup, seed, and assert a reviewable candidate (CI)"
	@echo "corpus        regenerate the adversarial document corpus"
	@echo "eval          run the benchmark and check the regression gate"
	@echo "schemas       regenerate contracts/schemas/*.json"
	@echo "rubric-lint   validate every rubric"
	@echo "clean         remove caches and build artefacts"

setup:
	$(PYTHON) -m venv .venv
	$(PY) -m pip install --upgrade pip
	$(PY) -m pip install -e ".[dev]"
	@echo ""
	@echo "Done. Activate with: source $(VENV_BIN)/activate"

# The two scanners run here as well as in the pre-commit hook, so a commit
# made with hooks disabled is still caught before it merges.
check: check-pii check-secrets
	$(RUFF) check .
	$(RUFF) format --check .
	$(MYPY)
	$(PYTEST) $(OFFLINE) -q

check-pii:
	$(PY) scripts/check_pii.py --all

check-secrets:
	$(PY) scripts/check_secrets.py --all

test:
	$(PYTEST) $(OFFLINE) -q

schemas:
	$(PY) scripts/export_schemas.py

rubric-lint:
	$(PY) scripts/rubric_lint.py

# The synthetic candidates, generated and then assessed. Demo mode on, so the
# only folder read is data/samples/synthetic and nothing is sent anywhere.
# Idempotent: a candidate already assessed is reused, not re-run.
seed:
	$(PY) -m scripts.make_synthetic_corpus
	$(PY) -m app.cli.main --demo process --role ai-engineer

# The reviewer interface, against the offline defaults: the fake model provider,
# blind mode on, no API key, demo mode on. A fresh clone runs this after seed.
demo:
	$(PY) -m app.demo

doctor:
	$(PY) -m app.cli.main doctor

# The interface without demo mode, for a pilot: uploads accepted, the CSV sink
# present, and whatever .env configures. Read RUNBOOK.md first.
run:
	$(PY) -m streamlit run app/main.py

# The HTTP API, in demo mode, on the port the frontend's dev proxy expects.
api:
	$(PY) -m app.cli.main --demo api --logs

# The HTTP API on real documents: uploads accepted, and the provider chosen
# on the admin page (Settings) makes the calls. Read RUNBOOK.md first.
api-live:
	$(PY) -m app.cli.main api --logs

# The React frontend. Needs Node; `npm install` runs once in frontend/.
web:
	cd frontend && npm install --silent && npm run dev

web-check:
	cd frontend && npm install --silent && npm run lint && npm run build

# What CI runs on a clean clone: everything above, then one assertion that a
# synthetic candidate reached a reviewer. If this passes, the three-command
# setup in the README is true.
smoke: seed
	$(PYTEST) tests/workflow/test_clean_clone_smoke.py -q -m smoke

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

# Counterfactual pairs: the same CV under different identity tokens, blind mode
# off and on, plus a control that runs each base CV three times with identity
# held fixed. The control is not optional — a flip rate without its noise floor
# is uninterpretable, and the code refuses to print one.
eval-fairness:
	$(PY) -m eval.fairness.runner

# Sweep the two span thresholds over real and fabricated corpora, and print the
# ROC table. The recommended operating point goes into config/limits.yaml and
# the table into docs/EVALUATION.md.
tune-thresholds:
	$(PY) -m scripts.tune_span_thresholds

# POSIX tools; the one target that needs Git Bash on Windows.
clean:
	rm -rf .pytest_cache .mypy_cache .ruff_cache build dist *.egg-info
	find . -type d -name __pycache__ -not -path "./.venv/*" -exec rm -rf {} +
