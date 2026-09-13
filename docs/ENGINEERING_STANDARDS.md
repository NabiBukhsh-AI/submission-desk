# Engineering Standards

Engineering constitution for **Submission Desk**. Read this file completely before working on the project. It overrides habit, convention, and anything inferred from surrounding code. If a requirement conflicts with this file, raise the conflict rather than choosing silently.

---

## 1. Project objective

Submission Desk is an evidence-first candidate screening and submission-package system for a single non-technical recruiter.

**The contract that defines this project:**

> The language model produces evidence. Deterministic Python produces the score, the band, and the recommendation.

A model is asked to find and quote text. It is never asked how good a candidate is. Evidence flows into two pure functions, `resolve_criterion` and `aggregate`, which are configured by a YAML rubric and produce an auditable derivation trace. Every claim about a candidate is anchored to a verbatim span at a character offset in a source document, and that span is mechanically verified to exist.

If a change would weaken that contract, it is wrong regardless of how much cleaner the code becomes.

## 2. Non-negotiable rules

These twelve rules are absolute. Violating one is a defect even if all tests pass.

1. **No LLM output schema may contain a score, rating, ranking, band, percentage, verdict, or hiring recommendation.** Enforced by `tests/unit/test_schemas_forbid_scores.py`. Do not weaken this test.
2. **No LLM output may contain, or be prompted to produce, a forbidden attribute**: age, gender, nationality, ethnicity, religion, marital status, family status, disability, personality, culture fit, appearance.
3. **Evidence in states `supported` or `contradicted` must carry a verbatim span with provenance.** Absence of evidence yields `insufficient_evidence`, never a guess.
4. **Only span-validated evidence reaches the rule engine.** Invalid spans are excluded from scoring, persisted, and surfaced to the reviewer.
5. **`domain/` imports nothing from `infrastructure/`, `application/`, `app/`, or `eval/`, and no network or database library.** Enforced by `tests/architecture/test_dependency_direction.py`. Do not add exceptions to that test.
6. **No agent framework.** No LangGraph, CrewAI, AutoGen, agents SDK, or equivalent. See `ARCHITECTURE.md` section 3 and ADR-001.
7. **Nothing reaches `DELIVERED` except from `APPROVED` or `DELIVERY_PENDING_RETRY`**, and `APPROVED` requires a persisted `ReviewDecision`. Delivery adapters re-assert this independently.
8. **No retry is unbounded.** Every retry path has a finite `max_attempts`. Maximum calls per LLM call site is 3: initial, repair, escalated.
9. **No price literal appears anywhere outside `config/pricing.yaml` and its tests.** When pricing is unconfigured, cost fields are `null` and the UI says "not configured". Never zero. Never a guess.
10. **No real candidate PII in the repository.** `data/samples/` is synthetic only. Real pilot data lives at `PILOT_DATA_DIR`, outside the repo.
11. **No credential, key, token, or endpoint secret in source.** `.env.example` only, with empty values.
12. **No measured claim without the measurement.** Do not write a number into a docstring, a README, an eval report, or a comment unless a run produced it. If instrumentation is needed and does not exist, build the instrumentation and say the number is not yet measured.

## 3. Architecture rules

### Layers and dependency direction

```
app/  eval/   ->  application/ pipeline/  ->  domain/
                                    ^
                          infrastructure/ (implements domain/ports, wired by factory)
```

| Layer | May import | Must never import |
|---|---|---|
| `domain/` | stdlib, pydantic, `domain.*` | everything else, including `sqlite3`, `httpx`, any provider SDK |
| `application/`, `pipeline/` | `domain.*`, stdlib, pydantic | concrete `infrastructure.*` classes, `app.*`, `eval.*` |
| `infrastructure/` | `domain.ports`, `domain.contracts`, stdlib, SDKs | `application.*`, `pipeline.*`, `app.*`, `eval.*` |
| `app/` | `application.use_cases`, `domain.contracts`, `infrastructure.factory` | `pipeline.*`, concrete `infrastructure.*`, `domain.rules` internals |
| `eval/` | `application.*`, `domain.*`, `infrastructure.factory` | `app.*` |

`infrastructure/factory.py` is the only module permitted to construct concrete adapters. Everything else receives protocol-typed handles on the `Deps` dataclass.

