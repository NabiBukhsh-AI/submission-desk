# Submission Desk

Evidence-first candidate screening. A recruiter uploads a candidate's documents,
the system finds and quotes what those documents say about each point in a role
rubric, a fixed set of rules turns the quotations into a recommendation, and a
person decides. Nothing is sent anywhere without that decision.

> The language model produces evidence. Deterministic Python produces the score,
> the band, and the recommendation.

A model is asked to find and quote text, never to judge a candidate. Every claim
in the output is anchored to a verbatim span at a character offset in a source
document, and that span is mechanically verified to exist before it is allowed
to affect anything. No schema anywhere in the system can express a score.

## Three commands

    make setup     # virtual environment and dependencies (Python 3.11)
    make seed      # generate four synthetic candidates and assess them, offline
    make demo      # open the reviewer interface

No API key, no network, no account. `make seed` runs the whole pipeline against
a deterministic stand-in for the model and leaves three candidates ready to
review and one quarantined. `make demo` opens on that queue with demo mode on:
only the synthetic corpus is read, and nothing can be delivered. `make doctor`
says whether a deployment will work and what to fix if not.

`make check` runs the linters, the type checker, both pre-commit scanners and
the offline test suite — 2,772 tests at the time of writing, none of which
touch a network.

## What is in the box

- **An eleven-node pipeline** — CONFIG, INTAKE, EXTRACT, SANITIZE, STRUCTURE,
  CALIBRATE, ASSESS, AGGREGATE, COMPOSE, REVIEW, DELIVER — as plain functions
  over persisted state. No agent framework. Every node commits before the next
  starts, so a crash resumes from the last completed node.
- **Five declared model call sites**, each with a schema that cannot express a
  verdict, and two model tiers named by cost class rather than by vendor.
- **Span validation** with three valid tiers (exact, normalised, fuzzy-OCR) and
  three invalid ones. Rejected quotations are shown to the reviewer in their own
  panel rather than dropped.
- **Ten deterministic injection detectors** and a quarantine state that halts a
  run before any model call, at zero spend.
- **A rule engine** in two pure functions with a printed derivation, property
  tests for monotonicity, blocker dominance and coverage dominance, and an
  override path that recomputes through the same functions.
- **A reviewer interface** in Streamlit that holds no business logic — an
  architecture test fails the build if it ever does.
- **An evaluation harness** with a dev/holdout split fixed before tuning, a
  regression gate, three routing policies, a calibration ablation, and a
  counterfactual fairness experiment that refuses to print a flip rate without
  its noise floor.
- **Cost accounting from the first call**: usage is part of the model client's
  return type, prices are configuration and ship empty, and every cost figure
  reads "not configured" rather than zero until somebody enters rates.

## The six design decisions

Each is a claim with a mechanism behind it and a number, or an honest absence of
one, in [EVALUATION.md](docs/EVALUATION.md).

1. **Evidence first, never score first.** The model emits evidence items with
   verbatim spans and an explicit insufficient-evidence state. Two pure
   functions in `domain/rules/` do the rest. Sixty-three quotations were offered
   across the dev split and every one was found in its source (hallucination
   rate 0.0% [0.0–5.7], n=63) — with the stand-in, which quotes literally, so
   that row establishes the validator does not reject true quotations rather
   than that a model never invents one. Fabrication is measured by the
   threshold sweep: at the shipped digital threshold, 6.2% of real quotations
   are rejected and 0.0% of fabrications accepted over 16 real and 10
   fabricated spans.

2. **Counterfactual fairness, with a floor.** Four base CVs under six personas,
   differing in identity tokens and nothing else (a test asserts it). The
   measured band flip rate is 0.0% [0.0–16.1] (n=20) with blind mode off and
   the same with it on, against a self-consistency floor of 0.0% [0.0–24.2]
   (n=12). The code's own interpretation: overlapping intervals at this sample
   size distinguish nothing, and it is not evidence of fairness either.
   Against a deterministic stand-in that is the expected result; the same
   command against a real model is the experiment.

3. **Prompt injection caught, live, at zero cost.** An instruction hidden in a
   CV is detected by deterministic scanners before any model call. The run is
   quarantined, the sentence is quoted back to the reviewer as data, and no
   token is spent. Integrity-flag accuracy on the benchmark is 100% [67.6–100]
   (n=8) on dev and 100% [51.0–100] (n=4) on the holdout.

4. **Model routing with a cost table.** Three policies — all cheap, all strong,
   routed — over the same cases. The experiment is wired and runs; the cost
   column reads *not measured* because no provider is priced and the stand-in
   never triggers an escalation. That is the honest state of the claim.

5. **Retrieval only for calibration, and off by default.** Two or three past
   approved decisions for the same role are offered as anchors — never citable,
   by two independent mechanisms. The ablation shows no difference (band 50.0%,
   n=2, in both arms) over an index that was empty, so the feature ships off
   with the experiment that would have to move before it ships on.

6. **Overrides as the learning loop.** A reviewer correcting a criterion has
   to say why, from a taxonomy that routes to a prompt, the rubric, or a
   documented limit. `scripts/overrides_to_cases.py` turns those corrections
   into draft evaluation cases, with the band left for a person to decide.

## What is not measured

A human baseline (no recruiter session was recorded, so the productivity claim
is absent rather than favourable); any model's judgement (every figure above is
from the deterministic stand-in); and cost in money. [EVALUATION.md](docs/EVALUATION.md)
says all three in more words, and [LIMITATIONS.md](LIMITATIONS.md) says what
else.

## Where to read next

| If you want to know | Read |
|---|---|
| How it is built and why | [ARCHITECTURE.md](ARCHITECTURE.md) |
| The ten decisions, with the alternatives that lost | [docs/DECISIONS/](docs/DECISIONS/) |
| What was measured and what was not | [docs/EVALUATION.md](docs/EVALUATION.md) |
| What it gets wrong, and what it cannot do | [LIMITATIONS.md](LIMITATIONS.md) |
| How to run, operate, and fix it | [RUNBOOK.md](RUNBOOK.md) |
| What it defends against and what it does not | [docs/THREAT_MODEL.md](docs/THREAT_MODEL.md) |
| The five-minute demonstration | [docs/DEMO_SCRIPT.md](docs/DEMO_SCRIPT.md) |
| Using it, for the person doing the screening | [docs/RECRUITER_GUIDE.md](docs/RECRUITER_GUIDE.md), [docs/REVIEWER_GUIDE.md](docs/REVIEWER_GUIDE.md) |
| The rules the code is written to | [docs/ENGINEERING_STANDARDS.md](docs/ENGINEERING_STANDARDS.md) |

## Layout

    domain/          contracts, rules, state machine, provenance, ports. No I/O.
    application/     use cases, the runner, deps, budget, accounting.
    pipeline/        the eleven nodes, one signature each.
    infrastructure/  adapters: SQLite, blobs, extraction, security, models, integrations.
    app/             Streamlit pages and the command line. No business logic.
    eval/            the harness, the arms, the fairness experiment.
    prompts/         versioned prompt files.   rubrics/    role definitions.
    config/          limits, routing, tier bindings, pricing (empty).
    contracts/       exported JSON Schema, checked in CI against the models.
    scripts/         operational tooling.       tests/      2,772 offline tests.
    data/samples/    the only directory under data/ that may be committed.

The direction of dependencies — `domain` inward of `application` inward of
`pipeline` and `infrastructure`, with `app` and `eval` at the edge — is
enforced by a test that walks the import graph and fails the build.

## Licence

MIT.
