"""The approval gate, and what a reviewer is told before they open anything.

REVIEW is not the review. It stages a run for a person: it decides whether they
see it clean or flagged, and records why. Nothing past it happens without a
persisted decision.

That the gate is a missing call rather than a condition is the property worth
testing. There is no flag to set wrong, because the runner is never asked to go
further. The two tests at the bottom check that from the outside: DELIVER does
not execute, and the run never reaches DELIVERED.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from uuid import uuid4

import pytest

from domain.contracts.enums import Band, CriterionState, IntegrityTier, RunStatus
from domain.contracts.run_state import NodeStatus, RunState
from pipeline.registry import NODE_NAMES, node_by_name
from pipeline.review import FLAG_REASONS, node, reasons_to_flag


@dataclass
class _Recommendation:
    requires_human: bool = False
    criterion_states: dict[str, CriterionState] = field(default_factory=dict)
    band: Band = Band.ADVANCE


def state(**updates: object) -> RunState:
    base = RunState(
        run_id=uuid4(),
        candidate_id="cand-0007",
        role_id="ai-engineer",
        status=RunStatus.COMPOSED,
        started_at=datetime.now(UTC),
    )
    return base.model_copy(update={"recommendation": _Recommendation(), **updates})


# --- clean runs ---------------------------------------------------------------


def test_a_clean_run_reaches_ready_for_review() -> None:
    result = node(state(), deps=None)  # type: ignore[arg-type]

    assert result.next_status is RunStatus.READY_FOR_REVIEW
    assert result.status is NodeStatus.OK


def test_a_clean_run_carries_no_reasons() -> None:
    assert reasons_to_flag(state(), _Recommendation()) == []


# --- every reason to flag ------------------------------------------------------


@pytest.mark.parametrize(
    ("updates", "expected"),
    [
        ({"integrity_tier": IntegrityTier.SUSPECT}, "integrity"),
        ({"profile_partial": True}, "partial_profile"),
        ({"invalid_span_present": True}, "invalid_span"),
        ({"budget_capped": True}, "budget_capped"),
        ({"degraded_reasons": ("STRUCTURE",)}, "degraded"),
    ],
)
def test_each_condition_flags_the_run(updates: dict, expected: str) -> None:
    result = node(state(**updates), deps=None)  # type: ignore[arg-type]

    assert result.next_status is RunStatus.NEEDS_REVIEW
    assert expected in result.events[0].payload["reasons"]


def test_a_rule_requiring_a_person_flags_the_run() -> None:
    flagged = state(recommendation=_Recommendation(requires_human=True))

    result = node(flagged, deps=None)  # type: ignore[arg-type]

    assert result.next_status is RunStatus.NEEDS_REVIEW
    assert "requires_human" in result.events[0].payload["reasons"]


def test_a_contradicted_criterion_flags_the_run() -> None:
    """A document that disagrees with itself is where a person is cheap and a
    model is dangerous."""
    flagged = state(
        recommendation=_Recommendation(
            criterion_states={"evaluation-practice": CriterionState.CONTRADICTED}
        )
    )

    result = node(flagged, deps=None)  # type: ignore[arg-type]

    assert "contradicted" in result.events[0].payload["reasons"]


def test_several_reasons_are_all_reported() -> None:
    """A reviewer opening a flagged run needs to know which of six things
    happened. One boolean would send them looking."""
    flagged = state(
        integrity_tier=IntegrityTier.SUSPECT,
        invalid_span_present=True,
        budget_capped=True,
    )

    reasons = reasons_to_flag(flagged, flagged.recommendation)

    assert set(reasons) == {"integrity", "invalid_span", "budget_capped"}


def test_every_reason_has_a_sentence_for_the_reviewer() -> None:
    """A reason code shown in an interface is a defect. The interface renders
    these strings, so every code a node can emit must have one."""
    flagged = state(
        integrity_tier=IntegrityTier.SUSPECT,
        profile_partial=True,
        invalid_span_present=True,
        budget_capped=True,
        degraded_reasons=("STRUCTURE",),
        recommendation=_Recommendation(
            requires_human=True,
            criterion_states={"a": CriterionState.CONTRADICTED},
        ),
    )

    for reason in reasons_to_flag(flagged, flagged.recommendation):
        assert reason in FLAG_REASONS
        assert FLAG_REASONS[reason].endswith(".")


def test_the_reasons_read_as_english() -> None:
    for sentence in FLAG_REASONS.values():
        assert sentence[0].isupper()
        for jargon in ("null", "none", "true", "false", "_"):
            assert jargon not in sentence.lower()


# --- the flag does not change the answer ----------------------------------------


def test_flagging_never_changes_the_band() -> None:
    """The recommendation still says what the evidence said. The flag says look
    harder, not score lower."""
    flagged = state(
        invalid_span_present=True,
        recommendation=_Recommendation(band=Band.ADVANCE),
    )

    result = node(flagged, deps=None)  # type: ignore[arg-type]

    assert result.state.recommendation.band is Band.ADVANCE
    assert result.events[0].payload["band"] == Band.ADVANCE.value


def test_a_run_with_no_recommendation_is_flagged_rather_than_failed() -> None:
    """The reviewer can still read the documents and decide, which is more use
    than a dead run."""
    result = node(state(recommendation=None), deps=None)  # type: ignore[arg-type]

    assert result.status is NodeStatus.OK
    assert result.next_status is RunStatus.NEEDS_REVIEW
    assert "requires_human" in result.events[0].payload["reasons"]


# --- the gate itself -------------------------------------------------------------


def test_review_is_the_last_automatic_node() -> None:
    """Everything after it needs a persisted decision."""
    assert NODE_NAMES[NODE_NAMES.index("REVIEW") + 1 :] == ("DELIVER",)


def test_delivery_is_reachable_only_through_a_decision() -> None:
    """Read off the state machine rather than the node list, because the state
    machine is what actually forbids it."""
    from domain.state_machine import ALLOWED

    doors = [status for status, allowed in ALLOWED.items() if RunStatus.DELIVERED in allowed]

    assert set(doors) == {RunStatus.APPROVED, RunStatus.DELIVERY_PENDING_RETRY}


def test_neither_review_outcome_can_reach_delivered_directly() -> None:
    from domain.state_machine import ALLOWED

    for status in (RunStatus.READY_FOR_REVIEW, RunStatus.NEEDS_REVIEW):
        assert RunStatus.DELIVERED not in ALLOWED[status]


def test_the_review_node_is_wired_into_the_pipeline() -> None:
    assert node_by_name("REVIEW").fn is node
