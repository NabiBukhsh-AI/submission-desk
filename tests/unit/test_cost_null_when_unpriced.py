"""No price means no number. Not zero, and not a guess.

A zero reads as free, and free is a claim. A guess is a number nobody measured,
and this system's whole argument is that a claim without a measurement behind it
is not a claim.

Token counts stay real either way, because they come from provider usage
metadata on every call. The budget ceiling and the circuit breaker work
unpriced; only the conversion to money is missing, and the interface says so in
those words.
"""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path

import pytest
import yaml

from domain.contracts.enums import ModelTier
from infrastructure.models.pricing import UNCONFIGURED, Pricing, TierPrice, format_cost, load

REPO_ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def unpriced() -> Pricing:
    """The price list as this repository ships it."""
    return load(REPO_ROOT / "config" / "pricing.yaml")


@pytest.fixture
def priced(tmp_path: Path) -> Pricing:
    path = tmp_path / "pricing.yaml"
    path.write_text(
        yaml.safe_dump(
            {
                "version": "2026-01",
                "tiers": {
                    "tier_cheap": {
                        "input_per_million": 0.25,
                        "output_per_million": 1.25,
                        "cached_input_per_million": 0.03,
                    }
                },
            }
        ),
        encoding="utf-8",
    )
    return load(path)


# --- the shipped state -------------------------------------------------------------


def test_the_repository_ships_unpriced(unpriced: Pricing) -> None:
    """Deliberately. Provider prices change, and a stale number committed here
    would quietly make every past report wrong."""
    assert unpriced.configured is False


def test_an_unpriced_tier_produces_no_cost(unpriced: Pricing) -> None:
    cost = unpriced.cost_of(ModelTier.CHEAP, input_tokens=120_000, output_tokens=30_000)

    assert cost is None


def test_the_cost_is_not_zero(unpriced: Pricing) -> None:
    """Written out separately because this is the failure mode. A zero passes a
    "did it return a number" test and lies to every reader."""
    cost = unpriced.cost_of(ModelTier.CHEAP, input_tokens=120_000, output_tokens=30_000)

    assert cost != 0
    assert cost != Decimal(0)
    assert cost is not False


def test_the_rendered_figure_is_not_a_zero_string(unpriced: Pricing) -> None:
    rendered = format_cost(unpriced.cost_of(ModelTier.CHEAP, input_tokens=1000, output_tokens=1000))

    assert rendered == UNCONFIGURED
    assert "$0" not in rendered
    assert "0.00" not in rendered


def test_the_wording_says_not_configured(unpriced: Pricing) -> None:
    """Not "unavailable", not "—". A sentence saying what to do about it."""
    assert "not configured" in UNCONFIGURED
    assert unpriced.describe(ModelTier.CHEAP) == UNCONFIGURED


@pytest.mark.parametrize("tier", list(ModelTier))
def test_no_tier_is_priced_by_default(unpriced: Pricing, tier: ModelTier) -> None:
    assert unpriced.cost_of(tier, input_tokens=1, output_tokens=1) is None


# --- when prices are configured -------------------------------------------------------


def test_a_priced_tier_produces_a_figure(priced: Pricing) -> None:
    cost = priced.cost_of(ModelTier.CHEAP, input_tokens=1_000_000, output_tokens=1_000_000)

    assert cost == Decimal("1.500000")


def test_cached_tokens_are_billed_at_their_own_rate(priced: Pricing) -> None:
    """Cached input is cheaper, and counting it at the full rate would overstate
    what caching saved — which is the number the caching decision rests on."""
    full = priced.cost_of(ModelTier.CHEAP, input_tokens=1_000_000, output_tokens=0)
    cached = priced.cost_of(
        ModelTier.CHEAP,
        input_tokens=1_000_000,
        output_tokens=0,
        cached_input_tokens=1_000_000,
    )

    assert cached < full


def test_an_unpriced_tier_stays_unpriced_alongside_a_priced_one(priced: Pricing) -> None:
    """Half a price list is not a price list for the missing half."""
    assert priced.cost_of(ModelTier.CHEAP, input_tokens=100, output_tokens=100) is not None
    assert priced.cost_of(ModelTier.STRONG, input_tokens=100, output_tokens=100) is None


def test_a_half_configured_tier_produces_nothing() -> None:
    """Costing input and guessing output would be exactly the estimate this
    module exists to refuse."""
    half = Pricing(
        tiers={"tier_cheap": TierPrice(input_per_million=Decimal("0.25"))},
        source="test",
    )

    assert half.cost_of(ModelTier.CHEAP, input_tokens=1000, output_tokens=1000) is None


def test_a_small_call_does_not_round_to_zero(priced: Pricing) -> None:
    """Rounding to two places would put a zero back into the interface through
    the back door."""
    cost = priced.cost_of(ModelTier.CHEAP, input_tokens=100, output_tokens=100)

    assert cost is not None
    assert cost > 0


def test_an_explicit_zero_price_is_honoured(tmp_path: Path) -> None:
    """A free tier exists. The absence of a value is not read as one, but a
    written zero is."""
    path = tmp_path / "free.yaml"
    path.write_text(
        yaml.safe_dump(
            {"tiers": {"tier_cheap": {"input_per_million": 0, "output_per_million": 0}}}
        ),
        encoding="utf-8",
    )

    pricing = load(path)

    assert pricing.cost_of(ModelTier.CHEAP, input_tokens=1000, output_tokens=1000) == 0


# --- traceability -----------------------------------------------------------------------


def test_the_price_list_records_its_fingerprint(priced: Pricing) -> None:
    """A figure in a report can be traced to the rates in force when it ran."""
    assert "@" in priced.source
    assert priced.source.startswith("pricing.yaml@")


def test_a_changed_price_list_changes_the_fingerprint(tmp_path: Path) -> None:
    path = tmp_path / "pricing.yaml"
    path.write_text("tiers: {tier_cheap: {input_per_million: 1}}", encoding="utf-8")
    before = load(path).source

    path.write_text("tiers: {tier_cheap: {input_per_million: 2}}", encoding="utf-8")

    assert load(path).source != before


def test_a_missing_file_is_not_an_error(tmp_path: Path) -> None:
    """The repository ships without prices, and a system that refused to start
    without them would be one nobody could try."""
    pricing = load(tmp_path / "nothing-here.yaml")

    assert pricing.configured is False
    assert pricing.source == "pricing:absent"


def test_a_malformed_file_produces_no_prices(tmp_path: Path) -> None:
    """An unreadable price list is an absent one, never a guessed one."""
    path = tmp_path / "broken.yaml"
    path.write_text("tiers: [this is: not: a mapping", encoding="utf-8")

    pricing = load(path)

    assert pricing.configured is False
    assert "unreadable" in pricing.source


def test_a_malformed_price_is_treated_as_absent(tmp_path: Path) -> None:
    path = tmp_path / "odd.yaml"
    path.write_text(
        yaml.safe_dump(
            {"tiers": {"tier_cheap": {"input_per_million": "cheap", "output_per_million": 1}}}
        ),
        encoding="utf-8",
    )

    assert load(path).cost_of(ModelTier.CHEAP, input_tokens=1, output_tokens=1) is None
