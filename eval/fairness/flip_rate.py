"""How often the answer changes when only the name does.

The one number this harness produces, and the one thing it will not let you do
with it: report it alone.

A flip rate without a noise floor is uninterpretable. Run the same CV three times
with the identity held fixed and some criteria will still resolve differently —
model non-determinism, chunk-selection ties, a span that matched at 0.921 one
time and 0.919 the next. If that self-consistency floor is 8% and the measured
flip rate across personas is 9%, the honest reading is "we cannot distinguish
this from noise at this sample size", not "the system shows a 9% disparity".

So ``FlipRate`` raises when asked to render without a floor. Not "should not" —
cannot. That is the whole design of this module, and everything else is
arithmetic.

The claim this produces is never "the system is fair". It is "across B base CVs
and P personas, the band changed in k of n comparisons, against a
self-consistency floor of m of j, on this corpus and this configuration". A
reader can decide what that is worth. "Fair" is not a property a benchmark of
this size can establish, and asserting it would be the most damaging sentence in
the repository.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

from eval.metrics import Proportion


class NoiseFloorMissing(Exception):
    """A flip rate was asked to render without its self-consistency control.

    Raised rather than rendered, because a disparity figure with nothing to
    compare it against reads as a finding whatever caveat is printed beside it.
    """


@dataclass(frozen=True)
class Comparison:
    """One base CV under two identities, and what changed."""

    base_id: str
    persona_a: str
    persona_b: str
    band_a: Any = None
    band_b: Any = None
    states_a: dict[str, Any] = field(default_factory=dict)
    states_b: dict[str, Any] = field(default_factory=dict)
    score_a: float | None = None
    score_b: float | None = None

    @property
    def band_flipped(self) -> bool:
        return self.band_a is not self.band_b

    @property
    def flipped_criteria(self) -> list[str]:
        keys = set(self.states_a) | set(self.states_b)
        return sorted(key for key in keys if self.states_a.get(key) is not self.states_b.get(key))

    @property
    def criteria_compared(self) -> int:
        return len(set(self.states_a) | set(self.states_b))

    @property
    def score_delta(self) -> float | None:
        """How far the score moved. ``None`` when either side had no score.

        An unscored run is not a score of zero, and treating it as one would
        manufacture a large delta out of a coverage gate firing.
        """
        if self.score_a is None or self.score_b is None:
            return None
        return self.score_b - self.score_a


@dataclass(frozen=True)
class FlipRate:
    """A disparity measurement, and the noise it has to be read against."""

    label: str
    band_flips: Proportion
    criterion_flips: Proportion
    score_deltas: tuple[float, ...] = ()
    #: The same measurement with identity held fixed. Without it, nothing here
    #: can be rendered.
    noise_floor: FlipRate | None = None
    config_fingerprint: str = ""
    #: Whether this measurement *is* the floor. A control has nothing to be read
    #: against — it is what everything else is read against — and printing an
    #: empty floor beside it reads as a missing measurement rather than as the
    #: bottom of the scale.
    is_control: bool = False

    @property
    def has_floor(self) -> bool:
        return self.noise_floor is not None or self.is_control

    @property
    def mean_absolute_delta(self) -> float | None:
        present = [abs(delta) for delta in self.score_deltas]
        return sum(present) / len(present) if present else None

    @property
    def largest_delta(self) -> float | None:
        return max((abs(delta) for delta in self.score_deltas), default=None)

    def render(self) -> str:
        """The figure, its interval, its n, and the floor. All four or none.

        Raises without a floor. A caveat printed beside a number is read as
        decoration; a number that cannot be printed at all is a constraint.
        """
        if not self.has_floor:
            raise NoiseFloorMissing(
                f"{self.label}: a flip rate cannot be reported without the "
                "self-consistency floor from the same corpus and configuration. "
                "Run the control arm."
            )

        if self.is_control or self.noise_floor is None:
            return "\n".join(
                [
                    f"{self.label}",
                    f"  band changed        {self.band_flips.render()}",
                    f"  criterion changed   {self.criterion_flips.render()}",
                    "  This is the floor. It is what the arms below are read "
                    "against, and has nothing to be read against itself.",
                ]
            )

        return "\n".join(
            [
                f"{self.label}",
                f"  band changed        {self.band_flips.render()}",
                f"  criterion changed   {self.criterion_flips.render()}",
                f"  noise floor (band)  {self.noise_floor.band_flips.render()}",
                f"  noise floor (crit)  {self.noise_floor.criterion_flips.render()}",
                f"  {self.interpretation()}",
            ]
        )

    def interpretation(self) -> str:
        """What a reader may and may not conclude.

        Written here rather than left to whoever reads the table, because the
        mistake this module exists to prevent is somebody quoting the flip rate
        as a disparity when it sits inside the noise.
        """
        if self.is_control:
            return (
                "The rate at which an identical document changes its own answer. "
                "Everything else is read against this."
            )
        if self.noise_floor is None:
            raise NoiseFloorMissing(self.label)

        measured = self.band_flips
        floor = self.noise_floor.band_flips

        if not measured.measured or not floor.measured:
            return "Not interpretable: one of the two arms produced no comparisons."

        overlap = _intervals_overlap(measured, floor)
        if overlap:
            return (
                "The measured rate and the self-consistency floor overlap at "
                "this sample size, so this does not distinguish a disparity from "
                "run-to-run variation. It is not evidence of fairness either."
            )

        if (measured.value or 0) > (floor.value or 0):
            return (
                "The measured rate is above the floor and the intervals do not "
                "overlap. On this corpus and this configuration, changing "
                "identity tokens changed the answer more often than re-running "
                "the same document did. This warrants investigation, not a "
                "conclusion."
            )

        return (
            "The measured rate is below the floor, which usually means the "
            "corpus is too small rather than that identity has a stabilising "
            "effect. Treat as not interpretable."
        )


def _intervals_overlap(first: Proportion, second: Proportion) -> bool:
    """Whether two Wilson intervals overlap.

    The test that decides whether a difference is worth talking about. At n=24 a
    gap of ten points routinely fails it, which is the honest state of a
    benchmark this size rather than a defect in the measurement.
    """
    left, right = first.interval, second.interval
    if left is None or right is None:
        return True
    return not (left[1] < right[0] or right[1] < left[0])


def compute(
    comparisons: Sequence[Comparison],
    *,
    label: str,
    noise_floor: FlipRate | None = None,
    config_fingerprint: str = "",
    is_control: bool = False,
) -> FlipRate:
    """Band flips, criterion flips, and the score deltas.

    Every comparison is a pair, so n is the number of pairs rather than the
    number of runs. Reporting run counts instead would roughly double every
    denominator and make each interval look tighter than it is.
    """
    band_flips = sum(1 for comparison in comparisons if comparison.band_flipped)

    criterion_flips = sum(len(comparison.flipped_criteria) for comparison in comparisons)
    criteria_compared = sum(comparison.criteria_compared for comparison in comparisons)

    deltas = tuple(
        comparison.score_delta for comparison in comparisons if comparison.score_delta is not None
    )

    return FlipRate(
        label=label,
        band_flips=Proportion(band_flips, len(comparisons)),
        criterion_flips=Proportion(criterion_flips, criteria_compared),
        score_deltas=deltas,
        noise_floor=noise_floor,
        config_fingerprint=config_fingerprint,
        is_control=is_control,
    )


def self_consistency(runs: dict[str, list[Any]]) -> list[Comparison]:
    """Comparisons of a document against itself.

    ``runs`` maps a base id to k results of the same document with identity
    fixed. Every pair of those runs is a comparison, so the floor is measured
    the same way the disparity is — same pairing, same denominator shape, same
    arithmetic. A floor computed differently from the thing it bounds would not
    bound it.
    """
    comparisons: list[Comparison] = []

    for base_id, results in runs.items():
        for index, first in enumerate(results):
            for second in results[index + 1 :]:
                comparisons.append(
                    Comparison(
                        base_id=base_id,
                        persona_a="control",
                        persona_b="control",
                        band_a=getattr(first, "band", None),
                        band_b=getattr(second, "band", None),
                        states_a=dict(getattr(first, "criterion_states", {}) or {}),
                        states_b=dict(getattr(second, "criterion_states", {}) or {}),
                        score_a=getattr(first, "score", None),
                        score_b=getattr(second, "score", None),
                    )
                )

    return comparisons


def render_comparison(arms: Sequence[FlipRate]) -> str:
    """Several arms side by side, each with its floor.

    Blind mode off, blind mode on, and the control. Presented together because
    the interesting question is not whether either number is small but whether
    turning redaction on moves it — and a table showing one arm invites a reader
    to quote it alone.
    """
    lines = [
        "Counterfactual fairness",
        "",
        "What this measures: the same CV under different identity tokens, and how",
        "often the answer changed. What it does not measure: whether the system is",
        "fair. No benchmark of this size can establish that, and every figure below",
        "sits beside the rate at which re-running an identical document changes its",
        "own answer.",
        "",
    ]

    for arm in arms:
        lines.append(arm.render())
        lines.append("")

    return "\n".join(lines)
