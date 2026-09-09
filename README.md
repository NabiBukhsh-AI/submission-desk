# Submission Desk

Evidence-first candidate screening and submission-package system.

> The language model produces evidence. Deterministic Python produces the score,
> the band, and the recommendation.

A model is asked to find and quote text, never to judge a candidate. Every claim
in the output is anchored to a verbatim span at a character offset in a source
document, and that span is mechanically verified to exist before it is allowed
to affect anything.

## Status

Under construction. This README is a stub and will be written properly at the
end of the build, when there are measured results to put in it. No number
appears here until a run has produced it.

## Setup

```bash
make setup     # virtual environment and dependencies
make check     # lint, types, and the offline test suite
```

The test suite runs offline: no network, no API key.

## Documentation

- [Engineering standards](docs/ENGINEERING_STANDARDS.md) — the evidence-first
  contract, the twelve non-negotiable rules, the layer dependency table, and the
  definition of done.
- [Decision records](docs/DECISIONS/) — why the system is shaped this way.
