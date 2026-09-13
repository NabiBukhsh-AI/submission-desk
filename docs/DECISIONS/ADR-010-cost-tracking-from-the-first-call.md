# ADR-010: Cost is accounted from the first call, and never estimated

**Status:** accepted. **Applies to:** `domain/ports/models.py`, `application/accounting.py`, `infrastructure/models/pricing.py`, `config/pricing.yaml`.

## Context

Usage caps, model routing, and per-seat cost tracking are expected inputs to
any request for an AI budget, and cost tracking bolted on later is always
incomplete: it misses the calls made while the design was being found, which
are exactly the ones that show how expensive the design is.

## Decision

`Usage` is part of the model client's return type, so a call cannot happen
without accounting. Every call writes a cost row with its tokens, tier, call
site, latency, and whether it was cached, repaired, or escalated; the run's
totals are incremented in the same place. Usage is what the provider reported.
A response that omits it is recorded as zero tokens with the omission flagged,
never filled in from a tokeniser.

Prices are configuration in `config/pricing.yaml` and ship empty. A tier is
priced only when both directions are given. A cost with no rate is `None`,
renders as "not configured", and is never zero. A test forbids price literals
anywhere in source; another asserts that an unpriced call produces no number.
The token ceiling is checked before each node and works whether or not pricing
is configured, so the circuit breaker does not depend on somebody having
entered rates.

## Alternatives

- **Estimate tokens with a tokeniser.** Wrong for cached and reasoning tokens,
  and it invites invented numbers that look measured.
- **Add cost tracking on the last day.** Would miss every call before it.
- **Ship a default price table.** Would name a vendor in a committed file and
  would go stale silently; a stale price is a wrong number that looks right.

## Trade-offs

Cost figures are absent until an operator enters rates, which looks like an
incomplete feature on first run. That is the correct appearance, and the
interface and the doctor both say why. With the offline stand-in, token
counts are size estimates and every usage row is marked unmeasured, so the
cost column is empty in both directions.

## Consequences

The routing experiment is wired to produce the cost table the decision was
made for, and it ran against the stand-in: three policies, identical figures,
because the stand-in never gives the routed policy a reason to escalate; it
has not been run against the model, and EVALUATION.md and the README say so.
What has been measured is one configuration on the model — Haiku on both
tiers, priced from the Settings page, 2.3 cents a candidate on the
benchmark, against 0.12 cents for one naive call — and every such figure
carries its n and the source of the prices that produced it.
