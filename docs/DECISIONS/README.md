# Architecture Decision Records

One file per decision: context, decision, alternatives, trade-offs,
consequences, and the triggers that would reopen it. Read them in order; each
assumes the ones before it.

| | Decision | Where it lives |
|---|---|---|
| [ADR-001](ADR-001-static-pipeline-not-agent-framework.md) | A static pipeline of plain functions, not an agent framework | `pipeline/`, `application/runner.py` |
| [ADR-002](ADR-002-streamlit-with-no-business-logic.md) | An interface with no business logic in it — Streamlit first, then React over an HTTP API (amended) | `app/`, `frontend/` |
| [ADR-003](ADR-003-sqlite-and-a-blob-store.md) | SQLite plus a content-addressed blob store | `infrastructure/storage/` |
| [ADR-004](ADR-004-thin-model-client-not-a-framework.md) | A thin model client with composable decorators (amended: the repair resends the document) | `infrastructure/models/` |
| [ADR-005](ADR-005-evidence-first-extraction.md) | Evidence first, never score first | `domain/provenance/`, `pipeline/assess.py` |
| [ADR-006](ADR-006-deterministic-scoring.md) | Two pure functions turn evidence into a recommendation | `domain/rules/` |
| [ADR-007](ADR-007-calibration-retrieval-off-by-default.md) | Retrieval only for calibration, and off until it earns its place | `pipeline/calibrate.py` |
| [ADR-008](ADR-008-human-approval-before-delivery.md) | A person approves before anything is delivered | `domain/state_machine.py`, `pipeline/deliver.py` |
| [ADR-009](ADR-009-synthetic-data-in-public.md) | Synthetic data in public, real data only in a private pilot | `data/samples/`, `scripts/check_*.py` |
| [ADR-010](ADR-010-cost-tracking-from-the-first-call.md) | Cost is accounted from the first call, and never estimated | `application/accounting.py` |

A new decision gets the next number, the same five sections, and a line here.
A reversed decision is not deleted: its status changes and the record that
replaced it is linked, so the history of why stays readable.
