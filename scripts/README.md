# Operational and one-off tooling

Not imported by the application. Each script is runnable on its own and says
at the top what it is for.

| Script | Purpose | Make target |
|---|---|---|
| `check_pii.py` | Refuse candidate contact details and any file under `data/` outside `data/samples/` | `check-pii`, and a pre-commit hook |
| `check_secrets.py` | Refuse credentials, key-shaped strings, and `.env` files | `check-secrets`, and a pre-commit hook |
| `make_synthetic_corpus.py` | Generate the four synthetic candidates demo mode reads | `seed` |
| `make_adversarial_corpus.py` | Generate the injection and true-negative documents | `corpus` |
| `overrides_to_cases.py` | Draft evaluation cases from reviewer overrides | — |
| `tune_span_thresholds.py` | Sweep the two span thresholds and print the trade-off table | `tune-thresholds` |
| `export_schemas.py` | Regenerate `contracts/schemas/`, or `--check` that they are current | `schemas` |
| `rubric_lint.py` | Validate every rubric and print its hash | `rubric-lint` |
| `migrate.py` | Apply migrations to a database file | — |
