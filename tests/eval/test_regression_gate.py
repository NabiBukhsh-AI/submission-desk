"""The gate fires on a real regression and stays quiet otherwise.

Both halves matter equally. A gate that never fires protects nothing; a gate
that fires on noise gets disabled within a week, and a disabled gate protects
nothing either. This benchmark is twelve cases, so one case flipping moves a
proportion by eight points — which is exactly why the tolerances exist and why
they are tested.

The failure message is tested too. "band_accuracy fell" sends somebody reading
everything; "dev-003 went from hold to decline" sends them to one file.
"""

from __future__ import annotations

import json
from pathlib import Path

from eval.runner import GateFailure, SuiteResult, check_gates, load_gates, save_baseline


def summary(**metrics: float) -> dict:
    """A summary in the shape the runner writes, with n on every proportion."""
    return {
        "summaries": {
            "c": {
                name: {"value": value, "n": 12, "successes": int(value * 12)}
                for name, value in metrics.items()
            }
        },
        "rows": [],
    }


def result(**metrics: float) -> SuiteResult:
    return SuiteResult(
        run_at="2026-03-04T10:00:00+00:00",
        split="dev",
        config_fingerprint="test",
        rows=[],
        summaries={
            "c": {
                name: {"value": value, "n": 12, "successes": int(value * 12)}
                for name, value in metrics.items()
            }
        },
    )


GATES = {
    "metrics": {
        "abstention_accuracy": {"higher_is_better": True, "tolerance": 0.05},
        "hallucination_rate": {"higher_is_better": False, "tolerance": 0.02},
        "forbidden_claim_rate": {"higher_is_better": False, "tolerance": 0.0},
    }
}


# --- it fires -----------------------------------------------------------------------


def test_a_drop_beyond_tolerance_fails() -> None:
    """The seeded regression: abstention accuracy falls by twenty points."""
    failures = check_gates(
        result(abstention_accuracy=0.55),
        summary(abstention_accuracy=0.75),
        GATES,
    )

    assert len(failures) == 1
    assert failures[0].metric == "abstention_accuracy"


def test_a_rise_in_a_lower_is_better_metric_fails() -> None:
    """Direction is per metric. A hallucination rate going up is a regression
    even though the number got bigger."""
    failures = check_gates(
        result(hallucination_rate=0.10),
        summary(hallucination_rate=0.02),
        GATES,
    )

    assert [failure.metric for failure in failures] == ["hallucination_rate"]


def test_a_forbidden_claim_appearing_fails_immediately() -> None:
    """No tolerance at all. One of these is not noise, it is the failure the
    case was written to catch."""
    failures = check_gates(
        result(forbidden_claim_rate=0.01),
        summary(forbidden_claim_rate=0.0),
        GATES,
    )

    assert failures


def test_several_regressions_are_all_reported() -> None:
    """Fixing one and rediscovering the next on the following run wastes a
    cycle each time."""
    failures = check_gates(
        result(abstention_accuracy=0.4, hallucination_rate=0.2),
        summary(abstention_accuracy=0.9, hallucination_rate=0.0),
        GATES,
    )

    assert len(failures) == 2


# --- it stays quiet ------------------------------------------------------------------


def test_no_change_passes() -> None:
    assert (
        check_gates(result(abstention_accuracy=0.75), summary(abstention_accuracy=0.75), GATES)
        == []
    )


def test_an_improvement_passes() -> None:
    assert (
        check_gates(result(abstention_accuracy=0.95), summary(abstention_accuracy=0.75), GATES)
        == []
    )


def test_a_move_within_tolerance_passes() -> None:
    """One case in twelve is eight points. A gate that fired on that is a gate
    somebody disables."""
    assert (
        check_gates(result(abstention_accuracy=0.72), summary(abstention_accuracy=0.75), GATES)
        == []
    )


def test_an_ungated_metric_is_reported_not_enforced() -> None:
    """Escalation rate and latency move for legitimate reasons. Gating a number
    nobody promised turns every improvement into an argument."""
    failures = check_gates(result(escalation_rate=0.9), summary(escalation_rate=0.1), GATES)

    assert failures == []


def test_a_first_run_has_nothing_to_compare_against() -> None:
    """Gating against a baseline that does not exist would fail every first
    run, and a check that always fails is a check nobody reads."""
    assert check_gates(result(abstention_accuracy=0.1), None, GATES) == []


