# ADR-005: Evidence first, never score first

**Status:** accepted. **Applies to:** `domain/contracts/evidence.py`, `domain/provenance/`, `pipeline/assess.py`, every prompt.

## Context

This is the decision the rest of the system exists to make true. A screening
system that returns a number cannot say whether the number came from the
document or from the model's prior, and a recruiter who disagrees with it has
nothing to disagree with.

## Decision

The model emits, per criterion, a list of evidence items: a claim, the
verbatim span, its location (document, page, normalised offsets), a
confidence, and a state that is one of *supported*, *contradicted*, or
*insufficient evidence*. Nothing else. No response schema in the system has a
field for a score, a band, or a verdict; a test walks every exported schema
and fails on one.

Every span is mechanically verified against the source document before it can
affect anything. Three ways to be valid — exact, after normalisation, or fuzzy
within a threshold that is separate for OCR text — and three ways to be
invalid: not found, found at a different offset than claimed, found in a
different document than claimed. Rejected items are kept, counted in the
hallucination metric, and shown to the reviewer in their own panel.

## Alternatives

- **Ask for a score with a justification.** Unverifiable, and the
  justification is generated after the score, so it explains nothing.
- **Ask for a score plus evidence.** The score anchors and the evidence becomes
  decoration; nobody reads the second half of a response whose first half
  already answered.
- **Paraphrased evidence.** Cannot be checked against the source. Prompts
  instruct copying, and a paraphrase that fails validation is a rejected item,
  which is the signal that a prompt has drifted.

## Trade-offs

More calls (one per criterion rather than one per candidate), higher token
cost, and a harder prompt. In exchange: every claim is checkable, hallucination
is mechanically detectable rather than a vibe, the rubric is editable without
touching a prompt, and criteria are independent so they run in parallel.

The threshold sweep exposed the limit: no similarity threshold separates a
one-character fabrication from a one-character OCR error. Fuzzy matching is
therefore one of four controls, and the operating points — 0.92 for digital
text, 0.90 for OCR — were chosen from a printed trade-off table rather than by
taste. The table is in EVALUATION.md.

## Consequences

Text is normalised once under a named profile and every offset is into that
text, with a map back to the raw bytes. A span from a calibration anchor is
rejected by document membership, which is one of the two mechanisms that stop
another candidate's history being cited. A document that *contains* the right
sentences without having earned them passes validation — it is in the
document — and that is the boundary of what evidence-first can do, named in
LIMITATIONS.md.
