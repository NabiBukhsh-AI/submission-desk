"""Every metric against a fixture somebody worked out by hand.

Including the kappa. A kappa nobody has checked is a number that looks like
evidence, and the formula has two places to go wrong that produce plausible
answers: the expected-agreement term, and what to do when both raters used a
single category.

The other thing tested hard here is the refusal to report a rate without a
denominator. That is the property the whole report rests on, and it is one line
of code away from being lost.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import pytest

from domain.contracts.enums import Band, CriterionState
from eval import metrics


@dataclass
class Label:
    expected_band: Band | None = None
    criterion_states: dict[str, CriterionState] = field(default_factory=dict)
    must_be_insufficient: list[str] = field(default_factory=list)
    must_flag_integrity: bool = False
    forbidden_claims: list[str] = field(default_factory=list)


@dataclass
class Result:
    case_id: str
    band: Band | None = None
    criterion_states: dict[str, CriterionState] = field(default_factory=dict)
    cost_usd: Any = None
    latency_ms: int = 0
    escalations: int = 0
    invalid_spans: int = 0
    metrics: dict[str, float] = field(default_factory=dict)


# --- the Wilson interval ------------------------------------------------------------


def test_a_perfect_score_does_not_claim_certainty() -> None:
    """12 of 12 is not 100% with no doubt. The normal approximation gives
    [1.0, 1.0], which asserts something nobody measured."""
    low, high = metrics.wilson(12, 12)

    assert high == 1.0
    assert 0.70 < low < 0.80


def test_a_perfect_score_never_exceeds_one() -> None:
    """The normal approximation produces bounds above 100% at small n, which is
    how a report ends up claiming better than perfect."""
    for n in range(1, 40):
        low, high = metrics.wilson(n, n)
        assert 0.0 <= low <= 1.0
        assert high <= 1.0


def test_a_zero_score_never_goes_below_zero() -> None:
    for n in range(1, 40):
        low, high = metrics.wilson(0, n)
        assert low >= 0.0
        assert high <= 1.0


def test_a_half_score_is_centred_near_a_half() -> None:
    low, high = metrics.wilson(6, 12)

    assert low < 0.5 < high
    assert abs((low + high) / 2 - 0.5) < 0.01


def test_a_larger_sample_gives_a_tighter_interval() -> None:
    """The property that makes the interval worth reporting at all."""
    small = metrics.wilson(6, 12)
    large = metrics.wilson(60, 120)

    assert (large[1] - large[0]) < (small[1] - small[0])


def test_no_observations_has_no_interval() -> None:
    assert metrics.wilson(0, 0) is None


def test_the_known_value_matches_the_textbook() -> None:
    """9/12 at 95%, computed by hand: centre 0.7899, half-width 0.2217."""
    low, high = metrics.wilson(9, 12)

    assert low == pytest.approx(0.4677, abs=0.001)
    assert high == pytest.approx(0.9111, abs=0.001)


# --- proportions carry their n ---------------------------------------------------------


def test_a_proportion_reports_its_denominator() -> None:
    """ "94% accurate" over seventeen cases is not a measurement."""
    rendered = metrics.Proportion(9, 12).render()

    assert "(n=12)" in rendered
    assert "75.0%" in rendered


def test_a_proportion_reports_its_interval() -> None:
    rendered = metrics.Proportion(9, 12).render()

    assert "[46.8" in rendered


def test_nothing_measured_is_not_zero_percent() -> None:
    """Zero hallucinations out of zero opportunities is not a hallucination
    rate, and printing 0% would be a claim nobody made."""
    empty = metrics.Proportion(0, 0)

    assert empty.value is None
    assert empty.interval is None
    assert empty.measured is False
    assert empty.render() == "not measured (n=0)"
    assert "0.0%" not in empty.render()


def test_a_genuine_zero_is_reported_as_zero() -> None:
    """The other direction. Zero out of sixty-three is a measurement."""
    rendered = metrics.Proportion(0, 63).render()

    assert rendered.startswith("0.0%")
    assert "(n=63)" in rendered


# --- band accuracy ------------------------------------------------------------------------


def test_band_accuracy_counts_only_asserted_cases() -> None:
    """A case that deliberately asserts no band is testing something else, and
    counting it as a miss would punish the benchmark for being precise."""
    results = [
        Result("a", band=Band.ADVANCE),
        Result("b", band=Band.HOLD),
        Result("c", band=Band.DECLINE),
    ]
    labels = {
        "a": Label(expected_band=Band.ADVANCE),
        "b": Label(expected_band=Band.ADVANCE),
        "c": Label(expected_band=None),
    }

    result = metrics.band_accuracy(results, labels)

    assert result.n == 2
    assert result.successes == 1


def test_being_one_band_out_is_counted_separately() -> None:
    """Bands are ordered, so one step out is a different error from three."""
    results = [Result("a", band=Band.ADVANCE_WITH_RESERVATIONS)]
    labels = {"a": Label(expected_band=Band.ADVANCE)}

    assert metrics.band_accuracy(results, labels).successes == 0
    assert metrics.band_within_one(results, labels).successes == 1


def test_being_three_bands_out_is_not_within_one() -> None:
    results = [Result("a", band=Band.DECLINE)]
    labels = {"a": Label(expected_band=Band.ADVANCE)}

    assert metrics.band_within_one(results, labels).successes == 0


def test_insufficient_information_is_matched_exactly() -> None:
    """It is not on the ordered scale: it is a different kind of answer, so
    "one step away" is undefined."""
    results = [Result("a", band=Band.HOLD)]
    labels = {"a": Label(expected_band=Band.INSUFFICIENT_INFORMATION)}

    assert metrics.band_within_one(results, labels).successes == 0


# --- abstention -----------------------------------------------------------------------------


def test_abstention_counts_saying_so() -> None:
    """The half of the benchmark most evaluations omit."""
    results = [
        Result(
            "a",
            criterion_states={
                "one": CriterionState.INSUFFICIENT_EVIDENCE,
                "two": CriterionState.MET,
            },
        )
    ]
    labels = {"a": Label(must_be_insufficient=["one", "two"])}

    result = metrics.abstention_accuracy(results, labels)

    assert result.n == 2
    assert result.successes == 1


def test_inventing_an_answer_scores_zero() -> None:
    """A system that answers where a person could not read one is wrong, not
    lucky."""
    results = [Result("a", criterion_states={"one": CriterionState.MET})]
    labels = {"a": Label(must_be_insufficient=["one"])}

    assert metrics.abstention_accuracy(results, labels).successes == 0


def test_not_met_is_not_abstention() -> None:
    """The distinction the whole system rests on. "Not met" is a judgement
    about a candidate; "not addressed" is one about a document."""
    results = [Result("a", criterion_states={"one": CriterionState.NOT_MET})]
    labels = {"a": Label(must_be_insufficient=["one"])}

    assert metrics.abstention_accuracy(results, labels).successes == 0


# --- hallucination -----------------------------------------------------------------------------


def test_the_hallucination_rate_is_over_opportunities() -> None:
    """Two rejected out of ten offered, computed by hand."""
    results = [
        Result("a", invalid_spans=2, metrics={"evidence_items": 10}),
    ]

    result = metrics.hallucination_rate(results)

    assert result.n == 10
    assert result.value == pytest.approx(0.2)


def test_no_evidence_offered_is_not_a_zero_rate() -> None:
    results = [Result("a", invalid_spans=0, metrics={"evidence_items": 0})]

    assert metrics.hallucination_rate(results).measured is False


# --- integrity ---------------------------------------------------------------------------------


def test_integrity_accuracy_counts_both_directions() -> None:
    """A scanner that flags everything is as useless as one that flags
    nothing, and only one of those shows up in a recall number."""
    results = [
        Result("a", metrics={"integrity_flagged": 1}),
        Result("b", metrics={"integrity_flagged": 1}),
    ]
    labels = {
        "a": Label(must_flag_integrity=True),
        "b": Label(must_flag_integrity=False),
    }

    result = metrics.integrity_flag_accuracy(results, labels)

    assert result.n == 2
    assert result.successes == 1


# --- kappa, worked by hand -------------------------------------------------------------------


def test_kappa_on_a_worked_example() -> None:
    """Ten cases, computed on paper.

        A: a a a a a a a h h h   (7 advance, 3 hold)
        B: a a a a a a h h h h   (6 advance, 4 hold)

    They agree on nine: positions 1–6 on advance and 8–10 on hold. Position 7 is
    the only disagreement.

        observed  = 9/10 = 0.90
        expected  = (7/10 · 6/10) + (3/10 · 4/10) = 0.42 + 0.12 = 0.54
        kappa     = (0.90 − 0.54) / (1 − 0.54) = 0.36 / 0.46 = 0.7826

    Worth writing the working out: the first version of this test asserted
    0.5652 because it counted eight agreements instead of nine, and the neat
    number looked convincing enough to nearly ship.
    """
    first = ["advance"] * 7 + ["hold"] * 3
    second = ["advance"] * 6 + ["hold"] * 4

    assert sum(1 for a, b in zip(first, second, strict=True) if a == b) == 9
    assert metrics.cohens_kappa(first, second) == pytest.approx(0.7826, abs=0.001)


def test_perfect_agreement_is_one() -> None:
    assert metrics.cohens_kappa(["a", "b", "a"], ["a", "b", "a"]) == 1.0


def test_complete_disagreement_is_negative() -> None:
    """Worse than chance, and the sign says so."""
    assert metrics.cohens_kappa(["a", "b"], ["b", "a"]) < 0


def test_both_raters_using_one_category_is_complete_agreement() -> None:
    """Where the usual formula divides by zero. Chance would have achieved the
    same, so kappa is undefined; complete agreement is what a reader needs."""
    assert metrics.cohens_kappa(["a", "a", "a"], ["a", "a", "a"]) == 1.0


def test_nothing_to_compare_has_no_kappa() -> None:
    assert metrics.cohens_kappa([], []) is None


def test_kappa_ignores_unanswered_pairs() -> None:
    """A case where one side has no label contributes nothing rather than
    counting as a disagreement."""
    assert metrics.cohens_kappa(["a", None, "b"], ["a", "b", "b"]) == 1.0


# --- aggregates ---------------------------------------------------------------------------------


def test_an_unpriced_run_is_dropped_rather_than_counted_as_zero() -> None:
    """Averaging an unknown cost in as zero would understate every total."""
    summary = metrics.summarise([None, 2.0, 4.0])

    assert summary.n == 2
    assert summary.mean == pytest.approx(3.0)


def test_nothing_recorded_is_not_measured() -> None:
    summary = metrics.summarise([None, None])

    assert summary.measured is False
    assert summary.mean is None


def test_the_median_is_not_the_mean() -> None:
    """Reported separately because one slow run moves an average and a reader
    cannot tell which figure they are looking at."""
    summary = metrics.summarise([1.0, 1.0, 1.0, 1.0, 100.0])

    assert summary.median == pytest.approx(1.0)
    assert summary.mean == pytest.approx(20.8)


# --- the report refuses a rate without an n -------------------------------------------


def test_the_report_refuses_a_proportion_with_no_denominator() -> None:
    """The property the whole report rests on, and one line of code away from
    being lost. Raising rather than rendering, so a template cannot produce
    "94% accurate" by omitting the part that makes it meaningful."""
    from eval.report import UnmeasuredMetric, rate

    with pytest.raises(UnmeasuredMetric):
        rate({"value": 0.94})

    with pytest.raises(UnmeasuredMetric):
        rate(None)


def test_the_report_renders_a_measured_rate_with_its_n() -> None:
    from eval.report import rate

    rendered = rate({"value": 0.75, "n": 12, "interval": (0.468, 0.911)})

    assert "75.0%" in rendered
    assert "(n=12)" in rendered
    assert "[46.8" in rendered


def test_the_report_says_not_measured_at_zero_observations() -> None:
    from eval.report import rate

    assert rate({"value": None, "n": 0}) == "not measured (n=0)"


def test_the_report_never_prints_a_zero_for_an_absent_number() -> None:
    from eval.report import number

    assert number(None) == "not measured"
    assert "0.00" not in number(None)