def test_a_metric_missing_from_the_baseline_is_skipped() -> None:
    """A metric added since the baseline was recorded has nothing to regress
    from."""
    failures = check_gates(result(abstention_accuracy=0.1), summary(hallucination_rate=0.0), GATES)

    assert failures == []


def test_an_unmeasured_metric_is_skipped() -> None:
    """A rate with no denominator cannot have got worse."""
    current = SuiteResult(
        run_at="2026-03-04T10:00:00+00:00",
        split="dev",
        config_fingerprint="test",
        summaries={"c": {"abstention_accuracy": {"value": None, "n": 0}}},
    )

    assert check_gates(current, summary(abstention_accuracy=0.9), GATES) == []


# --- what the failure says -------------------------------------------------------------


def test_the_failure_names_the_responsible_cases() -> None:
    """The difference between a gate that reports a problem and one that starts
    an investigation."""
    baseline = summary(abstention_accuracy=0.9)
    baseline["rows"] = [
        {"case_id": "dev-003", "band": "hold"},
        {"case_id": "dev-005", "band": "advance"},
    ]

    current = result(abstention_accuracy=0.4)
    current.rows = [
        {"case_id": "dev-003", "band": "decline"},
        {"case_id": "dev-005", "band": "advance"},
    ]

    failures = check_gates(current, baseline, GATES)

    assert "dev-003" in failures[0].sentence()
    assert "dev-005" not in failures[0].sentence()


def test_the_failure_shows_both_numbers() -> None:
    """A reader deciding whether to investigate needs to know how far it
    moved."""
    sentence = GateFailure(
        metric="abstention_accuracy", before=0.9, after=0.4, tolerance=0.05
    ).sentence()

    assert "0.900" in sentence
    assert "0.400" in sentence
    assert "0.050" in sentence


def test_the_failure_reads_as_a_sentence() -> None:
    sentence = GateFailure(
        metric="hallucination_rate", before=0.0, after=0.2, tolerance=0.02
    ).sentence()

    assert sentence[0].islower() or sentence[0].isupper()
    assert sentence.rstrip().endswith(".")


# --- the shipped configuration -------------------------------------------------------------


def test_the_shipped_gates_cover_the_load_bearing_claims() -> None:
    """The claims this system makes about itself. If one of these is not gated,
    it can regress silently between releases."""
    gated = set(load_gates()["metrics"])

    assert {
        "abstention_accuracy",
        "hallucination_rate",
        "forbidden_claim_rate",
        "integrity_flag_accuracy",
    } <= gated


def test_the_forbidden_claim_gate_has_no_tolerance() -> None:
    assert load_gates()["metrics"]["forbidden_claim_rate"]["tolerance"] == 0.0


def test_every_gate_declares_its_direction() -> None:
    """A gate with no direction would fire on an improvement half the time."""
    for name, rule in load_gates()["metrics"].items():
        assert "higher_is_better" in rule, name
        assert "tolerance" in rule, name


def test_latency_is_not_gated() -> None:
    """It moves with the machine it ran on."""
    assert "latency" not in load_gates()["metrics"]


# --- the baseline round trip --------------------------------------------------------------------


def test_a_baseline_can_be_saved_and_read_back(tmp_path: Path) -> None:
    from eval.runner import load_baseline

    current = result(abstention_accuracy=0.8)
    current.rows = [{"case_id": "dev-001", "band": "advance"}]

    save_baseline(current, tmp_path)
    loaded = load_baseline(tmp_path)

    assert loaded is not None
    assert loaded["summaries"]["c"]["abstention_accuracy"]["value"] == 0.8
    assert loaded["rows"][0]["case_id"] == "dev-001"


def test_a_missing_baseline_reads_as_none(tmp_path: Path) -> None:
    from eval.runner import load_baseline

    assert load_baseline(tmp_path / "nothing") is None


def test_a_saved_baseline_is_readable_json(tmp_path: Path) -> None:
    """A baseline nobody can open is a baseline nobody will update."""
    save_baseline(result(abstention_accuracy=0.8), tmp_path)

    data = json.loads((tmp_path / "summary.json").read_text(encoding="utf-8"))

    assert data["split"] == "dev"
    assert "config_fingerprint" in data
