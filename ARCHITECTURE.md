# Architecture

How Submission Desk is built, and why it is built this way. Written for
somebody who has cloned the repository and has an hour. The ten decisions that
shaped it each have a record in [docs/DECISIONS/](docs/DECISIONS/) with the
alternatives that lost; this document is the shape they add up to.

## 1. The one sentence

> The language model produces evidence. Deterministic Python produces the
> score, the band, and the recommendation.

Everything else follows from taking that sentence literally. A model is asked,
per rubric criterion, to find and quote text from the candidate's documents and
to say when there is nothing to quote. It is never asked whether the candidate
is good. No response schema in the system has a field for a score, a band, or a
verdict, and a test asserts that over every exported schema. Two pure functions
in `domain/rules/` take validated evidence to a criterion state and criterion
states to a band, producing a derivation a recruiter can read line by line. A
person approves, rejects, or asks for more; nothing leaves the system until
they do.

Why it matters: disagreement becomes debuggable. "The model found the wrong
sentence" and "the rule weighted the right sentence wrongly" are different bugs
with different fixes, and a system that returns a number cannot tell you which
one you have.

## 2. Layers, and the test that enforces them

    domain          contracts, rules, state machine, provenance, ports. No I/O, no framework.
    application     use cases, the runner, Deps, the budget guard, cost accounting.
    pipeline        the eleven nodes. One signature: (RunState, Deps) -> NodeResult.
    infrastructure  every adapter: SQLite, blobs, extraction, security, models, integrations.
    app             Streamlit pages, the HTTP API, and the command line. Rendering and parsing only.
    eval            the harness, the arms, the fairness experiment.

Dependencies point inward. `domain` imports nothing above it; `application` and
`pipeline` import `domain`; `infrastructure` imports `domain.ports` and
`domain.contracts` and nothing from `application` except in the one file that
is the composition root; `app` may import use cases, types, and the factory,
and may not import the rule engine, the state machine or the pipeline. `eval`
may assemble the system it measures.

`tests/architecture/test_dependency_direction.py` walks the import graph with
the AST and fails the build on a violation. Each layer's rule carries the
reason it exists, in the test, so that the person who hits it reads why before
deciding whether to widen it. Over the build the rule was widened twice, both
times with a written justification in the test file, and the other dozen
violations were fixed by moving code to where it belonged or introducing a
port.

A second architecture test, `test_no_business_logic_in_app.py`, asserts that no
Streamlit page imports the rule engine, calls a transition, or names a
threshold. The interface renders what a use case returns.

## 3. The pipeline

Eleven nodes, in a static tuple in `pipeline/registry.py`:

    CONFIG     resolve the rubric, the prompts and the tier bindings; hash them into the run
    INTAKE     accept or refuse each document; hash bytes; set the content key
    EXTRACT    text with offsets, by digital layer or OCR, with a confidence
    SANITIZE   ten deterministic detectors; quarantine halts the run here
    STRUCTURE  a career profile with provenance, or "partial"
    CALIBRATE  optionally retrieve past decisions as anchors (off by default)
    ASSESS     one evidence call per criterion, span-validated, routed by tier
    AGGREGATE  criterion states to a band, with the derivation
    COMPOSE    the scorecard, interview questions and information requests
    REVIEW     stage the run for a person, with the reasons it needs one
    DELIVER    send the approved package; refuse everything else

No agent framework. The graph is static, so a framework's central feature would
go unused, and the things a framework would give — retries, checkpoints,
parallelism — are a few dozen lines here and are testable in isolation. The
runner (`application/runner.py`) is short enough to read in one sitting: for
each node, check the budget, execute, commit the result in one transaction,
advance the state machine, stop if told to.

**Every node commits before the next starts.** The event row, the error record
if any, the integrity tier if it changed, and the status transition are one
SQLite transaction. A crash resumes from the last completed node, and there is
no separate resume path that could drift from the ordinary one: resumption is
the runner skipping nodes already marked complete.

**The reviewer gate is a missing call.** `process_candidate` runs the pipeline
with `stop_after="REVIEW"`. DELIVER is reached only by `deliver_approved`,
which the queue and the command line call after a decision exists. The state
machine independently permits `DELIVERED` only from `APPROVED` or
`DELIVERY_PENDING_RETRY`, and DELIVER itself re-checks the decision table
rather than the status column. Three controls; each would have to fail.

## 4. The state machine

