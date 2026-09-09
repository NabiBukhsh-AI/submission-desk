"""No loop, anywhere, ever.

Every retry path in this system has a finite maximum, and the total for one call
site is three: the original, one repair, one escalation. A `while True` in a
retry layer is how a five-day sprint produces a provider bill nobody can
explain, and how a single bad document takes a run down with it.

This file checks the property two ways: by driving the layers with a client that
always fails and counting, and by reading the source for loop shapes that have
no bound.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from domain.contracts.enums import ModelTier
from domain.contracts.responses import AssessmentResponse
from domain.ports.models import BlockKind, GenerationRequest, PromptBlock
from infrastructure.models.fake import ScriptedModelClient
from infrastructure.models.registry import CALL_REGISTRY
from infrastructure.models.repairing import MAX_REPAIRS, RepairingClient

REPO_ROOT = Path(__file__).resolve().parents[2]
MODEL_DIR = REPO_ROOT / "infrastructure" / "models"

#: initial + repair + escalated. Stated once, here, so the number in the
#: architecture and the number the code enforces cannot drift apart.
MAX_CALLS_PER_CALL_SITE = 3

MALFORMED = "{not json"


def request() -> GenerationRequest:
    return GenerationRequest(
        call_site="assess.criterion",
        tier=ModelTier.CHEAP,
        system_prompt="find and quote",
        user_blocks=(PromptBlock(kind=BlockKind.DOCUMENT, content="a CV"),),
        response_schema=AssessmentResponse,
        nonce="a3f9",
    )


# --- measured by driving it ---------------------------------------------------------


def test_a_permanently_failing_model_stops_after_two_calls() -> None:
    """The repair layer's whole budget. It does not keep asking."""
    inner = ScriptedModelClient(responses=[MALFORMED] * 20)
    client = RepairingClient(inner=inner)

    client.structured_generate(request())

    assert inner.call_count == 1 + MAX_REPAIRS


def test_repeated_calls_do_not_accumulate_attempts() -> None:
    """Each call gets its own budget; a previous failure does not lend the next
    one extra tries."""
    inner = ScriptedModelClient(responses=[MALFORMED] * 20)
    client = RepairingClient(inner=inner)

    client.structured_generate(request())
    client.structured_generate(request())

    assert inner.call_count == 2 * (1 + MAX_REPAIRS)


def test_the_repair_budget_is_exactly_one() -> None:
    """Not a default and not configurable at the call site: the architecture
    caps a call site at three attempts and this layer owns one of them."""
    assert MAX_REPAIRS == 1


def test_every_call_site_declares_a_finite_repair_budget() -> None:
    for call_site, spec in CALL_REGISTRY.items():
        assert spec.max_repairs <= MAX_REPAIRS, call_site
        assert spec.max_repairs >= 0, call_site


def test_the_total_for_one_call_site_is_three() -> None:
    """initial + repair + escalated. The escalation is at most one, decided by
    the routing layer, and no call site may exceed the sum."""
    for call_site, spec in CALL_REGISTRY.items():
        total = 1 + spec.max_repairs + (1 if spec.may_escalate else 0)
        assert total <= MAX_CALLS_PER_CALL_SITE, f"{call_site} allows {total} calls"


# --- read from the source -------------------------------------------------------------


def _unbounded_loops(path: Path) -> list[str]:
    """Loops with no exit condition in their header.

    ``while True`` is the shape being looked for. It has legitimate uses, but
    not in a layer that calls a paid API, and a bounded loop is easy to write
    instead.
    """
    found: list[str] = []
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))

    for node in ast.walk(tree):
        if isinstance(node, ast.While):
            test = node.test
            if isinstance(test, ast.Constant) and test.value is True:
                found.append(f"{path.name}:{node.lineno}: while True")
    return found


def test_no_model_layer_contains_an_unbounded_loop() -> None:
    offenders: list[str] = []
    for path in sorted(MODEL_DIR.rglob("*.py")):
        offenders.extend(_unbounded_loops(path))

    assert offenders == [], "unbounded retry loop:\n" + "\n".join(offenders)


def test_no_pipeline_node_contains_an_unbounded_loop() -> None:
    """A node that retried forever would never return to the runner, and the
    run would sit in the queue with no way to clear it."""
    offenders: list[str] = []
    for path in sorted((REPO_ROOT / "pipeline").rglob("*.py")):
        offenders.extend(_unbounded_loops(path))

    assert offenders == []


def test_no_layer_sleeps_without_a_bound() -> None:
    """A retry that sleeps in a loop is the same problem wearing a delay: the
    caller waits and the run appears to hang."""
    offenders = []
    for path in sorted(MODEL_DIR.rglob("*.py")):
        source = path.read_text(encoding="utf-8")
        if "time.sleep" in source and "while True" in source:
            offenders.append(path.name)

    assert offenders == []


@pytest.mark.parametrize("call_site", sorted(CALL_REGISTRY))
def test_every_call_site_says_what_happens_when_it_fails(call_site: str) -> None:
    """A call site with no defined failure behaviour is one that will surprise
    somebody at four in the afternoon on day four."""
    spec = CALL_REGISTRY[call_site]

    assert spec.on_failure.strip()
    assert "exception" not in spec.on_failure.lower()
    assert "crash" not in spec.on_failure.lower()
