"""No price is written into the source.

The failure this catches is small, plausible, and very hard to see later:
somebody needs a cost figure for a demo, hardcodes a rate next to a token count,
and every report the system produces from then on is a number nobody measured.

Two things make it worth a source-walking test rather than a code review note.
The number would be right at first, so nothing would fail. And once a figure
exists, everything downstream treats it as measured.

Prices live in ``config/pricing.yaml`` and nowhere else, so this walks the tree
and fails on a float literal sitting near a token identifier.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]

#: Where a price legitimately appears: the configuration file, the module that
#: reads it, and the tests that check both.
EXEMPT = (
    "config",
    "tests/unit/test_cost_null_when_unpriced.py",
    "tests/unit/test_no_fabricated_costs.py",
)

#: Directories to walk. Everything the system actually runs.
SOURCE_DIRS = ("domain", "application", "infrastructure", "pipeline", "app", "eval", "scripts")

#: Identifiers that mean money is being computed nearby.
#:
#: Narrow on purpose. A bare "token" matches ROLE_TOKENS in the injection
#: detector and a dozen other places where the word has nothing to do with
#: billing, and a rule that fires on those is a rule somebody adds an exemption
#: list to until it means nothing. These are the names that only appear where a
#: cost is being computed.
TOKEN_WORDS = (
    "input_tokens",
    "output_tokens",
    "cached_input_tokens",
    "total_input_tokens",
    "total_output_tokens",
    "per_million",
    "cost_usd",
    "unit_price",
    "price_per",
    "usd",
)

#: How close a literal has to be to count as "next to".
NEARBY_LINES = 3

#: Floats that are obviously not prices. Zero and one appear everywhere; the
#: rest are ratios and percentages that mean nothing as a rate per million
#: tokens.
INNOCENT = frozenset({0.0, 1.0, 0.5, 100.0, 1000.0})

#: What a float is bound to, when it is definitely not a rate.
#:
#: Proximity alone is not enough. A timeout sits next to a max-output-tokens
#: field, a confidence threshold sits next to a token ceiling, and a budget
#: fraction sits next to everything — none of them are money, and flagging them
#: would push somebody to add file-level exemptions until the rule meant
#: nothing.
#:
#: This is a list of *kinds of number*, not a list of forgiven files. A new
#: literal called ``rate_per_million`` is still caught wherever it appears.
NOT_A_PRICE = (
    "timeout",
    "seconds",
    "_s",
    "confidence",
    "threshold",
    "ratio",
    "fraction",
    "share",
    "temperature",
    "delta",
    "weight",
    "coverage",
    "headroom",
    "ceiling",
    "dpi",
    "alpha",
    "min_",
    "max_",
    "warn",
)


def source_files() -> list[Path]:
    found: list[Path] = []
    for directory in SOURCE_DIRS:
        root = REPO_ROOT / directory
        if not root.is_dir():
            continue
        found += [
            path
            for path in root.rglob("*.py")
            if "__pycache__" not in path.parts
            and not any(part in path.as_posix() for part in EXEMPT)
        ]
    return sorted(found)


def token_lines(tree: ast.AST) -> set[int]:
    """Lines mentioning a token or cost identifier."""
    lines: set[int] = set()

    for node in ast.walk(tree):
        name = None
        if isinstance(node, ast.Name):
            name = node.id
        elif isinstance(node, ast.Attribute):
            name = node.attr
        elif isinstance(node, ast.keyword):
            name = node.arg
        elif isinstance(node, ast.Constant) and isinstance(node.value, str):
            name = node.value

        if name and any(word in str(name).lower() for word in TOKEN_WORDS):
            line = getattr(node, "lineno", None)
            if line:
                lines.add(line)

    return lines


def bound_names(tree: ast.AST) -> dict[int, str]:
    """What each float literal on a line is being assigned to.

    A number's name says what kind of number it is, and that is more reliable
    than how close it sits to something else.
    """
    names: dict[int, str] = {}

    for node in ast.walk(tree):
        target = None
        if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            target = node.target.id
        elif isinstance(node, ast.Assign):
            first = node.targets[0] if node.targets else None
            target = getattr(first, "id", None) or getattr(first, "attr", None)
        elif (isinstance(node, ast.keyword) and node.arg) or isinstance(node, ast.arg):
            target = node.arg

        if target and getattr(node, "lineno", None):
            names.setdefault(node.lineno, target)

    return names


def float_literals(tree: ast.AST) -> list[tuple[int, float]]:
    return [
        (node.lineno, float(node.value))
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant)
        and isinstance(node.value, float)
        and not isinstance(node.value, bool)
    ]


# --- the walk --------------------------------------------------------------------


def test_there_is_source_to_walk() -> None:
    """A test that silently found no files would pass forever."""
    assert len(source_files()) > 40


@pytest.mark.parametrize("path", source_files(), ids=lambda path: path.name)
def test_no_price_sits_next_to_a_token_count(path: Path) -> None:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    near = token_lines(tree)
    bound = bound_names(tree)

    for line, value in float_literals(tree):
        if value in INNOCENT:
            continue

        name = (bound.get(line) or "").lower()
        if any(word in name for word in NOT_A_PRICE):
            continue

        close = any(abs(line - other) <= NEARBY_LINES for other in near)
        assert not close, (
            f"{path.relative_to(REPO_ROOT)}:{line} has the literal {value} within "
            f"{NEARBY_LINES} lines of a token or cost identifier. If this is a "
            "price, it belongs in config/pricing.yaml."
        )


# --- the shape of the rule ---------------------------------------------------------


def test_pricing_is_read_from_configuration_only() -> None:
    """One reader, one file. A second path to a rate is a second answer."""
    from infrastructure.models import pricing

    source = Path(pricing.__file__).read_text(encoding="utf-8")

    assert "config/pricing.yaml" in source


def test_the_config_file_ships_without_prices() -> None:
    """If this ever fails, somebody committed a rate. Provider prices change,
    and a stale one would quietly make every past report wrong."""
    import yaml

    data = yaml.safe_load((REPO_ROOT / "config" / "pricing.yaml").read_text(encoding="utf-8"))

    for tier, values in (data.get("tiers") or {}).items():
        for field, value in (values or {}).items():
            assert value is None, f"{tier}.{field} carries a committed price: {value}"


def test_the_cost_contract_permits_nothing_to_be_known() -> None:
    """The contract has to allow ``None`` or the honest answer is unrepresentable
    and somebody will write a zero."""
    from domain.contracts.cost import CostRecord

    assert CostRecord.model_fields["cost_usd"].default is None


def test_a_cost_must_name_the_prices_that_produced_it() -> None:
    """A figure with no price source cannot be audited later."""
    from datetime import UTC, datetime
    from decimal import Decimal
    from uuid import uuid4

    from domain.contracts.cost import CostRecord
    from domain.contracts.enums import ModelTier

    with pytest.raises(ValueError, match="which pricing configuration"):
        CostRecord(
            record_id=uuid4(),
            run_id=uuid4(),
            call_site="assess.criterion",
            model_tier=ModelTier.CHEAP,
            input_tokens=100,
            output_tokens=50,
            cost_usd=Decimal("0.01"),
            latency_ms=100,
            occurred_at=datetime.now(UTC),
        )