Twenty statuses, in `domain/state_machine.py`, with an explicit allowed-
transition table. Four properties are tested rather than assumed:

- `DELIVERED` is reachable only through `APPROVED`, over every path.
- `QUARANTINED` reaches a decision only through `NEEDS_REVIEW`, so the
  reviewer always sees the integrity banner first.
- Terminal means terminal: `REJECTED`, `DELIVERED` and `FAILED_TERMINAL` have
  no onward transitions, and a new run is how somebody tries again.
- A node may propose a next status, but the runner ignores any proposal the
  table forbids and uses the node's declared success status instead, so a node
  cannot invent a transition.

Optimistic concurrency on the run row: a reviewer's write carries the version
their page was rendered from, and a stale write is refused with a sentence
telling them to reload, rather than silently overwriting a colleague's decision.

## 5. Contracts

Every value that crosses a boundary is a Pydantic v2 model with
`frozen=True, extra="forbid"`, in `domain/contracts/`. The JSON Schema for each
is exported to `contracts/schemas/` and checked in; `make check` fails if the
committed schema and the model disagree. The interesting ones:

- **EvidenceItem** — a claim, a verbatim span, provenance (document, page,
  normalised offsets), a confidence, and a state that is one of supported,
  contradicted, or insufficient evidence. A supported or contradicted item
  must carry a span; the validator refuses one that does not.
- **CriterionAssessment** and **Recommendation** — what the rule engine
  produced, with the rule id that produced each state and a derivation that
  is a finite list of sentences.
- **ReviewDecision** — who, what, the overrides with their reason codes, how
  long it took, and the trust rating. The only thing that authorises delivery.
- **RunRecord** — the run's configuration hashes (rubric, prompts, tier
  bindings, routing policy, pipeline version), its totals, its status, and the
  nodes it completed. Enough to say what produced any result and to reproduce
  it.
- **GoldLabel** and **EvaluationCase** — what a person decided before the
  system ran, including which criteria the document does *not* answer.

A test walks every exported schema and fails if any field is named or typed
like a score.

## 6. Evidence and provenance

`domain/provenance/` is the part of the system that makes the one sentence
true rather than aspirational.

Text is normalised once per document under a named profile
(`np-v1-nfkc-ws-dash-hyphen`), and every offset in the system is an offset into
that normalised text, with a run-length map back to the raw text. Changing the
profile changes its id, and a cached extraction under an old id is never served
against a new one.

Every span the model returns is looked for. Three ways it can be valid — exact,
after normalisation, or fuzzy within a threshold that is separate for OCR text
because scanners make characteristic mistakes that are not fabrications — and
three ways it can be invalid: not found, found at a different offset than
claimed, or found in a different document than claimed. The thresholds were
chosen by sweeping them over real and fabricated spans and printing the trade-
off; the table is in [docs/EVALUATION.md](docs/EVALUATION.md), and the
interesting result is that no threshold separates a one-character fabrication
from a one-character OCR error, which is why fuzzy matching is one control of
four and not the control.

An invalid span does not lower the band. It routes the run to a person, is
counted in the hallucination metric, and is shown in its own panel on the
review page — the system catching itself, visibly. Downgrading a band for an
operational failure would corrupt the quality metrics with noise.

## 7. The rule engine

Two pure functions, configured by the rubric YAML, with property tests that are
the formal statement of the architectural claim.

**resolve** turns one criterion's evidence into a state. Six rows: met, partial,
not met, contradicted, insufficient, and unassessed. The two rows that carry the
argument: a contradiction (`R-CONFLICT`) never resolves itself — it scores
nothing and routes to a person, because a CV and a cover letter disagreeing
about years of experience is where a human is cheap and a model is dangerous.
And insufficient evidence is not "not met": absence of evidence is not evidence
of absence, and collapsing the two is the most common failure of a naive
screener.

**aggregate** turns states into a band in seven fixed steps, each appending a
derivation line. The score divides by *resolved* weight, never total weight, so
a candidate is not punished for the system's failure to find something. A
coverage gate refuses to produce a band at all below a rubric-set fraction of
assessable weight, and says what to ask for instead. A blocker that is not met
dominates; a blocker that is unknown routes to a person. The human-required
overlay — an invalid span, a partial profile, a budget cap — never moves the
band.

