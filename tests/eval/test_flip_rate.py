"""The flip rate, and the thing it will not do.

Hand-computed fixtures for all three metrics, and — the reason this module
exists — a test that a flip rate asked to render without its noise floor raises
rather than printing.

That is not a nicety. A disparity figure with nothing to compare it against
reads as a finding whatever caveat is printed beside it, and the caveat is the
first thing dropped when somebody quotes the number in a slide.
"""

from __future__ import annotations

import pytest

from domain.contracts.enums import Band, CriterionState
from eval.fairness.flip_rate import (
    Comparison,
    FlipRate,
    NoiseFloorMissing,
    compute,
    render_comparison,
    self_consistency,
)
from eval.metrics import Proportion

MET = CriterionState.MET
NOT_MET = CriterionState.NOT_MET
UNKNOWN = CriterionState.INSUFFICIENT_EVIDENCE


def comparison(band_a=Band.ADVANCE, band_b=Band.ADVANCE, states_a=None, states_b=None, **kwargs):
    return Comparison(
        base_id=kwargs.pop("base_id", "base"),
        persona_a="p1",
        persona_b="p2",
        band_a=band_a,
        band_b=band_b,
        states_a=states_a or {},
        states_b=states_b or {},
        **kwargs,
    )


def floor(band_flips: int = 0, n: int = 10) -> FlipRate:
    return FlipRate(
        label="control",
        band_flips=Proportion(band_flips, n),
        criterion_flips=Proportion(0, n * 9),
        is_control=True,
    )


# --- the refusal ---------------------------------------------------------------------


def test_rendering_without_a_floor_raises() -> None:
    """The headline. Not "should not print" — cannot."""
    rate = compute([comparison()], label="blind off")

    with pytest.raises(NoiseFloorMissing):
        rate.render()


def test_interpreting_without_a_floor_raises() -> None:
    rate = compute([comparison()], label="blind off")

    with pytest.raises(NoiseFloorMissing):
        rate.interpretation()


def test_the_message_says_what_is_missing() -> None:
    """Somebody hitting this needs to know they have to run the control arm,
    not that something crashed."""
    rate = compute([comparison()], label="blind off")

    with pytest.raises(NoiseFloorMissing, match="self-consistency floor"):
        rate.render()


def test_rendering_with_a_floor_works() -> None:
    rate = compute([comparison()], label="blind off", noise_floor=floor())

    assert "blind off" in rate.render()
    assert "noise floor" in rate.render()


def test_the_control_is_its_own_floor() -> None:
    """A control has nothing to be read against — it is what everything else is
    read against — and that is a different state from having no floor."""
    control = compute([comparison()], label="control", is_control=True)

    assert control.has_floor
    assert "This is the floor" in control.render()


def test_a_comparison_table_always_shows_the_floor() -> None:
    rendered = render_comparison(
        [floor(), compute([comparison()], label="blind off", noise_floor=floor())]
    )

    assert "noise floor" in rendered
    assert "does not measure" in rendered


def test_the_table_refuses_to_claim_fairness() -> None:
    """The sentence that must never appear."""
    rendered = render_comparison([floor()])

    assert "the system is fair" not in rendered.lower().replace("whether the system is\nfair", "")


# --- band flips, by hand ----------------------------------------------------------------


def test_band_flips_over_pairs() -> None:
    """Four pairs, one flip. 1/4 = 25%."""
    comparisons = [
        comparison(Band.ADVANCE, Band.ADVANCE),
        comparison(Band.ADVANCE, Band.HOLD),
        comparison(Band.HOLD, Band.HOLD),
        comparison(Band.DECLINE, Band.DECLINE),
    ]

    rate = compute(comparisons, label="test", noise_floor=floor())

    assert rate.band_flips.n == 4
    assert rate.band_flips.successes == 1
    assert rate.band_flips.value == pytest.approx(0.25)


def test_no_flips_is_zero_not_unmeasured() -> None:
    """Zero out of four is a measurement; zero out of zero is not."""
    rate = compute([comparison()] * 4, label="test", noise_floor=floor())

    assert rate.band_flips.value == 0.0
    assert rate.band_flips.measured is True


def test_no_comparisons_is_unmeasured() -> None:
    rate = compute([], label="test", noise_floor=floor())

    assert rate.band_flips.measured is False
    assert "not measured" in rate.band_flips.render()


def test_the_denominator_is_pairs_not_runs() -> None:
    """Counting runs would roughly double every denominator and make each
    interval look tighter than it is."""
    rate = compute([comparison()] * 6, label="test", noise_floor=floor())

    assert rate.band_flips.n == 6


# --- criterion flips, by hand ---------------------------------------------------------------


def test_criterion_flips_over_criteria() -> None:
    """Two pairs, three criteria each. One criterion differs in the first pair
    and none in the second: 1/6."""
    comparisons = [
        comparison(
            states_a={"a": MET, "b": MET, "c": UNKNOWN},
            states_b={"a": MET, "b": NOT_MET, "c": UNKNOWN},
        ),
        comparison(
            states_a={"a": MET, "b": MET, "c": UNKNOWN},
            states_b={"a": MET, "b": MET, "c": UNKNOWN},
        ),
    ]

    rate = compute(comparisons, label="test", noise_floor=floor())

    assert rate.criterion_flips.n == 6
    assert rate.criterion_flips.successes == 1
    assert rate.criterion_flips.value == pytest.approx(1 / 6)


