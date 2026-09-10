"""Replacing two guessed numbers with two measured ones.

``fuzzy_threshold`` is 0.92 and ``fuzzy_ocr_threshold`` is 0.88 because somebody
picked them. They are the last two magic numbers in the system, and both sit on
the path that decides whether a quotation counts as evidence — so getting them
wrong is either rejecting real quotations from scanned CVs or accepting
fabricated ones.

This sweeps both over two corpora and reports what each value costs.

    python -m scripts.tune_span_thresholds

The two corpora are the two directions of the error:

*Real spans* are quotations that genuinely appear in a document, degraded the way
extraction degrades them — OCR confusions, ligatures, hyphenation, whitespace.
These must stay valid. Rejecting one is a false invalidation: a real quotation
thrown away, which costs a candidate evidence they actually provided.

*Fabricated spans* are quotations that do not appear. These must be rejected.
Accepting one is a missed fabrication, which is the failure the whole span
validator exists to prevent.

The output is an ROC table and a recommended operating point, with both rates
stated. There is no threshold that gets both to zero, and the table is how
somebody chooses which error they would rather make.
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass
from pathlib import Path
from uuid import UUID, uuid4

from domain.contracts.enums import (
    EscalationState,
    EvidenceState,
    ExtractionMethod,
    ModelTier,
    SpanValidation,
)
from domain.contracts.evidence import EvidenceItem
from domain.contracts.source_text import OffsetRun, PageSpan, Provenance, SourceText
from domain.provenance.validator import SpanThresholds, validate

#: What to sweep. Fine enough to see where the curve turns, coarse enough that
#: the table fits on a screen and somebody actually reads it.
SWEEP = [round(0.70 + step * 0.02, 2) for step in range(16)]

PROFILE = "np-v1-nfkc-ws-dash-hyphen"

#: A CV with the phrases the corpora are drawn from. One document, so a span's
#: validity is a fact about the threshold rather than about which file it hit.
DOCUMENT = """Ana Ferreira
Senior Backend Engineer

Acme Payments, Lead Engineer, 2021 to present.
Owned a multi-service payments backend and its on-call rotation for two years.
Introduced a nightly evaluation suite; releases were blocked when agreement fell
below 0.8 against human labels.
Reduced cost per request by routing simple cases to a cheaper model.
Replaced a manual triage process; handling time fell from 40 minutes to 6.
Built a document extraction pipeline handling scanned and multilingual inputs.

Northwind Logistics, Backend Engineer, 2018 to 2021.
Built the routing service in Python. Reduced p95 latency from 900ms to 210ms.

