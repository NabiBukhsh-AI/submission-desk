# Evaluation

What was measured, how, and what the numbers do not say.

Every figure in this document carries the number of observations behind it. A
proportion without its denominator is the easiest way to mislead somebody
honestly, and at this scale — twelve benchmark cases, twenty fairness pairs — the
denominator is the most important part of every claim.

Nothing here is a claim about a model. The offline harness runs against a
deterministic stand-in, so these figures measure the pipeline: span validation,
the rule engine, the coverage gate, abstention, and the integrity path. Point
`MODEL_PROVIDER` at a real provider and the same harness measures the model
instead.

## Running it

    make eval             # the dev split, with the regression gate
    make eval-holdout     # the held-out split, once, at the end
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

**Cost in money.** `config/pricing.yaml` ships unpriced. Token counts are real
and come from provider usage metadata; the conversion to money is absent rather
than estimated, and every cost figure renders as "not configured" rather than as
zero.

## The regression gate

`make eval` compares against `eval/results/BASELINE` and exits non-zero if a
gated metric moved beyond its tolerance, naming the cases that changed answer.

Tolerances exist because one case in twelve moves a proportion by eight points,
and a gate that fires on noise is a gate somebody disables within a week.
`forbidden_claim_rate` has a tolerance of zero: one of those appearing is not
noise, it is the failure the case was written to catch.

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
