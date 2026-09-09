"""The circuit breaker.

Cost control that is visible before it is forced. The ceiling holds whether or
not anyone has entered a price, because tokens are always real and money is
optional: a system that could only stop spending once it knew the price would
not stop at all in its shipped state.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from uuid import uuid4

from application.budget import BudgetGuard, BudgetState
from domain.contracts.enums import RunStatus
from domain.contracts.run_state import RunState


def state(run_id=None) -> RunState:
    return RunState(
        run_id=run_id or uuid4(),
        candidate_id="cand-0007",
        role_id="ai-engineer",
        status=RunStatus.CREATED,
        started_at=datetime.now(UTC),
    )


# --- the token ceiling ------------------------------------------------------------


def test_a_fresh_run_may_proceed() -> None:
    assert BudgetGuard(token_ceiling=1000, max_escalations=3).allows(state())


def test_a_run_at_its_ceiling_may_not() -> None:
    guard = BudgetGuard(token_ceiling=1000, max_escalations=3)
    run = state()
    guard.record(run.run_id, input_tokens=800, output_tokens=200)

    assert guard.allows(run) is False


def test_the_ceiling_holds_with_no_prices_configured() -> None:
    """The shipped state. A ceiling that needed a price would not hold."""
    guard = BudgetGuard(token_ceiling=1000, max_escalations=3)
    run = state()
    guard.record(run.run_id, input_tokens=1000, output_tokens=0, cost_usd=None)

    assert guard.allows(run) is False
    assert guard.state_for(run.run_id).cost_usd is None


def test_a_money_ceiling_also_stops_a_run() -> None:
    guard = BudgetGuard(
        token_ceiling=1_000_000, max_escalations=3, cost_ceiling_usd=Decimal("1.00")
    )
    run = state()
    guard.record(run.run_id, input_tokens=10, output_tokens=10, cost_usd=Decimal("1.50"))

    assert guard.allows(run) is False


def test_the_reason_is_a_sentence_a_person_can_act_on() -> None:
    guard = BudgetGuard(token_ceiling=1000, max_escalations=3)
    run = state()
    guard.record(run.run_id, input_tokens=1000, output_tokens=0)

    reason = guard.remaining_reason(run)

    assert reason is not None
    assert "saved" in reason
    for jargon in ("exception", "traceback", "null", "budget_exceeded"):
        assert jargon not in reason.lower()


def test_a_run_below_the_ceiling_has_no_reason_to_stop() -> None:
    guard = BudgetGuard(token_ceiling=1000, max_escalations=3)
    run = state()
    guard.record(run.run_id, input_tokens=10, output_tokens=10)

    assert guard.remaining_reason(run) is None


# --- accounting -------------------------------------------------------------------


def test_tokens_accumulate_across_calls() -> None:
    guard = BudgetGuard(token_ceiling=10_000, max_escalations=3)
    run = state()

    guard.record(run.run_id, input_tokens=100, output_tokens=20)
    guard.record(run.run_id, input_tokens=50, output_tokens=10)

    tracked = guard.state_for(run.run_id)
    assert tracked.input_tokens == 150
    assert tracked.output_tokens == 30
    assert tracked.total_tokens == 180


def test_costs_accumulate_only_when_priced() -> None:
    """An unpriced call adds tokens and no money, rather than adding a zero that
    would read as free."""
    guard = BudgetGuard(token_ceiling=10_000, max_escalations=3)
    run = state()

    guard.record(run.run_id, input_tokens=10, output_tokens=1, cost_usd=None)
    assert guard.state_for(run.run_id).cost_usd is None

    guard.record(run.run_id, input_tokens=10, output_tokens=1, cost_usd=Decimal("0.01"))
    assert guard.state_for(run.run_id).cost_usd == Decimal("0.01")


def test_calls_are_counted() -> None:
    guard = BudgetGuard(token_ceiling=10_000, max_escalations=3)
    run = state()

    for _ in range(3):
        guard.record(run.run_id, input_tokens=1, output_tokens=1)

    assert guard.state_for(run.run_id).llm_calls == 3


def test_runs_are_tracked_separately() -> None:
    """A batch must not let one candidate spend another's budget."""
    guard = BudgetGuard(token_ceiling=1000, max_escalations=3)
    first, second = state(), state()

    guard.record(first.run_id, input_tokens=1000, output_tokens=0)

    assert guard.allows(first) is False
    assert guard.allows(second) is True


# --- the escalation cap -------------------------------------------------------------


def test_escalations_are_capped() -> None:
    """Bounded worst case: one hard document cannot cost several times what a
    normal one does."""
    guard = BudgetGuard(token_ceiling=100_000, max_escalations=2)
    run_id = uuid4()

    assert guard.may_escalate(run_id)
    guard.record(run_id, input_tokens=1, output_tokens=1, escalated=True)
    assert guard.may_escalate(run_id)
    guard.record(run_id, input_tokens=1, output_tokens=1, escalated=True)

    assert guard.may_escalate(run_id) is False


def test_an_ordinary_call_does_not_count_against_the_escalation_cap() -> None:
    guard = BudgetGuard(token_ceiling=100_000, max_escalations=1)
    run_id = uuid4()

    guard.record(run_id, input_tokens=1, output_tokens=1, escalated=False)

    assert guard.may_escalate(run_id) is True


def test_escalation_caps_are_per_run() -> None:
    guard = BudgetGuard(token_ceiling=100_000, max_escalations=1)
    first, second = uuid4(), uuid4()

    guard.record(first, input_tokens=1, output_tokens=1, escalated=True)

    assert guard.may_escalate(first) is False
    assert guard.may_escalate(second) is True


def test_an_untouched_run_starts_at_zero() -> None:
    tracked = BudgetGuard(token_ceiling=100, max_escalations=1).state_for(uuid4())

    assert tracked == BudgetState()
