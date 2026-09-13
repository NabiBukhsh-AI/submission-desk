# Evaluation

What was measured, how, and what the numbers do not say.

Every figure in this document carries the number of observations behind it. A
proportion without its denominator is the easiest way to mislead somebody
honestly, and at this scale — twelve benchmark cases, twenty fairness pairs — the
denominator is the most important part of every claim.

Two kinds of figure, kept apart. The offline harness runs against a
deterministic stand-in, so its figures measure the pipeline — span validation,
the rule engine, the coverage gate, abstention, the integrity path — and are
what the regression gate protects. The comparison in the section "Against a
real model" runs the same cases on Claude Haiku 4.5, beside a naive one-call
baseline, and is the only place a number is a claim about a model.

## Running it

    make eval             # the dev split, with the regression gate (stand-in)
    make eval-holdout     # the held-out split, once, at the end (stand-in)
    make eval-baseline    # the naive one-call baseline against the pipeline, on the real model
    make eval-routing     # three routing policies over the same cases
    make eval-calibration # with and without calibration
    make eval-fairness    # counterfactual pairs, with the noise floor

## What the benchmark is

Twelve cases, eight in `dev/` and four in `holdout/`, split before any tuning.
Prompts are tuned against dev only; the gap between the two splits is the honest
estimate of how much dev performance was overfitting.

The cases are chosen so that most of them test something a band-accuracy figure
would hide: a sparse CV that invites invention, a document that contradicts
itself, an injected one, a keyword-stuffed one, and a strong candidate for a
different job. More cases assert *abstention* than assert a band, because any
system can be graded on whether it agreed about a strong candidate.

Cases deliberately leave fields null where they assert nothing, and the metrics
compute only over asserted fields. A case testing abstention has no opinion about
the band, and scoring it on one would punish the benchmark for being precise.

## What is not measured

**A human baseline.** `eval/gold/manual_baseline.csv` is not committed, because
its contents would be real people's judgements about real applications and
inventing twelve of them would fabricate the one number a reader has least
ability to check. Arm A is reported as *not measured*, and the comparison against
human judgement is therefore absent rather than favourable.

**Whether the system is fair.** See the fairness section: what is measured is a
flip rate against a noise floor, which is a different and much smaller claim.

**Cost in money.** `config/pricing.yaml` ships unpriced. With a real provider,
token counts come from its usage metadata; the conversion to money is absent
rather than estimated, and every cost figure renders as "not configured" rather
than as zero. With the stand-in, token counts are size estimates and every usage
row is marked unmeasured, so the cost column is empty in both directions.

**Any model's judgement — in the sections below this one.** Every run in
"Measured results" used the deterministic stand-in and measures the pipeline.
The section that follows is the same twelve cases against a real model, and
against the naive one-call baseline, and it is the section to read first.

## Against a real model: the naive baseline and the final system

`make eval-baseline` (`eval/experiments/baseline.py`) runs the twelve cases
twice on the same model: **arm B**, one call with the whole document and the
whole rubric that returns a band — what somebody reaches for when told to
"just use the LLM" — and **arm C**, the pipeline. Claude Haiku 4.5 on both
tiers, priced at $1 / $5 per million tokens. Run on 2026-09-13, twice: before
and after one fix the first run exposed.

### What the first run found