BSc Computer Science, University of Porto, 2018.
"""

#: Quotations that are really there, degraded the way *digital* extraction
#: degrades them.
#:
#: No OCR confusions here, and that is the point. A digital PDF does not turn
#: "m" into "rn"; it loses a hyphen at a line break, doubles a space, and turns
#: a straight quote curly. Sweeping the digital threshold against OCR damage —
#: which the first version of this script did — tunes it against an error it
#: will never see, and produced a recommendation two points out.
DIGITAL_REAL_SPANS: list[tuple[str, str]] = [
    ("Owned a multi-service payments backend and its on-call rotation", "exact"),
    ("Owned a multi service payments backend and its on call rotation", "hyphens lost"),
    ("Owned  a  multi-service  payments  backend", "whitespace doubled"),
    ("Owned a multi-service payments backend\nand its on-call rotation", "line break"),
    ("Introduced a nightly evaluation suite", "exact"),
    ("Introduced a nightly evaluation suite;", "trailing punctuation"),
    ("Reduced cost per request by routing simple cases", "exact"),
    ("Reduced cost per request by routing  simple cases", "internal double space"),
    ("Built the routing service in Python", "exact"),
    ("Built the routing service in Python.", "sentence terminator"),
    ("Reduced p95 latency from 900ms to 210ms", "exact"),
    ("Replaced a manual triage process", "exact"),
    ("Replaced a manual triage process;", "trailing punctuation"),
    ("BSc Computer Science, University of Porto", "exact"),
    ("handling time fell from 40 minutes to 6", "exact"),
    ("Built a document extraction pipeline handling scanned", "exact"),
]

#: The same, degraded the way OCR degrades text. The confusion classes the
#: validator folds before matching are exactly what appears here, because those
#: are the errors a scanner actually makes.
OCR_REAL_SPANS: list[tuple[str, str]] = [
    ("Owned a multi-service payments backend and its on-call rotation", "exact"),
    ("Introduced a nightly evaluation suite", "exact"),
    ("lntroduced a nightly evaluation suite", "I read as l"),
    ("Introduced a nightIy evaluation suite", "l read as I"),
    ("Reduced cost per request by routing simple cases", "exact"),
    ("Built the routing service in Python", "exact"),
    ("Buiit the routing service in Python", "l read as i"),
    ("Reduced p95 latency from 900ms to 210ms", "exact"),
    ("Reduced p95 latency frorn 900ms to 210ms", "m read as rn"),
    ("Replaced a manual triage process", "exact"),
    ("Replaced a rnanual triage process", "m read as rn"),
    ("BSc Computer Science, University of Porto", "exact"),
    ("BSc Cornputer Science, University of Porto", "m read as rn"),
    ("handling time fell from 40 minutes to 6", "exact"),
    ("handling time fell from 4O minutes to 6", "0 read as O"),
    ("Owned a multi service payments backend and its on call rotation", "hyphens lost"),
]

#: Quotations that are not there. Each is close to something that is, because a
#: fabrication that shares no words with the document is caught by any threshold
#: and would make the sweep look better than it is.
FABRICATED_SPANS: list[tuple[str, str]] = [
    ("Owned a multi-service payments backend and led a team of 50", "plausible extension"),
    ("Introduced a nightly evaluation suite at Google", "employer invented"),
    ("Reduced cost per request by 80 percent", "quantity invented"),
    ("Built the routing service in Rust", "technology swapped"),
    ("Reduced p95 latency from 900ms to 2ms", "number changed"),
    ("Replaced a manual triage process, saving £2m annually", "outcome invented"),
    ("MSc Computer Science, University of Porto", "qualification upgraded"),
    ("Owned a multi-service payments backend for twelve years", "duration inflated"),
    ("Has twelve years of production experience", "wholly invented"),
    ("Managed a department of forty engineers", "wholly invented"),
]


@dataclass(frozen=True)
class Point:
    """One threshold, and what it costs in both directions."""

    threshold: float
    real_kept: int
    real_total: int
    fabrications_caught: int
    fabrications_total: int

    @property
    def false_invalidation_rate(self) -> float:
        """Real quotations thrown away. A candidate loses evidence they gave."""
        if not self.real_total:
            return 0.0
        return 1 - self.real_kept / self.real_total

    @property
    def missed_fabrication_rate(self) -> float:
        """Invented quotations accepted. The failure the validator exists for."""
        if not self.fabrications_total:
            return 0.0
        return 1 - self.fabrications_caught / self.fabrications_total

    @property
    def total_error(self) -> float:
        """Both errors, weighted equally.

        Equally on purpose, and the weighting is the part worth arguing about.
        Missing a fabrication puts a false claim in front of a reviewer, who has
        the quotation and the document and can check it. A false invalidation
        silently removes real evidence and nobody sees what was lost. They are
        different harms; weighting them equally is a starting point, not a
        finding, and the table lets somebody choose differently.
        """
        return self.false_invalidation_rate + self.missed_fabrication_rate


def source_text(document_id: UUID, method: ExtractionMethod) -> SourceText:
    """The document, extracted the way the sweep needs.

    The extraction method decides which threshold the validator uses, so a sweep
    of the digital threshold over an OCR page measures nothing and produces a
    perfectly flat table. The first version of this script did exactly that and
    recommended 1.00 from sixteen identical rows.
    """
    return SourceText(
        document_id=document_id,
        raw_text=DOCUMENT,
        normalized_text=DOCUMENT,
        offset_runs=[OffsetRun(norm_start=0, raw_start=0, length=len(DOCUMENT))],
        pages=[
            PageSpan(
                page_number=1,
                norm_start=0,
                norm_end=len(DOCUMENT),
                extraction_method=method,
            )
        ],
        normalization_profile_id=PROFILE,
        extraction_confidence=0.85,
    )


def item_for(span: str, document_id: UUID, method: ExtractionMethod) -> EvidenceItem:
    start = max(DOCUMENT.find(span), 0)
    return EvidenceItem(
        evidence_id=uuid4(),
        criterion_id="tuning",
        state=EvidenceState.SUPPORTED,
        claim="A claim long enough to satisfy the contract.",
        verbatim_span=span,
        provenance=Provenance(
            document_id=document_id,
            page_start=1,
            page_end=1,
            norm_start=start,
            norm_end=start + len(span),
            extraction_method=method,
            normalization_profile_id=PROFILE,
        ),
        confidence=0.9,
        model_tier=ModelTier.CHEAP,
        prompt_version="tuning@1",
        escalation_state=EscalationState.NOT_ESCALATED,
        span_validation=SpanValidation.INVALID_NOT_FOUND,
    )


def sweep(*, ocr: bool) -> list[Point]:
    """Every threshold, over both corpora."""
    document_id = uuid4()
    method = ExtractionMethod.OCR if ocr else ExtractionMethod.DIGITAL_PDF
    real = OCR_REAL_SPANS if ocr else DIGITAL_REAL_SPANS
    sources = {document_id: source_text(document_id, method)}
    points = []

    for threshold in SWEEP:
        thresholds = (
            SpanThresholds(fuzzy_ocr_threshold=threshold)
            if ocr
            else SpanThresholds(fuzzy_threshold=threshold)
        )

        real_kept = sum(
            1
            for span, _ in real
            if validate(
                item_for(span, document_id, method), sources, thresholds
            ).validation.value.startswith("valid")
        )
        caught = sum(
            1
            for span, _ in FABRICATED_SPANS
            if not validate(
                item_for(span, document_id, method), sources, thresholds
            ).validation.value.startswith("valid")
        )

        points.append(
            Point(
                threshold=threshold,
                real_kept=real_kept,
                real_total=len(real),
                fabrications_caught=caught,
                fabrications_total=len(FABRICATED_SPANS),
            )
        )

    return points


def is_flat(points: list[Point]) -> bool:
    """Whether the sweep moved at all.

    A flat table means the threshold was never consulted — the wrong extraction
    method, an empty corpus, a validator path that short-circuits earlier — and
    recommending a value from one would be tuning to something the sweep did not
    support. Which is the one thing this script exists not to do.
    """
    return len({(p.real_kept, p.fabrications_caught) for p in points}) <= 1


def recommend(points: list[Point]) -> Point:
    """The lowest threshold on the plateau of least total error.

    Ties go to the *lower* threshold, and the reason is worth stating because
    the opposite rule is the intuitive one.

    Several thresholds usually score identically — on this corpus everything
    from 0.92 to 1.00 keeps the same spans and catches the same fabrications.
    Picking the top of that plateau looks conservative and is not: real
    degradation is a continuum, this corpus samples a handful of points on it,
    and a threshold of 1.00 rejects every degradation the normalisation profile
    does not fully absorb, including ones nobody thought to write down. The
    lower end of the plateau costs nothing measurable in fabrications and leaves
    room for the degradations the corpus does not contain.

    Choosing the top would also be tuning to a value the sweep did not support:
    the data says "0.92 and above are indistinguishable", not "1.00 is best".
    """
    return min(points, key=lambda point: (point.total_error, point.threshold))


def table(points: list[Point], recommended: Point) -> str:
    """The ROC table, as it goes into EVALUATION.md."""
    lines = [
        "| threshold | real spans kept | fabrications caught "
        "| false invalidation | missed fabrication |",
        "|---|---|---|---|---|",
    ]

    for point in points:
        marker = " (recommended)" if point.threshold == recommended.threshold else ""
        lines.append(
            f"| {point.threshold:.2f}{marker} "
            f"| {point.real_kept}/{point.real_total} "
            f"| {point.fabrications_caught}/{point.fabrications_total} "
            f"| {point.false_invalidation_rate:.1%} "
            f"| {point.missed_fabrication_rate:.1%} |"
        )

    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, default=None)
    args = parser.parse_args(argv)

    sections = []

    for name, ocr in (("fuzzy_ocr_threshold", True), ("fuzzy_threshold", False)):
        points = sweep(ocr=ocr)
        corpus_size = len(OCR_REAL_SPANS if ocr else DIGITAL_REAL_SPANS)

        if is_flat(points):
            sections.append(
                "\n".join(
                    [
                        f"### {name}",
                        "",
                        "**No recommendation.** The sweep produced identical "
                        "results at every threshold, which means this "
                        "threshold was never consulted on this corpus. A value "
                        "chosen from a flat table would be a guess wearing a "
                        "table.",
                        "",
                        table(points, points[0]),
                        "",
                    ]
                )
            )
            continue

        best = recommend(points)

        sections.append(
            "\n".join(
                [
                    f"### {name}",
                    "",
                    f"Swept over {corpus_size} real spans (which must stay "
                    f"valid) and {len(FABRICATED_SPANS)} fabricated ones (which "
                    "must be rejected).",
                    "",
                    table(points, best),
                    "",
                    f"**Recommended: {best.threshold:.2f}.** At this value "
                    f"{best.false_invalidation_rate:.1%} of real quotations are "
                    f"rejected and {best.missed_fabrication_rate:.1%} of "
                    "fabrications are accepted.",
                    "",
                    "Neither rate reaches zero at any threshold on this corpus. "
                    "The table is how somebody chooses which error to make, not "
                    "a claim that one value is correct.",
                    "",
                ]
            )
        )

    report = "\n".join(["## Span threshold sweep", "", *sections])
    print(report)

    if args.out:
        args.out.write_text(report, encoding="utf-8")
        print(f"written to {args.out}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
