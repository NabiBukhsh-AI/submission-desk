"""Every claim this system makes, as a number with an n beside it.

Pure functions over results and labels. Nothing here runs a pipeline, reads a
database, or calls a model, which is what lets each one be tested against a
fixture worked out by hand — including a kappa somebody actually calculated on
paper, because a kappa nobody has checked is a number that looks like evidence.

Two rules run through the whole module.

Every proportion carries its n and a Wilson interval. "94% accurate" over
seventeen cases is not a measurement, and a figure without its denominator is
the single easiest way to mislead somebody honestly. The interval is Wilson
rather than normal-approximation because at n=12 with p near 1 the normal
approximation produces bounds above 100%, which is how a report ends up claiming
something impossible.

A metric with nothing to measure returns None, never zero. Zero hallucinations
out of zero opportunities is not a hallucination rate, and printing 0% would be
a claim nobody made.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from domain.contracts.enums import Band, CriterionState

#: The confidence level every interval reports. One level, stated once, so no
#: table mixes 90% and 95% bounds in adjacent columns.
CONFIDENCE = 0.95

#: z for a two-sided 95% interval. Written out rather than computed, because
#: pulling in a statistics dependency for one constant is a poor trade and
#: because a reader can check this number.
Z = 1.959963984540054


@dataclass(frozen=True)
class Proportion:
    """A rate, its denominator, and how uncertain it is.

    The three together or none of them. A bare proportion is the most
    misleading shape a number can take in a report of this size.
    """

    successes: int
    n: int

    @property
    def value(self) -> float | None:
        """The rate, or None when there was nothing to measure."""
        return self.successes / self.n if self.n else None

    @property
    def interval(self) -> tuple[float, float] | None:
        """The Wilson 95% interval, or None when there is nothing to bound."""
        return wilson(self.successes, self.n)

    @property
    def measured(self) -> bool:
        return self.n > 0

    def render(self, places: int = 1) -> str:
        """The figure as a person should read it: rate, interval, and n.

        One function, so no template can render a rate without its denominator
        by forgetting to ask for one.
        """
        if not self.measured or self.value is None or self.interval is None:
            return "not measured (n=0)"

        low, high = self.interval
        return (
            f"{self.value * 100:.{places}f}% "
            f"[{low * 100:.{places}f}–{high * 100:.{places}f}] "
            f"(n={self.n})"
        )


def wilson(successes: int, n: int, z: float = Z) -> tuple[float, float] | None:
    """The Wilson score interval for a proportion.

    Chosen over the normal approximation because this benchmark is twelve cases.
    At n=12 with every case correct, the normal approximation gives an upper
    bound above 1.0 — a report claiming better than perfect — and a lower bound
    of exactly the point estimate, which asserts certainty nobody has.
    """
    if n <= 0:
        return None

    proportion = successes / n
    denominator = 1 + z * z / n
    centre = proportion + z * z / (2 * n)
    spread = z * math.sqrt(proportion * (1 - proportion) / n + z * z / (4 * n * n))

    return (
        max((centre - spread) / denominator, 0.0),
        min((centre + spread) / denominator, 1.0),
    )


# --- what the system got right ---------------------------------------------------


def band_accuracy(results: Sequence[Any], labels: dict[str, Any]) -> Proportion:
    """How often the band matched the recruiter's.

    Only over cases whose label asserts a band. A case that deliberately leaves
    it null is asserting something else, and counting it as a miss would punish
    the benchmark for being honest about what it tests.
    """
    successes = 0
    n = 0

    for result in results:
        label = labels.get(result.case_id)
        if label is None or label.expected_band is None:
            continue
        n += 1
        successes += int(result.band is label.expected_band)

    return Proportion(successes, n)


def band_within_one(results: Sequence[Any], labels: dict[str, Any]) -> Proportion:
    """How often the band was right or one step away.

    Reported alongside exact accuracy because the bands are ordered and the
    difference between "advance" and "advance with reservations" is not the same
    kind of error as the difference between "advance" and "decline". A single
    accuracy figure hides which one is happening.
    """
    order = [
        Band.DECLINE,
        Band.HOLD,
        Band.ADVANCE_WITH_RESERVATIONS,
        Band.ADVANCE,
    ]
    position = {band: index for index, band in enumerate(order)}

    successes = 0
    n = 0

    for result in results:
        label = labels.get(result.case_id)
        if label is None or label.expected_band is None:
            continue
        if result.band not in position or label.expected_band not in position:
            # INSUFFICIENT_INFORMATION is not on the scale: it is a different
            # kind of answer, so "one step away" is undefined and exact match is
            # the only sensible test.
            n += 1
            successes += int(result.band is label.expected_band)
            continue

        n += 1
        successes += int(abs(position[result.band] - position[label.expected_band]) <= 1)

    return Proportion(successes, n)


def criterion_accuracy(results: Sequence[Any], labels: dict[str, Any]) -> Proportion:
    """How often a criterion resolved to what the recruiter said.

    Per criterion rather than per case, so a system that gets the band right by
    getting two criteria wrong in opposite directions is visible.
    """
    successes = 0
    n = 0

    for result in results:
        label = labels.get(result.case_id)
        if label is None:
            continue
        for criterion_id, expected in label.criterion_states.items():
            n += 1
            successes += int(result.criterion_states.get(criterion_id) is expected)

    return Proportion(successes, n)


# --- what the system got honestly wrong -----------------------------------------------


def abstention_accuracy(results: Sequence[Any], labels: dict[str, Any]) -> Proportion:
    """How often the system said "I cannot tell" where the recruiter could not.

    The half of the benchmark most evaluations omit, and the one this system's
    whole argument rests on. A system that invents an answer where a person could
    not read one is wrong, not lucky, and this is the number that says so.
    """
    successes = 0
    n = 0

    for result in results:
        label = labels.get(result.case_id)
        if label is None:
            continue
        for criterion_id in label.must_be_insufficient:
            n += 1
            successes += int(
                result.criterion_states.get(criterion_id) is CriterionState.INSUFFICIENT_EVIDENCE
            )

    return Proportion(successes, n)


def hallucination_rate(results: Sequence[Any]) -> Proportion:
    """How often a quotation could not be found in its document.

    Measured mechanically rather than judged. Every evidence item is an
    opportunity, and every rejected span is a failure, so this is a rate over
    things that actually happened rather than an impression from reading a
    sample.
    """
    invalid = sum(result.invalid_spans for result in results)
    opportunities = sum(int(result.metrics.get("evidence_items", 0)) for result in results)

    return Proportion(invalid, opportunities)


def forbidden_claim_rate(results: Sequence[Any], labels: dict[str, Any]) -> Proportion:
    """How often a claim the label forbids appeared anyway.

    Cases carry the specific fabrications a document invites — a number an
    injection asks for, an inference a sparse CV tempts. This counts them.
    """
    hits = 0
    n = 0

    for result in results:
        label = labels.get(result.case_id)
        if label is None or not label.forbidden_claims:
            continue
        n += len(label.forbidden_claims)
        hits += int(result.metrics.get("forbidden_claims_present", 0))

    return Proportion(hits, n)


def integrity_flag_accuracy(results: Sequence[Any], labels: dict[str, Any]) -> Proportion:
    """How often a document needing a flag got one, and a clean one did not.

    Both directions in one figure, because a scanner that flags everything is as
    useless as one that flags nothing and only one of those is obvious from a
    recall number.
    """
    successes = 0
    n = 0

    for result in results:
        label = labels.get(result.case_id)
        if label is None:
            continue
        n += 1
        flagged = bool(result.metrics.get("integrity_flagged", 0))
        successes += int(flagged == label.must_flag_integrity)

    return Proportion(successes, n)


# --- agreement ---------------------------------------------------------------------------


def cohens_kappa(first: Sequence[Any], second: Sequence[Any]) -> float | None:
    """Agreement between two raters, corrected for chance.

    Raw agreement flatters any rater on a skewed benchmark: if eleven of twelve
    cases are "advance", answering "advance" every time scores 92% while knowing
    nothing. Kappa subtracts what chance would have achieved.

    Returns None when there is nothing to compare, and 1.0 when both raters gave
    a single identical label throughout — where the usual formula divides by
    zero, and where the honest answer is that they agreed completely.
    """
    pairs = [(a, b) for a, b in zip(first, second, strict=False) if a is not None and b is not None]
    if not pairs:
        return None

    n = len(pairs)
    observed = sum(1 for a, b in pairs if a == b) / n

    categories = {a for a, _ in pairs} | {b for _, b in pairs}
    expected = sum(
        (sum(1 for a, _ in pairs if a == category) / n)
        * (sum(1 for _, b in pairs if b == category) / n)
        for category in categories
    )

    if expected >= 1.0:
        # Both raters used one category and used the same one. Chance would have
        # achieved the same, so kappa is undefined; complete agreement is the
        # answer a reader needs, with the caveat in the report.
        return 1.0 if observed == 1.0 else 0.0

    return (observed - expected) / (1 - expected)


# --- cost and speed -------------------------------------------------------------------------


@dataclass(frozen=True)
class Aggregate:
    """A summary of a numeric column, with its n.

    Median as well as mean, because one interrupted review or one cold start
    moves an average and a reader cannot tell which figure they are looking at.
    """

    n: int = 0
    mean: float | None = None
    median: float | None = None
    p95: float | None = None
    total: float | None = None

    @property
    def measured(self) -> bool:
        return self.n > 0


def summarise(values: Sequence[float | None]) -> Aggregate:
    """Mean, median and p95 over whatever was actually recorded.

    ``None`` values are dropped rather than treated as zero. A run with no cost
    figure is a run whose cost is unknown, and averaging it in as zero would
    understate every total in the report.
    """
    present = [float(value) for value in values if value is not None]
    if not present:
        return Aggregate()

    ordered = sorted(present)
    return Aggregate(
        n=len(present),
        mean=sum(present) / len(present),
        median=_percentile(ordered, 0.5),
        p95=_percentile(ordered, 0.95),
        total=sum(present),
    )


def _percentile(ordered: list[float], fraction: float) -> float:
    index = min(round(fraction * (len(ordered) - 1)), len(ordered) - 1)
    return ordered[index]


def cost_per_candidate(results: Sequence[Any]) -> Aggregate:
    """What a candidate cost, over the runs that had a price to report.

    Unpriced runs are absent rather than zero, so this reads "not measured"
    when pricing is unconfigured instead of "free".
    """
    return summarise([result.cost_usd for result in results])


def latency_per_candidate(results: Sequence[Any]) -> Aggregate:
    return summarise([float(result.latency_ms) for result in results])


def escalation_rate(results: Sequence[Any]) -> Proportion:
    """How often a criterion was read twice."""
    escalated = sum(result.escalations for result in results)
    opportunities = sum(int(result.metrics.get("criteria_assessed", 0)) for result in results)

    return Proportion(escalated, opportunities)


# --- the whole picture ----------------------------------------------------------------------


@dataclass(frozen=True)
class ArmSummary:
    """Every headline number for one arm, each with its n."""

    arm: str
    cases: int
    band_accuracy: Proportion
    band_within_one: Proportion
    criterion_accuracy: Proportion
    abstention_accuracy: Proportion
    hallucination_rate: Proportion
    forbidden_claim_rate: Proportion
    integrity_flag_accuracy: Proportion
    escalation_rate: Proportion
    cost: Aggregate
    latency: Aggregate
    kappa_against_gold: float | None = None


def summarise_arm(arm: str, results: Sequence[Any], labels: dict[str, Any]) -> ArmSummary:
    """Everything measurable about one arm, computed once."""
    ordered = sorted(results, key=lambda result: result.case_id)
    gold_bands = [labels[r.case_id].expected_band for r in ordered if r.case_id in labels]
    arm_bands = [r.band for r in ordered if r.case_id in labels]

    return ArmSummary(
        arm=arm,
        cases=len(ordered),
        band_accuracy=band_accuracy(ordered, labels),
        band_within_one=band_within_one(ordered, labels),
        criterion_accuracy=criterion_accuracy(ordered, labels),
        abstention_accuracy=abstention_accuracy(ordered, labels),
        hallucination_rate=hallucination_rate(ordered),
        forbidden_claim_rate=forbidden_claim_rate(ordered, labels),
        integrity_flag_accuracy=integrity_flag_accuracy(ordered, labels),
        escalation_rate=escalation_rate(ordered),
        cost=cost_per_candidate(ordered),
        latency=latency_per_candidate(ordered),
        kappa_against_gold=cohens_kappa(arm_bands, gold_bands),
    )
