# ADR-007: Retrieval only for calibration, and off until it earns its place

**Status:** accepted. **Applies to:** `pipeline/calibrate.py`, `domain/calibration.py`, `infrastructure/calibration/`.

## Context

Retrieval is expected in a system like this, and expectation is not a reason.
The one purpose it can legitimately serve here is calibration: showing the
assessment step how much evidence was enough the last two or three times a
person decided a candidate for this role, so that "partial" means the same
thing this week as last week.

## Decision

Role-scoped retrieval of two or three previously *approved* decisions, offered
as anchors to the assessment prompt and to nothing else. Cosine similarity
over a few hundred vectors held in SQLite; no vector database. Off by default,
behind a flag with an ablation attached, and evaluated as a first-class
dimension of the benchmark rather than assumed to help.

Two mechanisms stop an anchor being cited as evidence: anchors are placed in a
block kind the span validator does not accept as a source, and the validator's
document-membership check rejects any span that is not from the candidate's
own documents. Only approved runs become cards, so the system cannot learn
from its own unreviewed opinions. Cards older than a staleness window are not
retrieved, because last year's decision met a different bar. A diversity step
picks the highest-scoring dissenting card by score, not by position, so the
anchors are a range rather than three copies of one view.

## Alternatives

- **Generic retrieval over CVs.** Forbidden by the design and by sense: it
  would make one candidate's history evidence about another.
- **A vector database.** Unjustifiable at tens to hundreds of vectors. An
  index that needs its own process to answer a millisecond question is a
  dependency bought with nothing.
- **Always on.** Would ship an unmeasured feature, and the ablation would then
  be measuring what turning it *off* does.

## Trade-offs

The search is linear per query, which is irrelevant below tens of thousands of
cards and unacceptable well above. A card written at delivery is built from
the state available then, and the structured profile is not persisted beyond
the run, so cards written after a review carry criterion states and a summary
that says no structured history was available. That weakens retrieval and is
one more reason the feature is off.

## Consequences

The ablation ran over the dev split and showed no difference — over an index
that was empty, because no approved decision existed to become a card. A null
result, reported as one in EVALUATION.md. The test that would catch an anchor
leaking into a prompt is guarded by a non-vacuity assertion first, because
the original version passed by never sending an anchor.

Triggers for revisiting: the ablation moving beyond the intervals; a corpus
approaching tens of thousands of cards; or cross-process writes to the index.
