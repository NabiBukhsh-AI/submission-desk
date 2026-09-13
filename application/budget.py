"""The circuit breaker.

The ceiling is enforced here; the token counts it reads come from the model
client's usage on every call (``application/accounting.py``). What matters
most is where it sits.

The guard is consulted by the runner *before* a node executes, not by the node
itself. A node cannot spend money the guard was going to refuse, and a node
cannot forget to ask.

The token ceiling is enforced whether or not pricing is configured. Cost in
dollars is null until someone fills in `config/pricing.yaml`, but a run that
cannot be priced still must not be allowed to run away.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from domain.contracts.run_state import RunState
from domain.errors import SubmissionDeskError


class BudgetExceeded(SubmissionDeskError):
    """A run reached its ceiling. Converted to a state by the runner."""

    error_code = "BUDGET_EXCEEDED"
    retryable = False


@dataclass
class BudgetState:
    """What this run has spent so far.

    Tokens come from real provider usage metadata. They are never estimated,
    because an estimated ceiling is a ceiling that does not hold.
    """

    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: Decimal | None = None
    llm_calls: int = 0
    escalations: int = 0

    @property
    def total_tokens(self) -> int:
        return self.input_tokens + self.output_tokens


class BudgetGuard:
    """Refuses the next call when a run would cross its ceiling."""

    def __init__(
        self,
        *,
        token_ceiling: int,
        max_escalations: int,
        cost_ceiling_usd: Decimal | None = None,
    ) -> None:
        self.token_ceiling = token_ceiling
        self.max_escalations = max_escalations
        self.cost_ceiling_usd = cost_ceiling_usd
        self._by_run: dict[str, BudgetState] = {}

    def state_for(self, run_id: object) -> BudgetState:
        return self._by_run.setdefault(str(run_id), BudgetState())

    def record(
        self,
        run_id: object,
        *,
        input_tokens: int,
        output_tokens: int,
        cost_usd: Decimal | None = None,
        escalated: bool = False,
    ) -> BudgetState:
        state = self.state_for(run_id)
        state.input_tokens += input_tokens
        state.output_tokens += output_tokens
        state.llm_calls += 1
        state.escalations += int(escalated)
        if cost_usd is not None:
            state.cost_usd = (state.cost_usd or Decimal(0)) + cost_usd
        return state

    def allows(self, run: RunState) -> bool:
        """Whether the run may continue. Checked before every node."""
        return self.remaining_reason(run) is None

    def remaining_reason(self, run: RunState) -> str | None:
        """Why the run must stop, in a sentence, or None to continue."""
        state = self.state_for(run.run_id)

        if state.total_tokens >= self.token_ceiling:
            return (
                f"This run reached its limit of {self.token_ceiling:,} tokens. "
                "The work done so far is saved."
            )
        if (
            self.cost_ceiling_usd is not None
            and state.cost_usd is not None
            and state.cost_usd >= self.cost_ceiling_usd
        ):
            return "This run reached its cost ceiling. The work done so far is saved."
        return None

    def may_escalate(self, run_id: object) -> bool:
        """Escalations are capped per candidate, so a hard document cannot
        quietly cost several times what a normal one does."""
        return self.state_for(run_id).escalations < self.max_escalations
