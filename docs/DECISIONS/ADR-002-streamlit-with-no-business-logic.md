# ADR-002: Streamlit for the interface, with no business logic in it

**Status:** accepted. **Applies to:** `app/`.

## Context

A recruiter with no technical background has to complete the whole workflow:
upload, review, correct, decide. The deliverable is a working workflow rather
than a front end, and the schedule is five days, most of which are needed for
the deterministic spine and the evaluation.

## Decision

Streamlit, with one hard rule enforced by a test: `app/` renders what a use
case returns and never computes anything. It may import use cases, contract
types, the session-state module and the factory. It may not import the rule
engine, the state machine, the pipeline, or any storage or model adapter. A
page that names a threshold or calls a transition fails the build.

Session state is one frozen dataclass under one key. Every action is a use
case call; the page shows the sentence that came back.

## Alternatives

- **A web API plus a JavaScript front end.** Two days minimum for the same
  reviewer experience, and the two days come out of the evaluation. The
  no-business-logic rule means this remains possible later: a front end would
  call the same use cases.
- **A different Python UI framework.** Weaker multi-page and session handling
  for the review flow, which is the page that matters.
- **A command line only.** Fails the requirement outright. The command line
  exists for operations, not review.

## Trade-offs

Streamlit reruns the whole script on every interaction, which makes state
awkward and long operations need care; both are contained by the single
dataclass and by reading queue status from the database rather than from the
page. Component customisation is limited, so the review page is a rendered
layout rather than a designed one. Streamlit's session proxy is not typed as
a mapping, so the state module declares the minimal protocol it needs.

## Consequences

The architecture test `tests/architecture/test_no_business_logic_in_app.py`
walks every page's imports and names. The one widening made during the build
— the command line may import the deployment checks and startup reconciliation
— is recorded in the test with its reason. A refresh loses nothing, because
nothing that matters lives in the page.

## Amendment, 2026-09-13

A second interface — React over a thin HTTP API in `app/api/` — joined the
Streamlit page and became the one a reviewer is pointed at: it has the admin
account, the roles editor, the run-again and delete actions, a phone layout,
and every word on the review page defined. The rule did not change: the API
layer is one use-case call per route, the React client renders what the API
returns and takes every sentence from `/api/vocabulary`, and the same
architecture test walks both interfaces. Streamlit stays for one person on
one machine, with no login.