def test_a_criterion_present_on_one_side_only_counts_as_a_flip() -> None:
    """A criterion the system answered under one identity and not the other is
    a difference, and dropping it would hide the most interesting kind."""
    pair = comparison(states_a={"a": MET}, states_b={})

    assert pair.flipped_criteria == ["a"]
    assert pair.criteria_compared == 1


def test_the_flipped_criteria_are_named() -> None:
    """A rate says something moved; the names say what to go and look at."""
    pair = comparison(
        states_a={"a": MET, "b": MET},
        states_b={"a": NOT_MET, "b": MET},
    )

    assert pair.flipped_criteria == ["a"]


# --- score deltas -------------------------------------------------------------------------------


def test_score_delta_is_signed() -> None:
    """Direction matters. A persona scoring consistently lower is a different
    finding from scores moving at random."""
    pair = comparison(score_a=0.80, score_b=0.65)

    assert pair.score_delta == pytest.approx(-0.15)


def test_an_unscored_side_produces_no_delta() -> None:
    """An unscored run is not a score of zero. Treating it as one would
    manufacture a large delta out of a coverage gate firing."""
    assert comparison(score_a=0.8, score_b=None).score_delta is None
    assert comparison(score_a=None, score_b=0.8).score_delta is None


def test_unscored_pairs_are_excluded_from_the_distribution() -> None:
    comparisons = [
        comparison(score_a=0.8, score_b=0.7),
        comparison(score_a=None, score_b=0.7),
    ]

    rate = compute(comparisons, label="test", noise_floor=floor())

    assert len(rate.score_deltas) == 1


def test_the_largest_delta_is_absolute() -> None:
    comparisons = [
        comparison(score_a=0.8, score_b=0.7),
        comparison(score_a=0.5, score_b=0.9),
    ]

    rate = compute(comparisons, label="test", noise_floor=floor())

    assert rate.largest_delta == pytest.approx(0.4)


# --- the interpretation ---------------------------------------------------------------------------


def test_an_overlapping_result_is_not_interpretable() -> None:
    """Two flips in four, floor of one in ten. The intervals overlap, so this
    does not distinguish a disparity from run-to-run variation."""
    rate = compute(
        [
            comparison(Band.ADVANCE, Band.HOLD),
            comparison(Band.ADVANCE, Band.HOLD),
            comparison(),
            comparison(),
        ],
        label="blind off",
        noise_floor=floor(band_flips=1, n=10),
    )

    assert "does not distinguish" in rate.interpretation()


def test_an_overlapping_result_is_not_called_fair() -> None:
    """The failure mode this whole module is built against."""
    rate = compute([comparison()] * 20, label="blind off", noise_floor=floor())

    assert "not evidence of fairness" in rate.interpretation()


def test_a_clear_separation_says_investigate_not_conclude() -> None:
    """Even a separated result is a reason to look, not a conclusion."""
    rate = compute(
        [comparison(Band.ADVANCE, Band.HOLD)] * 40,
        label="blind off",
        noise_floor=FlipRate(
            label="control",
            band_flips=Proportion(0, 40),
            criterion_flips=Proportion(0, 360),
            is_control=True,
        ),
    )

    assert "warrants investigation" in rate.interpretation()
    assert "conclusion" in rate.interpretation()


def test_an_empty_arm_is_not_interpretable() -> None:
    rate = compute([], label="blind off", noise_floor=floor())

    assert "Not interpretable" in rate.interpretation()


# --- the control ---------------------------------------------------------------


class _Run:
    def __init__(self, band, states=None, score=None):
        self.band = band
        self.criterion_states = states or {}
        self.score = score


def test_self_consistency_pairs_every_run_against_every_other() -> None:
    """Three runs give three pairs, which is the same pairing shape the
    disparity arms use. A floor computed differently from the thing it bounds
    does not bound it."""
    comparisons = self_consistency(
        {"base": [_Run(Band.ADVANCE), _Run(Band.ADVANCE), _Run(Band.ADVANCE)]}
    )

    assert len(comparisons) == 3


def test_a_deterministic_system_has_a_zero_floor() -> None:
    comparisons = self_consistency({"base": [_Run(Band.ADVANCE)] * 3})

    rate = compute(comparisons, label="control", is_control=True)

    assert rate.band_flips.value == 0.0


def test_a_wobbling_system_has_a_nonzero_floor() -> None:
    """The case the floor exists for: the system disagreeing with itself."""
    comparisons = self_consistency(
        {"base": [_Run(Band.ADVANCE), _Run(Band.HOLD), _Run(Band.ADVANCE)]}
    )

    rate = compute(comparisons, label="control", is_control=True)

    assert rate.band_flips.successes == 2
    assert rate.band_flips.n == 3


def test_a_single_run_produces_no_floor_pairs() -> None:
    """One run cannot disagree with itself, and reporting a floor of zero from
    it would understate the noise rather than measure it."""
    assert self_consistency({"base": [_Run(Band.ADVANCE)]}) == []
