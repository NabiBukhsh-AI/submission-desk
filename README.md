# Submission Desk

Evidence-first candidate screening. A recruiter uploads a candidate's documents
and picks a role; the system finds and quotes what those documents say about
each requirement, checks every quotation against the source, turns the verified
evidence into a recommendation through a fixed set of rules it prints, and a
person decides. Nothing is sent anywhere without that decision.

> The language model produces evidence. Deterministic Python produces the score,
> the band, and the recommendation.

A model is asked to find and quote text, never to judge a candidate. Every claim
in the output is anchored to a verbatim span at a character offset in a source
document, and that span is mechanically verified to exist before it is allowed
to affect anything. No schema anywhere in the system can express a score, and a
test asserts it on every commit.

## Three steps

    make setup     # virtual environment and dependencies (Python 3.11, Node 20+)
    make seed      # four synthetic candidates, assessed offline
    make api       # the API on :8000 — then, in a second terminal, make web

Open http://localhost:5173. The first visit creates the one admin account;
the queue shows the four synthetic candidates, three ready to review and one
quarantined. No API key, no network, no account elsewhere: `make seed` runs
the whole pipeline against a deterministic stand-in for the model, and
`make api` is demo mode — only the synthetic corpus is read and nothing can be
delivered.

To assess real documents: **Settings** → Anthropic → paste a key → **Test the
connection**; then `make api-live` instead of `make api`. A CV through Claude
Haiku 4.5 costs about two cents and takes about twenty seconds. `make doctor`
says whether a deployment will work and what to fix if not.

`make check` runs the linters, the type checker, both pre-commit scanners and
the offline test suite — 2,960 tests at the time of writing, none of which
touch a network. `render.yaml` deploys the whole thing as one container on
Render (RUNBOOK section 6a); the same `Dockerfile` runs anywhere Docker does.

## What is in the box

- **An eleven-node pipeline** — CONFIG, INTAKE, EXTRACT, SANITIZE, STRUCTURE,
  CALIBRATE, ASSESS, AGGREGATE, COMPOSE, REVIEW, DELIVER — as plain functions
  over persisted state. No agent framework. Every node commits before the next
  starts, so a crash resumes from the last completed node.
- **Five declared model call sites**, each with a schema that cannot express a
  verdict, and two model tiers named by cost class rather than by vendor.
  Anthropic through its SDK, structured output, streamed; one repair, one
  escalation, a token ceiling per run.
- **Span validation** with three valid tiers (exact, normalised, fuzzy-OCR) and
  three invalid ones. Rejected quotations are shown to the reviewer in their own
  panel rather than dropped.
- **Ten deterministic injection detectors** and a quarantine state that halts a
  run before any model call, at zero spend.
- **Three roles as YAML** — AI Engineer, Full Stack Developer, Flutter Mobile
  Developer — shown and edited on the Roles page, validated whole before they
  are stored, with every run recording the hash of the rubric it used.
- **A rule engine** in two pure functions with a printed derivation, property
  tests for monotonicity, blocker dominance and coverage dominance, and an
  override path that recomputes through the same functions.
- **A reviewer interface** — React over a thin HTTP API, usable on a phone —
  that holds no business logic; an architecture test fails the build if it
  ever does. Every word on the review page is defined in place. One admin
  account, keys sealed at rest, candidates run again against another role
  from their stored documents, deletion that spares the samples.
- **An evaluation harness** with a dev/holdout split fixed before tuning, a
  regression gate, a naive one-call baseline run on the same model as the
  system, three routing policies, a calibration ablation, and a
  counterfactual fairness experiment that refuses to print a flip rate
  without its noise floor.
- **Cost accounting from the first call**: usage is part of the model client's
  return type, prices are configuration, and every cost figure reads "not
  configured" rather than zero until somebody enters rates.

## What the evaluation found

Twelve labelled cases, run on Claude Haiku 4.5 with a naive one-call baseline
beside the pipeline — same cases, same model, same prices. Every proportion
below carries its n; at these sizes the interval is the finding.

| | one naive call | the pipeline |
|---|---|---|
| band accuracy (n=3) | 100% [43.9–100] | 66.7% [20.8–93.9] |
| hallucination rate | not measurable — it offers no quotation | 0.0% [0.0–3.6] (n=103) |
| integrity-flag accuracy (n=12) | 83.3% — it answered both injected CVs | 100% — both quarantined at zero cost |
| a person sees a reason | never | on every run it could not place |
| cost · latency per candidate | $0.0012 · 1.8 s | $0.0230 · 20.1 s |

