# ADR-006: Two pure functions turn evidence into a recommendation

**Status:** accepted. **Applies to:** `domain/rules/`, `application/use_cases/recompute_recommendation.py`.

## Context

Somebody has to turn evidence into a band. Whoever does it decides whether the
system is reproducible, auditable, and arguable with.

## Decision

Two pure functions, configured by the rubric YAML. `resolve` takes one
criterion's validated evidence to a state through a six-row table: met,
partial, not met, contradicted, insufficient, unassessed. `aggregate` takes the
states to a band in seven fixed steps, each appending a sentence to a
derivation. Overrides recompute through the same two functions, so there is
one scoring implementation in the repository.

Two rows carry the argument. A contradiction never resolves itself: it scores
nothing and routes to a person, because a CV and a cover letter disagreeing is
where a human is cheap and a model is dangerous. And insufficient evidence is
not "not met": absence of evidence is not evidence of absence, and collapsing
the two is the most common failure of a naive screener.

Two aggregation choices make it defensible rather than merely deterministic.
The score divides by *resolved* weight, never total weight, so a candidate is
not punished for the system's failure to find something; the gap surfaces as
coverage, and the coverage gate refuses to produce a band at all below the
rubric's threshold. And the human-required overlay — an invalid span, a
partial profile, a budget cap — never moves the band. It routes the run to a
person and is displayed; the band still says what the evidence said.

## Alternatives

- **Let the model aggregate.** Unreproducible, unauditable, and it recreates
  the problem the architecture exists to solve.
- **A learned aggregator.** No labelled data on day one, and less explainable
  than the thing it replaced.
- **A rules interpreter with its own expression language.** More expressive,
  and nobody can test it. Rule shapes beyond the table need a code change and
  a decision record; that boundary is deliberate.

## Trade-offs

Expressiveness is limited to what the table and the seven steps encode. A
rubric that wanted, say, "two of these three criteria" as a compound rule
cannot express it today.

## Consequences

Three Hypothesis properties are the formal statement of the claim: more
evidence never lowers a band; a failed blocker dominates whatever else is
true; the coverage gate fires whatever the resolved states are. The derivation
is a finite list of sentences, rendered on the review page as the answer to
"why". Every rule has an id, every state names the rule that produced it, and
the explanations live in a YAML file beside the code so the wording can change
without the logic.
