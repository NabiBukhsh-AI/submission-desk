"""The six panels, gathered once.

The operations page renders; it does not query. That split is the same one every
other page follows, and it earns the same two things: the page can be replaced
without re-deriving a number, and the evaluation can call this function to put
the same figures in a report.

It also puts the cost rule in one place. A page that reached for the pricing
module itself could format its own zero when the honest answer is that nobody
knows, and that is the exact failure the whole cost design exists to prevent.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from application.deps import Deps
from domain.ports.metrics import MetricsSnapshot, snapshot_of

#: The default window. A week is what "is anything stuck" means in practice.
DEFAULT_DAYS = 7

#: What the interface says when there is nothing to convert tokens with. One
#: wording, in one place, so no page can invent a gentler one — or a zero.
UNCONFIGURED_MESSAGE = (
    "Pricing is not configured. The token counts below are real and come from "
    "provider usage metadata; add rates to config/pricing.yaml to see money."
)


@dataclass(frozen=True)
class OperationsReport:
    """A snapshot, plus what is known about how to price it."""

    metrics: MetricsSnapshot
    pricing_configured: bool = False
    pricing_source: str = ""

    @property
    def has_runs(self) -> bool:
        return self.metrics.has_runs


def operations_report(deps: Deps, *, days: int = DEFAULT_DAYS) -> OperationsReport:
    """Read every panel's data in one pass."""
    return OperationsReport(
        metrics=snapshot_of(
            deps.metrics,
            days=days,
            stale_after_minutes=deps.settings.stale_run_minutes,
        ),
        pricing_configured=bool(getattr(deps.pricing, "configured", False)),
        pricing_source=str(getattr(deps.pricing, "source", "")),
    )


def format_cost(value: Decimal | None) -> str:
    """A cost figure for a person, or the honest absence of one.

    The one function every panel goes through. ``None`` is never rendered as a
    zero, because a zero reads as free and free is a claim nobody measured.
    """
    if value is None:
        return "not configured"
    return f"${value:.4f}"