### The pipeline

Eleven nodes, static order, defined once in `pipeline/registry.py`:

```
CONFIG  INTAKE  EXTRACT  SANITIZE  STRUCTURE  CALIBRATE
ASSESS  AGGREGATE  COMPOSE  REVIEW  DELIVER
```

Every node has the signature `(RunState, Deps) -> NodeResult`. A node never raises to the runner; it returns `NodeStatus.FAILED` with an `ErrorRecord`. A node never performs I/O except through `Deps`. A node never commits; the runner commits after each node, which is what makes runs resumable.

Do not add a node without an ADR. Do not reorder nodes. Do not make node execution conditional on model output.

### The five LLM call sites

Declared in `infrastructure/models/registry.py`. There are exactly five and no more without an ADR:

| Call site | Purpose |
|---|---|
| `structure.profile` | free-form prose to structured employment history |
| `assess.criterion` | per-criterion evidence with verbatim spans |
| `compose.questions` | phrase gaps as sendable questions |
| `sanitize.classify` | second opinion on ambiguous injection findings; may only raise severity |
| `calibrate.embed` | embedding of an anonymised profile summary |

There is no `score`, `rank`, `judge`, `decide`, `recommend`, or `compare` call site, and there never will be.

## 4. Repository rules

- Contracts are frozen. Changing one requires: the model change, `make schemas` regenerated and committed in the same commit, every affected test updated, and a line in `docs/DECISIONS/`. Never change a contract silently.
- Committed JSON Schema files in `contracts/schemas/` must match the models. CI diffs them.
- Behaviour that a recruiter might reasonably want to change belongs in `rubrics/`, `config/`, or `prompts/`, never in Python.
- Prompts live in `prompts/**.md` with complete YAML front matter. Do not inline a prompt string in Python.
- A new third-party dependency requires a one-line justification in the change description and must not duplicate something the stdlib does adequately.

## 5. Coding standards

- Python 3.11+. `ruff` for lint and format, `mypy --strict` on `domain/` and `application/`, standard mode elsewhere. `make check` must be green before a phase is reported complete.
- Type hints on every function signature. `Protocol` for ports, never ABCs.
- Pydantic v2 with `ConfigDict(frozen=True, extra="forbid")` on every contract.
- Enums are `str, Enum`.
- No mutable default arguments. No module-level mutable state except explicit caches with documented invalidation.
- Exceptions come from the taxonomy in `domain/errors.py`. Never raise bare `Exception`. Never catch bare `Exception` except at the runner boundary, where it is logged with the node name and converted to `NodeStatus.FAILED`.
- Functions in `domain/rules/` and `domain/provenance/` are pure: no I/O, no clock, no randomness. Time and randomness enter through `Deps.clock` and an explicit seed.
- Comments explain why, not what. A comment restating the code is noise; a comment recording a non-obvious constraint is valuable.

## 6. Test expectations

Nothing is complete without tests, and the tests must run offline.

- The full suite runs with `MODEL_PROVIDER=fake`, no network, no API key, in under two minutes.
- Unit tests for every rule-engine branch, every contract validator, every span-validation edge case, every routing trigger.
- Integration tests for every adapter, against its fake.
- Workflow tests through the real runner with fake infrastructure.
- Adversarial tests: every detector needs a true positive **and** a true negative. A detector that fires on everything is worse than no detector.
- Property tests for: offset-map round-trip, rule-engine monotonicity, blocker dominance, coverage dominance, and state-machine reachability.
- Failure tests: timeout, 429, 500, malformed JSON, schema-invalid JSON, empty response, missing usage metadata, corrupt file, unavailable integration.
- When a bug is fixed, the failing test is added first, in the same commit.

## 7. LLM usage rules

- Every call goes through `ModelClient.structured_generate` with a Pydantic response schema. No free-text completions anywhere.
- Three validation layers, all required: provider structured output, Pydantic with `extra="forbid"`, then domain validation (span existence, criterion membership, forbidden-attribute scan).
- One repair retry at the same tier, then at most one escalation. Never a loop.
- Candidate document text is a `PromptBlock` of kind `DOCUMENT`, placed only in the user role, inside a nonce-delimited region. Placing it in the system prompt raises `UntrustedBlockPlacement`. Never construct a prompt by string concatenation of untrusted text.
- Temperature 0 for `structure.profile` and `assess.criterion`. Changing this requires an eval run showing the effect.
- Model identities live only in `config/models.yaml`. **Use `TIER_CHEAP` and `TIER_STRONG` in all code, logs, labels, comments, and documentation.** Never write a provider model name into source, prose, a chart label, or a report.

