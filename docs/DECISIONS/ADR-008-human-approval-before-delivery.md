# ADR-008: A person approves before anything is delivered

**Status:** accepted. **Applies to:** `domain/state_machine.py`, `application/use_cases/submit_review.py`, `application/use_cases/deliver_approved.py`, `pipeline/deliver.py`.

## Context

The system makes claims about people's careers, and external delivery is
irreversible. The question is not whether to automate the decision but where
exactly the boundary sits and how many independent things have to fail for it
to be crossed.

## Decision

Three controls, each sufficient on its own.

1. **The runner stops.** `process_candidate` runs the pipeline with
   `stop_after="REVIEW"`. DELIVER is reached only by `deliver_approved`, a use
   case that runs after a decision exists. Approval and sending are two
   steps, so a decision can be looked at once more before anything leaves.
2. **The state machine forbids it.** `DELIVERED` is reachable only from
   `APPROVED` or `DELIVERY_PENDING_RETRY`, and `APPROVED` only from a review
   state. A property test asserts this over every path through the table, not
   just the tested ones. A node may propose a transition; the runner ignores a
   proposal the table forbids.
3. **DELIVER checks the decision table, not the status.** A status is a column
   any writer can update; a decision is a row that `submit_review` had to
   create, attributed to a configured reviewer. An `APPROVED` run with no row
   behind it is refused, even when the node is called directly.

`submit_review` writes the run before the decision, so a decision row never
exists for a run that refused the transition. Optimistic concurrency refuses a
stale write rather than letting the last one win.

## Alternatives

- **Auto-deliver above a confidence threshold.** The confidence is self-
  reported and weakly calibrated; a threshold on it would be theatre.
- **Auto-deliver clear declines.** The highest-stakes automation with the
  least oversight, and precisely where a fabricated span or an injected
  instruction does the most damage.
- **A single gate in the interface.** One control, in the layer least able to
  guarantee anything.

## Trade-offs

Throughput is capped by reviewer time. That is the correct cap for this
product: its job is to make each review faster and better-founded, not to
remove it. Approval not being the same as sending is one more step for a
reviewer, and the guide says so.

## Consequences

A quarantined run reaches a decision only through review, so the integrity
banner is always seen. In demo mode there are no sinks at all — absent, not
disabled — and DELIVER says so rather than reporting a failure, while the retry
path refuses rather than marking a run delivered that was never sent. Every
decision carries the reviewer id, the elapsed time, and a trust rating,
because those are the numbers a productivity claim would need and they cannot
be backfilled.