Three Hypothesis properties: more evidence never lowers a band (monotonicity);
a failed blocker produces a decline whatever else is true (blocker dominance);
and the coverage gate fires whatever the resolved states are (coverage
dominance). Overrides recompute through the same two functions, so there is
exactly one scoring implementation in the repository.

## 8. Models

Five call sites, each declared in `infrastructure/models/registry.py` with its
purpose, its tier, its schema, and its prompt: `structure.profile`,
`assess.criterion`, `compose.questions`, `sanitize.classify`, and
`calibrate.embed`. A sixth exists only in the evaluation package for arm B, the
naive single-call comparison, and is deliberately not in the system's registry.
A call to an undeclared site raises.

Two tiers, `tier_cheap` and `tier_strong`, named by cost class. The binding to
a provider's model identifier lives in `config/models.yaml` or the environment
and nowhere else: not in code, not in logs, not in a chart label, not in this
document. Swapping a model is an edit in one place, and the binding hash is on
every run record so a swap is visible in the evaluation rather than silently
changing results.

The client is a protocol with one method, `structured_generate`, wrapped by
decorators that each do one thing: one schema repair at the same tier, never
more; a response cache keyed on everything that determines a response; a
routing policy that decides the tier before the call; and a budget guard that
refuses before spending. `Usage` is part of the return type, so a call cannot
happen without accounting.

Three routing policies over the same interface — all cheap, all strong, and
routed. The routed policy escalates on mechanical evidence (an invalid span)
before self-reported confidence, starts high-stakes criteria strong, and caps
escalations per candidate and per budget. The experiment that would justify it
is wired and has run; the cost column reads *not measured* because no provider
is priced.

The offline client replays recorded fixtures and, for a request with no
fixture, hands over to a deterministic stand-in that quotes the document or
says it cannot. That is what lets `make seed` reach a reviewable candidate
with no key, and it is not a model: every figure it produces is a claim about
the pipeline, and every usage row it writes is marked unmeasured.

## 9. Security

Detailed in [docs/THREAT_MODEL.md](docs/THREAT_MODEL.md). The shape:

Five independent controls against instruction injection, of which three hold
if detection fails entirely. Candidate text enters a prompt only inside a
fenced region keyed to a per-run nonce the document has never seen, under a
system sentence that names it as data. Ten deterministic detectors run before
any model call — imperative phrasing addressed to the reader, role tokens,
hidden colour, hidden size, off-page text, render difference, zero-width
characters, homoglyphs, metadata instructions, and repetition — and a high-
confidence finding quarantines the run at zero spend. The response schema
cannot express compliance: there is no field an injected instruction could
fill. Every span is validated against the source. And a person reads the
result before anything is sent.

Blind mode removes identity tokens before the model sees the text. A word-
boundary matcher for protected attributes runs over every claim the model
produces, in both the assessment and the composition nodes, and drops the
claim rather than the run.

The log redactor is the first processor in the structlog chain, so no sink can
be reached without it. Identifiers are removed; whole span fields are dropped
unless spans are switched on, and then truncated. Secrets are never logged by
key name. Two pre-commit scanners — one for candidate contact details, one for
credentials and `.env` files — run in the hook and again in `make check`.

## 10. Review

Two interfaces, one rule. Streamlit was the first, chosen because the
deliverable is a working workflow rather than a front end, with a hard rule
that `app/` holds no business logic. The second is a thin HTTP API
(`app/api/main.py`, one use case call per route) and a React client under
`frontend/` that renders what it returns. The words both interfaces show —
chips, bands, states, reasons, detector names — come from one module,
`app/vocabulary.py`, served to the client as `/api/vocabulary`, so neither
can invent a gentler banner. The same architecture test walks both.

The review page is ordered by the questions a recruiter asks: can I trust these
documents (the integrity banner); what does it recommend and why (the band and
the derivation, in sentences); what did it read (evidence per criterion, with
click-through to the source); what did it throw away (the rejected-span panel);
what should I ask next. A reviewer who disagrees changes a criterion state, not
a score, and says why from a taxonomy whose values each route somewhere — a
prompt, the rubric, or a documented limit. The recommendation is recomputed
through the rule engine before they commit.

Decisions are attributed to a configured reviewer id, never to a name typed in
a box. Elapsed time and a trust rating are recorded on every decision because
they are the two numbers a productivity claim would need.

## 11. Calibration