The naive call names the band more often; the pipeline's miss is a strong
candidate under the 70% coverage gate, which is a rubric setting. What the
naive call cannot do is be checked: it quotes nothing, decided on documents a
person should decide on, and wrote the candidate's name into its answer. The
first run on the model also found a defect — the repair prompt did not resend
the document — and the before and after are both reported. Not measured: a
human baseline (no recruiter session was recorded, so the productivity claim
is absent rather than favourable) and any adoption figure.
[docs/EVALUATION.md](docs/EVALUATION.md) has every table;
[LIMITATIONS.md](LIMITATIONS.md) says what else is not known.

## The six design decisions

Each is a claim with a mechanism behind it and a number, or an honest absence of
one, in [docs/EVALUATION.md](docs/EVALUATION.md).

1. **Evidence first, never score first.** The model emits evidence items with
   verbatim spans and an explicit insufficient-evidence state. Two pure
   functions in `domain/rules/` do the rest. On the model, 103 quotations
   were offered across the twelve cases and every one was found in its
   source; in an earlier run the same day, 4 of 107 were caught and
   rejected. Fabrication is also measured by a threshold sweep: at the
   shipped digital threshold, 6.2% of real quotations are rejected and 0.0%
   of fabrications accepted over 16 real and 10 fabricated spans.

2. **Counterfactual fairness, with a floor.** Four base CVs under six personas,
   differing in identity tokens and nothing else (a test asserts it). The
   measured band flip rate is 0.0% [0.0–16.1] (n=20) with blind mode off and
   the same with it on, against a self-consistency floor of 0.0% [0.0–24.2]
   (n=12). The code's own interpretation: overlapping intervals at this sample
   size distinguish nothing, and it is not evidence of fairness either.
   Against a deterministic stand-in that is the expected result; the same
   command against the model is the experiment, and has not been run.

3. **Prompt injection caught, live, at zero cost.** An instruction hidden in a
   CV is detected by deterministic scanners before any model call. The run is
   quarantined, the sentence is quoted back to the reviewer as data, and no
   token is spent. Integrity-flag accuracy is 100% [75.8–100] (n=12) on the
   model; the naive call answered both injected documents.

4. **Model routing with a cost table.** Three policies — all cheap, all strong,
   routed — over the same cases. Against the stand-in the three tie because
   nothing escalates; against the model the experiment is wired and unrun,
   so the claim that routing saves money is unsupported. What is measured is
   one configuration: Haiku on both tiers, 2.3 cents a candidate.

5. **Retrieval only for calibration, and off by default.** Two or three past
   approved decisions for the same role are offered as anchors — never citable,
   by two independent mechanisms. The ablation shows no difference over an
   index that was empty, so the feature ships off with the experiment that
   would have to move before it ships on.

6. **Overrides as the learning loop.** A reviewer correcting a criterion has
   to say why, from a taxonomy that routes to a prompt, the rubric, or a
   documented limit. `scripts/overrides_to_cases.py` turns those corrections
   into draft evaluation cases, with the band left for a person to decide.

## Where to read next

| If you want to know | Read |
|---|---|
| How it is built and why | [ARCHITECTURE.md](ARCHITECTURE.md) |
| The ten decisions, with the alternatives that lost | [docs/DECISIONS/](docs/DECISIONS/) |
| What was measured and what was not | [docs/EVALUATION.md](docs/EVALUATION.md) |
| What it gets wrong, and what it cannot do | [LIMITATIONS.md](LIMITATIONS.md) |
| How to run, operate, host, and fix it | [RUNBOOK.md](RUNBOOK.md) |
| What it defends against and what it does not | [docs/THREAT_MODEL.md](docs/THREAT_MODEL.md) |
| Using it, for the person doing the screening | [docs/RECRUITER_GUIDE.md](docs/RECRUITER_GUIDE.md), [docs/REVIEWER_GUIDE.md](docs/REVIEWER_GUIDE.md) |
| The interface's own notes | [frontend/README.md](frontend/README.md) |
| The rules the code is written to | [docs/ENGINEERING_STANDARDS.md](docs/ENGINEERING_STANDARDS.md) |

## Layout

    domain/          contracts, rules, state machine, provenance, ports. No I/O.
    application/     use cases, the runner, deps, budget, accounting.
    pipeline/        the eleven nodes, one signature each.
    infrastructure/  adapters: SQLite, blobs, extraction, security, models, integrations.
    app/             the HTTP API, the command line, and the earlier Streamlit page. No business logic.
    frontend/        the React interface over app/api. Vite, Tailwind, shadcn/ui.
    eval/            the harness, the arms, the fairness experiment, the results.
    prompts/         versioned prompt files.   rubrics/    role definitions.
    config/          limits, routing, tier bindings, pricing (empty).
    contracts/       exported JSON Schema, checked in CI against the models.
    scripts/         operational tooling.       tests/      2,960 offline tests.
    data/samples/    the only directory under data/ that may be committed.

The direction of dependencies — `domain` inward of `application` inward of
`pipeline` and `infrastructure`, with `app` and `eval` at the edge — is
enforced by a test that walks the import graph and fails the build.

## Licence

MIT.