## 8. Security rules

- Candidate documents are untrusted input, always, at every stage.
- The system reports instructions found in documents; it never follows them.
- Sanitisation flags and wraps; it does not delete. Deleting would hide content from the reviewer and break span validation.
- `sanitize.classify` may only raise a severity, never lower one.
- Detected injection at HIGH severity halts before any assessment call, so an adversarial document costs nothing.
- Metadata never enters a prompt.
- PyMuPDF is opened with JavaScript and embedded-file execution disabled. Extracted text is never evaluated as code, never used as a path, never interpolated into a shell command.
- Uploaded filenames are metadata only. Blobs are stored under a content hash.

## 9. Privacy rules

- `data/` is gitignored except `data/samples/`.
- The log redactor is a structlog processor, not a call-site convention. It runs on every record before either sink.
- `LOG_SPANS` defaults to false. When true, spans are truncated to 120 characters.
- `ErrorRecord.message_redacted` passes through the redactor, because provider errors echo input.
- Slack payloads carry counts and links only. Never a name, a quotation, or a score.
- Calibration cards carry no name, contact, or nationality, and are created only from reviewer-approved runs.
- `DEMO_MODE=true` forces the synthetic corpus, disables every sink and notifier, and refuses to start if a non-sample document path is configured.

## 10. Observability and cost rules

- The runner writes node records; nodes do not log their own lifecycle. A node cannot forget to be observed.
- Every LLM call writes an `llm_calls` row with real provider usage metadata. Never estimate tokens.
- `Usage` is part of `GenerationResult`, so a call cannot happen without accounting.
- Cost is `tokens x configured price`. Unconfigured price means `null`, and `null` propagates to the UI as "not configured".
- The token ceiling is enforced even when pricing is unconfigured, so the circuit breaker always works.
- Every escalation records `pre_escalation_state` and `escalation_changed_state`, so escalation value is measurable after the fact.

## 11. Forbidden shortcuts

Each of these is a specific, tempting, wrong thing. None of them is acceptable.

- Letting an LLM emit a score "just for the UI".
- Accepting an evidence item whose span could not be found, "because the claim looks right".
- Loosening a span-validation threshold to make a test pass instead of investigating why the span failed.
- Defaulting an unassessed criterion to `not_met` instead of `insufficient_evidence`.
- Adding a `try/except: pass` anywhere.
- Retrying in a `while True`.
- Writing a plausible cost, latency, accuracy, or flip-rate number into documentation.
- Sending anything externally without a persisted `ReviewDecision` with `action == APPROVE`.
- Committing a real CV, a real name, an email address, or a `.env`.
- Hardcoding an API key, even temporarily, even in a test.
- Adding a vector database, a message queue, a container orchestrator, or a microservice.
- Adding a third interface, or business logic to either of the two.
- Replacing SQLite with PostgreSQL.
- Adding an observability platform (OTel collector, tracing SaaS, LLM-ops vendor).
- Putting business logic in `app/`.
- Optimising anything before the operations page shows it is slow.
- Fabricating an evaluation result, a baseline, or a gold label.
- Weakening or deleting a test to make a build pass.
- Silently changing a contract, a state transition, or a prompt's `output_schema`.

## 12. Anti-patterns to refuse

If a request asks for one of these, say so and propose the alternative rather than complying:

| Asked for | Refuse because | Propose instead |
|---|---|---|
| A multi-agent orchestration layer | The workflow is static; ADR-001 | The existing node pipeline |
| An LLM that scores candidates | Violates the core contract | Evidence plus `aggregate()` |
| "Just cache the score" | There is no score to cache | Cache the LLM response, keyed on the prompt |
| A vector DB for the calibration corpus | Tens to hundreds of vectors | numpy over SQLite BLOBs |
| Candidate sourcing or an ATS | Explicit non-goal | Nothing |
| Auto-delivery above a confidence threshold | Confidence is self-reported and weakly calibrated | The approval gate |
| A Kubernetes manifest | Five-day single-process prototype | `make setup` |
| A dashboard of extra charts | Decorative | The six panels on the operations page |