Two of nine criteria on the strong dev candidate and the blocker on the strong
holdout candidate came back *unassessed* with the reason `repair_failed`. The
repair prompt showed the model its error and not the document (ADR-004: "the
model has already read it"). That is true of a conversation and false of an
API call — each call is fresh — so a model asked to add the quotation it left
out answered *"no document provided"*. Both strong candidates fell under the
coverage gate and the pipeline reported `insufficient_information` where the
gold label said `advance`. The repair now resends the document
(`docs/DECISIONS/ADR-004`, amendment). Before and after,
`eval/results/comparison/2026-09-13-comparison-before-repair-fix.md` and
`-after-repair-fix.md`:

| metric | B: one naive call | C before the fix | C after the fix |
|---|---|---|---|
| band accuracy | 100% [43.9–100] (n=3) | 33.3% [6.1–79.2] (n=3) | 66.7% [20.8–93.9] (n=3) |
| abstention accuracy | 100% by construction (n=14) | 85.7% [60.1–96.0] (n=14) | 85.7% [60.1–96.0] (n=14) |
| hallucination rate | not measurable | 3.7% [1.5–9.2] (n=107) | 0.0% [0.0–3.6] (n=103) |
| forbidden-claim rate | 0.0% (n=7) | 0.0% (n=7) | 0.0% (n=7) |
| integrity-flag accuracy | 83.3% [55.2–95.3] (n=12) | 100% [75.8–100] (n=12) | 100% [75.8–100] (n=12) |
| criteria lost to repair | — | 3 | 0 |
| cost per candidate | $0.0012 | $0.0233 | $0.0230 |
| latency per candidate | 1.8 s | 21.8 s | 20.1 s |
| a person sees a reason | never: one band, one sentence | 12 of 12 | 11 of 12 (one advanced cleanly) |

### How to read it

**The naive call wins on band accuracy, and that is the honest result.** On
the three banded cases it named the band the labeller named; the pipeline
got two of three after the fix. The remaining miss, **hold-101**, is a strong
candidate who describes the work without the rubric's vocabulary. The model
found evidence for four of nine criteria (59% of the rubric by weight), the
70% coverage gate held, and the system said it did not know enough. The naive
call said *advance* because nothing required it to show 70% of anything.
Whether that gate is right for one-page CVs is a rubric setting — it is
editable per role on the Roles page — not a model question.

**What the naive call cannot do is the reason the pipeline exists.** It
offers no quotation, so nothing it says can be checked: its hallucination
rate is not low, it is *unmeasurable*. It answered both injected documents
instead of refusing them (integrity 83%); the pipeline quarantined both before
any model call, at zero cost. It gave *decline* to a CV that contradicts
itself and to a capable person applying for the wrong job, with one sentence
of justification each; the pipeline abstained on both and routed them to a
person with the reason on the run. Its abstention accuracy is 100% only
because it abstains on everything at the criterion level — it has no
criterion level.

**Hallucination moved between runs on the same model.** 4 of 107 quotations
rejected in the first run, all on the keyword-stuffed CV (dev-008: the model
stitched two bullets into one "quotation"); 0 of 103 in the second. Haiku is
not deterministic across runs at temperature 0, and n≈100 is where a 4%
rate is one document's worth of noise. Both figures are reported; neither is
the rate.

**Cost and time.** Twenty times the cost of the naive call — 2.3 cents
against 0.12 — and eleven times the latency, for per-criterion evidence a
person can click through to the page. Both numbers came from the provider's
usage metadata and the entered prices, not from an estimate. A hundred
candidates a week is about $2.30.

## The regression gate

`make eval` compares against `eval/results/BASELINE` and exits non-zero if a
gated metric moved beyond its tolerance, naming the cases that changed answer.

Tolerances exist because one case in twelve moves a proportion by eight points,
and a gate that fires on noise is a gate somebody disables within a week.
`forbidden_claim_rate` has a tolerance of zero: one of those appearing is not
noise, it is the failure the case was written to catch.

## The gold labels

A gold label is what a person decided about a case *before* the system was run
on it, written in the case file next to the document. Each carries:

- `criterion_states` — the state each named criterion should resolve to. Only
  criteria the labeller had an opinion about are listed.
- `expected_band` — the band, or null when the case tests something other than
  the band (abstention, quarantine, a contradiction a person has to settle).
- `must_be_insufficient` — criteria the document does *not* answer. This is the
  half of a label most benchmarks omit, and it is what lets an invented answer
  be scored as wrong rather than lucky.
- `must_flag_integrity` — whether the document should be quarantined.
- `forbidden_claims` — phrases that must not appear in any claim.

One labeller wrote all twelve. Agreement figures therefore inherit one person's
reading of the rubric; a second labeller is the highest-value addition to this
benchmark and has not been made. The ambiguity notes on each case say where the
reading was a judgement call.

## Measured results

Every figure is from a file under `eval/results/`, named beside it. Every
proportion carries its n and a Wilson 95% interval. At these sizes the
intervals are the finding: a point estimate on two cases is not a number, it
is a coin.

### Dev split, arm C

`eval/results/dev-2026-09-11-summary.json`. Eight cases, deterministic
stand-in, blind mode on, calibration off. Identical to the pinned baseline.

| metric | value | n |
|---|---|---|
| band accuracy | 50.0% [9.5–90.5] | 2 |
| band within one | 50.0% [9.5–90.5] | 2 |
| criterion accuracy | 60.0% [31.3–83.2] | 10 |
| abstention accuracy | 72.7% [43.4–90.3] | 11 |
| hallucination rate | 0.0% [0.0–5.7] | 63 |
| forbidden-claim rate | 0.0% [0.0–43.4] | 5 |
| integrity-flag accuracy | 100.0% [67.6–100.0] | 8 |
| escalation rate | 0.0% [0.0–5.7] | 63 |
| cost per candidate | not configured | 0 |
| Cohen's kappa against gold (criteria) | 0.33 | 10 |

What the rows mean, case by case (`dev-2026-09-11.jsonl`):

- **dev-001**, the strong candidate, came out `manual_review_required` against
  an expected `advance`. This is the stand-in's documented limitation: it
  matches on shared words and does not stem, so "eligible to work" does not
  match a criterion asking about "eligibility", the work-authorisation blocker
  resolves as unanswered, and the rule engine — correctly, given that input —
  refuses to advance a candidate whose blocker is unresolved. The band is wrong
  and the reason is visible in the derivation. That is the system behaving as
  designed on bad evidence, and it is the single case behind the 50%.
- **dev-002**, the sparse CV, abstained as expected. This is the other half of
  the band figure.
- **dev-004** was quarantined before any model call, at zero tokens, which is
  the integrity row.
- **dev-003, 005, 006, 007** abstained. Each asserts abstention rather than a
  band, and each is scored on whether it abstained on the right criteria.
- **dev-008**, the keyword-stuffed CV, reached `manual_review_required`: the
  stand-in found the rubric's own words and quoted them, span validation
  accepted them because they are genuinely in the document, and the coverage
  and blocker rules stopped it short of a band. This is the case that shows
  where the deterministic controls end: a document that *contains* the right
  sentences is indistinguishable, to a quoting system, from one that *earns*
  them. A person has to read it, and the system says so.

**Hallucination rate.** Sixty-three quotations were offered across the eight
cases and every one was found in its source document. With the stand-in that
is expected — it quotes literally — so the row establishes that the validator
does not reject true quotations, not that a model would never invent one. The
threshold sweep below is where fabrication is measured.

### Holdout split, arm C — run once

`eval/results/holdout-2026-09-11-summary.json`. Four cases the dev split had
never seen. Run once, on 2026-09-11, after every threshold and prompt was
fixed. Not run again.

| metric | value | n |
|---|---|---|
| band accuracy | 0.0% [0.0–79.3] | 1 |
| criterion accuracy | 40.0% [11.8–76.9] | 5 |
| abstention accuracy | 66.7% [20.8–93.9] | 3 |
| hallucination rate | 0.0% [0.0–12.5] | 27 |
| forbidden-claim rate | 0.0% [0.0–65.8] | 2 |
| integrity-flag accuracy | 100.0% [51.0–100.0] | 4 |
| Cohen's kappa against gold (criteria) | 0.00 | 5 |

The one banded case, **hold-101**, is a strong candidate who describes the
work without using the rubric's vocabulary. The stand-in found nothing to quote
and the system abstained. Against a real model this is the case that measures
paraphrase-to-evidence reading; against the stand-in it measures nothing except
that the stand-in is literal, which was already known. **hold-103**, an
injection shaped differently from the dev one, was quarantined at zero cost.
**hold-102** and **hold-104** abstained, as their labels ask.

The dev-to-holdout gap on criterion accuracy (60% to 40%, both intervals wide
enough to contain each other) is reported as observed. With n=10 and n=5 it is
not evidence of overfitting; it is not evidence of its absence either.

### Routing policies

`eval/results/routing/`. The same eight cases under `all_cheap`, `routed` and
`all_strong`. All three produced identical figures (band 50.0% n=2, abstention
72.7% n=11, hallucination 0.0% n=63, escalations 0.0% n=63) and no cost, for a
reason worth stating rather than tabulating: the stand-in never returns low
confidence and never produces an invalid span, so the routed policy never had
a reason to escalate, and the three policies executed the same calls. The
experiment is wired and runs; the cost claim it exists to test is **not
measured** until a real provider is configured and priced.

### Calibration

`eval/results/calibration/`. Off and on, over the same eight cases: band
50.0% (n=2), criterion 60.0% (n=10), abstention 72.7% (n=11) in both arms. A
null result, reported as one. It is also a weak one: the index was empty at
the start of the run, because no approved decision existed to become an
anchor, so "on" retrieved nothing. Calibration ships off, and this is the
experiment that would have to move before it ships on.

### Counterfactual fairness

`eval/results/fairness/fairness.txt`. Twenty-four variants from four base CVs
and six personas, twenty pairs per arm against the documented-unmarked
reference, and a control of twelve pairs from running each base three times.

| arm | band changed | criterion changed |
|---|---|---|
| control (identity fixed) | 0.0% [0.0–24.2] (n=12) | 0.0% [0.0–3.4] (n=108) |
| blind mode off | 0.0% [0.0–16.1] (n=20) | 0.0% [0.0–2.1] (n=180) |
| blind mode on | 0.0% [0.0–16.1] (n=20) | 0.0% [0.0–2.1] (n=180) |

Zero flips in every arm, including the control — which is what a deterministic
stand-in produces, and which says nothing about a sampling model. The report's
own interpretation line reads: *the measured rate and the self-consistency
floor overlap at this sample size, so this does not distinguish a disparity
from run-to-run variation. It is not evidence of fairness either.* That
sentence is generated by the code, not written here, and it will print
whatever the numbers are.

What the run does establish: the pairs differ only in identity tokens (a test
asserts it), the pipeline is deterministic under identity substitution, and the
harness refuses to print a flip rate without its floor. Against a real model,
the same command measures the thing this section is named after.

## Span thresholds

Two numbers that used to be guesses.

## Span threshold sweep

### fuzzy_ocr_threshold

Swept over 16 real spans (which must stay valid) and 10 fabricated ones (which must be rejected).

| threshold | real spans kept | fabrications caught | false invalidation | missed fabrication |
|---|---|---|---|---|
| 0.70 | 16/16 | 2/10 | 0.0% | 80.0% |
| 0.72 | 16/16 | 3/10 | 0.0% | 70.0% |
| 0.74 | 16/16 | 3/10 | 0.0% | 70.0% |
| 0.76 | 16/16 | 4/10 | 0.0% | 60.0% |
| 0.78 | 16/16 | 4/10 | 0.0% | 60.0% |
| 0.80 | 16/16 | 5/10 | 0.0% | 50.0% |
| 0.82 | 16/16 | 5/10 | 0.0% | 50.0% |
| 0.84 | 16/16 | 7/10 | 0.0% | 30.0% |
| 0.86 | 16/16 | 7/10 | 0.0% | 30.0% |
| 0.88 | 16/16 | 7/10 | 0.0% | 30.0% |
| 0.90 (recommended) | 15/16 | 8/10 | 6.2% | 20.0% |
| 0.92 | 12/16 | 9/10 | 25.0% | 10.0% |
| 0.94 | 11/16 | 9/10 | 31.2% | 10.0% |
| 0.96 | 11/16 | 9/10 | 31.2% | 10.0% |
| 0.98 | 8/16 | 10/10 | 50.0% | 0.0% |
| 1.00 | 8/16 | 10/10 | 50.0% | 0.0% |

**Recommended: 0.90.** At this value 6.2% of real quotations are rejected and 20.0% of fabrications are accepted.

Neither rate reaches zero at any threshold on this corpus. The table is how somebody chooses which error to make, not a claim that one value is correct.

### fuzzy_threshold

Swept over 16 real spans (which must stay valid) and 10 fabricated ones (which must be rejected).

| threshold | real spans kept | fabrications caught | false invalidation | missed fabrication |
|---|---|---|---|---|
| 0.70 | 16/16 | 2/10 | 0.0% | 80.0% |
| 0.72 | 16/16 | 3/10 | 0.0% | 70.0% |
| 0.74 | 16/16 | 4/10 | 0.0% | 60.0% |
| 0.76 | 16/16 | 4/10 | 0.0% | 60.0% |
| 0.78 | 16/16 | 5/10 | 0.0% | 50.0% |
| 0.80 | 16/16 | 6/10 | 0.0% | 40.0% |
| 0.82 | 16/16 | 6/10 | 0.0% | 40.0% |
| 0.84 | 16/16 | 7/10 | 0.0% | 30.0% |
| 0.86 | 16/16 | 7/10 | 0.0% | 30.0% |
| 0.88 | 16/16 | 8/10 | 0.0% | 20.0% |
| 0.90 | 15/16 | 8/10 | 6.2% | 20.0% |
| 0.92 (recommended) | 15/16 | 10/10 | 6.2% | 0.0% |
| 0.94 | 15/16 | 10/10 | 6.2% | 0.0% |
| 0.96 | 15/16 | 10/10 | 6.2% | 0.0% |
| 0.98 | 15/16 | 10/10 | 6.2% | 0.0% |
| 1.00 | 15/16 | 10/10 | 6.2% | 0.0% |

**Recommended: 0.92.** At this value 6.2% of real quotations are rejected and 0.0% of fabrications are accepted.

Neither rate reaches zero at any threshold on this corpus. The table is how somebody chooses which error to make, not a claim that one value is correct.

### What the sweep does not settle

The missed-fabrication rate does not reach zero at any usable threshold. That is
a real limit of fuzzy matching, not a tuning failure: a fabrication that changes
"BSc" to "MSc" differs from the truth by one character, and no similarity
threshold separates it from an OCR misread of the same size.

This is why fuzzy matching is one of four controls rather than the control. The
response schema cannot express a hiring recommendation; the reviewer sees every
rejected quotation in its own panel; and nothing is sent without a decision. The
threshold's job is to catch what it can cheaply, and the other three catch what
it cannot.

## Counterfactual fairness

`make eval-fairness` runs four base CVs under six personas, differing in exactly
the identity tokens — name, pronoun, email — and in nothing else. A test asserts
that: strip the identity words from two variants and the remaining token
multiset must be identical, or the comparison is measuring two things at once.

Three arms: blind mode off, blind mode on, and a control that runs each base CV
three times with identity held fixed.

**The control is not optional.** A flip rate without a self-consistency floor is
uninterpretable, and the code enforces that rather than relying on discipline:
`FlipRate.render()` raises if asked to print without one. If the floor is 8% and
the measured rate is 9%, the honest reading is "we cannot distinguish this from
noise at this sample size" — not "a 9% disparity".

**The claim is never that the system is fair.** It is: across four base CVs and
six personas, the band changed in *k* of *n* comparisons, against a
self-consistency floor of *m* of *j*, on this corpus and this configuration. A
reader can decide what that is worth. Fairness is not a property a benchmark of
twenty pairs can establish, and asserting it would be the most damaging sentence
in this repository.

At twenty pairs, only a large effect would be visible. A result inside the noise
is not evidence of fairness, and the report says so in those words rather than
leaving it to a footnote.
