"""Turning tokens into money, or honestly declining to.

The rule this module exists to enforce: an unconfigured price produces ``None``,
never zero and never an estimate. A zero reads as free. An estimate is a number
nobody measured, and the whole argument of this system is that a claim without a
measurement behind it is not a claim.

Token counts are always real — they come from provider usage metadata on every
call — so the budget ceiling and the circuit breaker work whether or not this
file has been filled in. Only the conversion to money is missing, and the
interface says so in those words.

The file's hash is recorded on every cost record. A figure in a report can then
be traced to the rates that were in force when it was produced, which matters
because provider prices change and a stale number would quietly make every past
report wrong.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path

import yaml

from domain.contracts.enums import ModelTier

#: Prices are quoted per million tokens, which is how every provider publishes
#: them. Converting at load time would introduce a rounding decision nobody
#: asked for.
PER = Decimal(1_000_000)

#: Cost is stored to this many places. Six is enough that a single cheap call
#: does not round to zero, which would put a zero back into the interface
#: through the back door.
PLACES = Decimal("0.000001")

#: What the interface says when there is nothing to convert with. Written here
#: so every panel says the same thing.
UNCONFIGURED = "pricing not configured"


@dataclass(frozen=True)
class TierPrice:
    """What one tier costs, or does not."""

    input_per_million: Decimal | None = None
    output_per_million: Decimal | None = None
    cached_input_per_million: Decimal | None = None

    @property
    def configured(self) -> bool:
        """Both directions priced, or this tier cannot produce a cost.

        Half a price is not a price: costing input and guessing output would be
        the estimate this module exists to refuse.
        """
        return self.input_per_million is not None and self.output_per_million is not None


@dataclass(frozen=True)
class Pricing:
    """The loaded price list, and where it came from."""

    tiers: dict[str, TierPrice]
    source: str
    version: str = "unpriced"

    @property
    def configured(self) -> bool:
        return any(price.configured for price in self.tiers.values())

    def for_tier(self, tier: ModelTier) -> TierPrice:
        return self.tiers.get(tier.value, TierPrice())

    def cost_of(
        self,
        tier: ModelTier,
        *,
        input_tokens: int,
        output_tokens: int,
        cached_input_tokens: int = 0,
    ) -> Decimal | None:
        """What this call cost, or ``None`` if it cannot be known.

        ``None`` is the honest answer and the contract permits it. Nothing in
        this function has a branch that returns zero for an unconfigured tier,
        which is deliberate: that branch is the bug this module is written to
        make impossible.
        """
        price = self.for_tier(tier)
        if not price.configured:
            return None

        billable_input = max(input_tokens - cached_input_tokens, 0)

        total = (
            Decimal(billable_input) * price.input_per_million  # type: ignore[operator]
            + Decimal(output_tokens) * price.output_per_million  # type: ignore[operator]
        )

        cached_rate = price.cached_input_per_million
        if cached_input_tokens and cached_rate is not None:
            total += Decimal(cached_input_tokens) * cached_rate

        return (total / PER).quantize(PLACES, rounding=ROUND_HALF_UP)

    def describe(self, tier: ModelTier) -> str:
        """One line about this tier's rates, for the operations page."""
        price = self.for_tier(tier)
        if not price.configured:
            return UNCONFIGURED
        return f"${price.input_per_million} in / ${price.output_per_million} out per million tokens"


def _decimal(value: object) -> Decimal | None:
    """A price, or None. Never zero by accident.

    An explicit ``0`` in the file is honoured — a free tier exists — but the
    absence of a value is not read as one.
    """
    if value is None or value == "":
        return None
    try:
        return Decimal(str(value))
    except Exception:
        return None


def load(path: Path | str = Path("config/pricing.yaml")) -> Pricing:
    """Read the price list, recording its fingerprint.

    A missing file is not an error. The repository ships without prices on
    purpose, and a system that refused to start without them would be a system
    nobody could try.
    """
    path = Path(path)
    if not path.is_file():
        return Pricing(tiers={}, source="pricing:absent")

    raw = path.read_bytes()
    digest = hashlib.sha256(raw).hexdigest()[:16]

    try:
        data = yaml.safe_load(raw.decode("utf-8")) or {}
    except yaml.YAMLError:
        return Pricing(tiers={}, source=f"pricing:unreadable:{digest}")

    tiers = {
        name: TierPrice(
            input_per_million=_decimal((values or {}).get("input_per_million")),
            output_per_million=_decimal((values or {}).get("output_per_million")),
            cached_input_per_million=_decimal((values or {}).get("cached_input_per_million")),
        )
        for name, values in (data.get("tiers") or {}).items()
    }

    return Pricing(
        tiers=tiers,
        source=f"{path.name}@{digest}",
        version=str(data.get("version", "unpriced")),
    )


def format_cost(value: Decimal | None) -> str:
    """A cost figure for a person to read, or the honest absence of one.

    The one function every panel goes through, so no page can render its own
    zero when the answer is that nobody knows.
    """
    if value is None:
        return UNCONFIGURED
    return f"${value:.4f}"