## 13. What may change, and what must never change

**Change freely:** implementation internals behind a protocol, test coverage, prompt wording within its declared invariants (bump the version), rubric YAML values, configuration defaults, docstrings, log field additions, UI layout within `app/`.

**Change only with an ADR in `docs/DECISIONS/` and a note in the change description:** a contract field, a state transition, the node list or order, a new LLM call site, a new third-party dependency, a public protocol signature, a routing trigger, a security detector's severity mapping, the layer dependency table.

**Never change:** the twelve non-negotiable rules in section 2; `tests/architecture/test_dependency_direction.py` except to add a legitimate new module to the correct layer; `test_schemas_forbid_scores.py`; the illegal-transition list; the approval gate; the requirement that spans are validated before scoring.

## 14. How to work

For every task:

1. **Read before writing.** Inspect the existing modules the task touches. Do not recreate something that exists under a different name.
2. **Confirm the phase prerequisite.** If the previous checkpoint's acceptance criteria do not pass, say so and stop. Do not build on a red checkpoint.
3. **Stay inside the declared file list.** Touching files outside it means the task boundary was wrong; say so rather than expanding silently.
4. **Write the test with the code**, in the same change, not afterwards.
5. **Run the verification commands** given in the task, and record the actual output.
6. **Report honestly.** If a test fails, report the failure with its output. Never describe a partially working change as complete. Never delete a test to turn a build green.
7. **Update documentation when architecture changes.** A contract change updates `contracts/schemas/`. A decision updates `docs/DECISIONS/`. A new limitation updates `LIMITATIONS.md`. A new command updates `RUNBOOK.md`.
8. **Do not build what was not asked for.** No speculative abstraction, no "while I was in there" refactor, no extra endpoints, no extra config flags.

## 15. Definition of done

A change is complete only when **all** of the following hold. "The code works" is not on this list by itself.

- [ ] Implementation exists and does what the task specified, no more.
- [ ] Contracts respected; no contract changed without a regenerated schema and an ADR.
- [ ] Layer boundaries respected; `tests/architecture` passes.
- [ ] Tests exist for the happy path, every failure branch, and any adversarial case the change touches.
- [ ] `make check` is green: ruff, mypy, schema diff, PII scan, secret scan, and every offline suite.
- [ ] The full suite still runs offline with `MODEL_PROVIDER=fake`.
- [ ] Failure behaviour is defined: trigger, action, state change, log record, and what the user sees.
- [ ] Logging exists where the change touches a run, an LLM call, an error, or a reviewer action.
- [ ] Cost impact understood and instrumented if the change adds or removes an LLM call.
- [ ] Security implications addressed if the change touches document text, prompts, storage, or delivery.
- [ ] Documentation updated where the change alters architecture, commands, limits, or measured claims.
- [ ] The phase's acceptance criteria pass, and the verification output is recorded.

## 16. Quick reference

```bash
make setup           # venv, deps, the submission-desk command
make seed            # synthetic corpus, assessed offline in demo mode
make api             # the HTTP API, demo mode on, no API key; make web for the interface
make run             # the interface without demo mode, for a pilot
make doctor          # every deployment check, with an action per line
make smoke           # seed, then assert a reviewable candidate (CI)
make check           # pii, secrets, lint, format, types, the offline suite
make test            # offline test suite only
make schemas         # regenerate contracts/schemas/*.json
make rubric-lint     # validate every rubric
make eval            # dev split: CSV, JSONL, HTML, regression gate
make eval-holdout    # the held-out split, reported not gated
make eval-routing    # all_cheap, routed, all_strong
make eval-calibration # calibration off vs on
make eval-fairness   # counterfactual flip rate with the noise floor
make tune-thresholds # the span-threshold sweep
make check-pii
make check-secrets

submission-desk doctor
submission-desk process --role ai-engineer --limit 12
submission-desk deliver
submission-desk retry-deliveries
submission-desk reconcile
submission-desk purge --days 30 --dry-run
```

**When in doubt:** prefer the deterministic option, prefer the smaller change, prefer surfacing uncertainty over resolving it, and prefer saying "this is not measured" over writing a number.