Role-scoped retrieval of two or three previously approved decisions, offered to
the assessment prompt as anchors for how much evidence was enough last time.
Cosine similarity over a few hundred vectors in SQLite; no vector database,
because a recruiter's history is hundreds of decisions and an index that needs
its own process to answer a millisecond question is a dependency bought with
nothing.

Two mechanisms prevent an anchor being cited: anchors are placed in a block
kind the span validator does not accept as a source, and the validator's
document-membership check rejects any span that is not from the candidate's own
documents. A test that could pass vacuously (an empty profile, zero similarity,
no anchor ever reaching the model) is guarded by a non-vacuity assertion first.

Off by default, because it plausibly helps and has not been shown to. The
ablation ran and showed nothing, over an index that was empty; the feature
ships off with the experiment attached.

## 12. Storage and recovery

SQLite in WAL mode with foreign keys on, behind repository protocols in
`domain/ports/repositories.py`, plus a content-addressed blob store whose only
accepted address is sixty-four hex characters, which is what makes path
traversal structurally impossible rather than unlikely. Five migrations,
applied at startup; the doctor refuses a database ahead of the code.

One writer. That is the ceiling and it is named in ADR-003 with the triggers
that would change it. Migration to another store is a second implementation of
the protocols and one line in the factory, verified by running the same suite
against both.

Runs that stall are reconciled to `INTERRUPTED` at startup and resume from the
last committed node. Retention is a use case, `purge`, that removes finished
runs older than a period, their rows by cascade, and their bytes unless
another run still refers to them; dry run by default.

## 13. Observability and cost

Structured logs through one processor chain, to a JSONL file and to SQLite so
the operations page is a query. Every run record carries its token totals, its
call count, its retries, repairs, escalations, validation failures and
invalid-span count, and every model call writes a cost row.

Prices are configuration and ship empty. A cost with no rate is `None`, renders
as "not configured", and is never zero — a test forbids price literals in
source and another asserts that an unpriced call does not produce a number.
The token ceiling works as a circuit breaker whether or not pricing is set.

`submission-desk doctor` is the deployment's observability: database,
migrations in both directions, prompts, rubrics, provider wiring, pricing, OCR,
the document source, disk, the reviewer id, demo mode, and the presence — never
the value — of each credential.

## 14. Evaluation

Three arms: A, a recruiter's own timed decisions (not recorded, so reported as
absent); B, one naive call that asks for a band and a justification; C, the
system. Twelve cases split eight/four into dev and holdout before any tuning,
most of them asserting abstention rather than a band because any system can be
graded on a strong candidate. A regression gate compares each dev run to a
pinned baseline and names the cases that changed answer.

Every proportion carries its n and a Wilson interval, and the report raises
rather than prints a figure that has no denominator. The fairness harness
refuses to render a flip rate without a self-consistency floor from the same
configuration, because a disparity figure with nothing beside it reads as a
finding whatever caveat is printed next to it. The full method and every
number, including the ones that are absent, are in
[docs/EVALUATION.md](docs/EVALUATION.md).

## 15. Demo mode

`DEMO_MODE=true` is enforced at the composition root, before any adapter
exists: a configured pilot path, a remote source, or an inbox outside
`data/samples/` refuses to start; the document source is the synthetic corpus
and nothing else; there are no sinks and no notifier — absent, not disabled —
so nothing in the process holds a handle that could send. DELIVER says so
rather than reporting a failure, and the retry path refuses rather than marking
a run delivered that was never sent. The upload page is switched off. This is
what `make demo` runs.

## 16. Reading the code

Start with `application/runner.py` for the control flow, then
`pipeline/registry.py` for the nodes in order, then `domain/rules/resolve.py`
and `aggregate.py` for the decision, then `infrastructure/factory.py` for what
a run actually talks to. `tests/workflow/test_end_to_end.py` drives a real
document through everything and is the best single file for seeing the shape.

Module docstrings say why the module exists and which alternative lost. Tests
are named for the behaviour they assert, and the ones that caught a real bug
say so in their docstring, with the bug.

## 17. When to revisit

Each decision record lists its triggers. The ones most likely to fire:

- A second concurrent reviewer or a hosted deployment — the storage decision.
- A real provider configured and priced — the routing claim becomes measurable,
  and the cache should be checked against the fairness control arm.
- Calibration showing a difference beyond the intervals — it ships on.
- A rule shape the six-row table cannot express — a code change and an ADR,
  not a rules interpreter.
- Any of the four triggers in ADR-001 for a dynamic graph — none has fired.
