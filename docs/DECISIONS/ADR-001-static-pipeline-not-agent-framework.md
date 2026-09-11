# ADR-001: A static pipeline of plain functions, not an agent framework

**Status:** accepted. **Applies to:** `pipeline/`, `application/runner.py`.

## Context

The work is multi-step document processing with language-model calls in the
middle, which is the shape agent frameworks are advertised for. The evaluation
this project rests on needs run-to-run comparability: the fairness experiment
requires that two runs differ only in the candidate's identity; the routing
comparison requires that three configurations differ only in tier selection;
the calibration ablation requires that the only difference is the presence of
anchors. A system that can choose a different sequence of steps on different
runs destroys that control.

## Decision

Eleven nodes in a static tuple (`pipeline/registry.py`), each a plain function
with one signature, `(RunState, Deps) -> NodeResult`, driven by a runner short
enough to read in one sitting. State is persisted after every node in one
transaction. No orchestration framework of any kind.

## Alternatives

- **A graph framework with checkpointing.** The closest fit. Rejected because
  the graph here is static, so the framework's central feature — dynamic
  routing between nodes — would go unused, while its checkpointing is a few
  dozen lines of SQLite in this codebase, written to this schema.
- **A role-playing multi-agent framework.** Rejected because there are no
  agents. There are functions, and the language model is called by three of
  them with a fixed schema each.
- **A homemade workflow DSL.** Rejected because a tuple of functions is already
  the simplest expression of a fixed sequence, and a DSL would be a second
  language to learn for no additional expressiveness.

## Trade-offs

Retries, parallelism (criteria within ASSESS), resumption and the budget check
are hand-written, roughly 150 lines in total, and each is tested on its own. In
exchange: no dependency to track through a sprint, control flow a stranger can
follow in three minutes, exact cost prediction, byte-reproducible evaluation
runs, and a spine that runs against fakes in under a second.

## Consequences

Adding a node is a one-line change in one file. Resumption is not a separate
path: the runner skips nodes marked complete, so a resumed run and a fresh run
execute the same code. The reviewer gate is a missing call — the runner stops
after REVIEW and DELIVER is only ever invoked by a use case that runs after a
decision exists.

Triggers for revisiting, none of which has fired: a node whose next step
depends on a model's output rather than on the state machine; a need for
human-in-the-loop *inside* a node rather than between them; a second workflow
sharing nodes with this one; or a runtime where per-node persistence to one
SQLite file is not available.
