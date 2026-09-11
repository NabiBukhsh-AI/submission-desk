# ADR-004: A thin model client with composable decorators, not a framework

**Status:** accepted. **Applies to:** `domain/ports/models.py`, `infrastructure/models/`.

## Context

Two model tiers, swappable by configuration; structured output validated
against a schema; one repair on a malformed response; a response cache so
evaluation runs are reproducible and demos are not re-billed; a routing policy;
a budget guard; and exact usage accounting on every call, because the cost
claim depends on it.

## Decision

A protocol with one method, `structured_generate(GenerationRequest) ->
GenerationResult`. `Usage` is part of the result type, so a call cannot happen
without accounting. Around the innermost client, decorators that each do one
thing: `RepairingClient` (exactly one schema repair, same tier, no document
resent), `ResponseCache` (keyed on everything that determines a response),
with the routing policy deciding the tier before the call and the budget guard
refusing before the call. The provider adapter is thin: it builds the payload,
hands it to an injected transport, and reads back the usage the provider
stated — never an estimate.

Tiers are named by cost class. The binding to a provider's model identifier
lives in `config/models.yaml` or the environment and nowhere else, and its hash
is on every run record.

## Alternatives

- **A provider-abstraction library.** A large dependency for roughly eighty
  lines of abstraction, with its own concepts to learn and its own version
  churn during a sprint, and a translation layer in which usage metadata can
  be lost on the way through.
- **Raw HTTP with no structure.** Loses provider-native structured output,
  which is the first of the three validation layers.
- **Retry loops on validation failure.** A model that returns malformed JSON
  twice is not fixed by asking a third time, and a loop is how a sprint
  produces a bill nobody can explain.

## Trade-offs

Adding a provider means writing a transport: a known, bounded cost. The
transport for a specific vendor is not in the repository, because its request
shape is the vendor's and a guess at it would name a vendor in code that knows
only about tiers. Until one is wired, `MODEL_PROVIDER=live` fails the doctor
and refuses at the first call with a sentence saying so.

## Consequences

`FakeModelClient` implements the same protocol and replays recorded fixtures;
a request with no fixture is answered by a deterministic stand-in that quotes
the document, which is what lets the whole suite and the demo run offline. The
stand-in marks every usage row as unmeasured. The cache is applied to the live
client only: a deterministic client gains nothing from it, and the fairness
control arm measures the system's consistency with itself, which a cache would
answer on its behalf. Five call sites are declared in one registry with their
schema and prompt; a call to an undeclared site raises.
