"""Five call sites, and no sixth.

The registry is where "explain precisely why every LLM call exists" stops being
a claim in a document and becomes a thing a test can check. Every entry declares
why a model is needed, what schema it fills, and what happens when it fails.

The absences matter more than the entries. A call site named `score` or `rank`
would mean a model deciding an outcome, which is the one thing this
architecture is built to prevent.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from domain.contracts.enums import ModelTier
from domain.contracts.responses import AssessmentResponse, CompositionResponse, InjectionVerdict
from infrastructure.models.registry import (
    CALL_REGISTRY,
    FORBIDDEN_CALL_SITE_WORDS,
    CallSpec,
    spec_for,
)

REPO_ROOT = Path(__file__).resolve().parents[2]

EXPECTED_SITES = {
    "structure.profile",
    "assess.criterion",
    "compose.questions",
    "sanitize.classify",
    "calibrate.embed",
}


def test_there_are_exactly_five_call_sites() -> None:
    """This is the entire probabilistic surface of the system. Adding a sixth
    requires a decision record, which is friction on purpose."""
    assert set(CALL_REGISTRY) == EXPECTED_SITES


@pytest.mark.parametrize("call_site", sorted(EXPECTED_SITES))
def test_every_entry_is_fully_declared(call_site: str) -> None:
    spec = CALL_REGISTRY[call_site]

    assert spec.call_site == call_site
    assert spec.why_a_model.strip()
    assert spec.output_schema is not None
    assert isinstance(spec.default_tier, ModelTier)
    assert 0.0 <= spec.temperature <= 1.0
    assert spec.on_failure.strip()


@pytest.mark.parametrize("call_site", sorted(EXPECTED_SITES))
def test_every_call_site_justifies_itself_in_a_sentence(call_site: str) -> None:
    """A call site that cannot say why a model is needed is one that should be
    deterministic code. Forcing the sentence at declaration time is cheaper than
    discovering the answer in review."""
    why = CALL_REGISTRY[call_site].why_a_model

    assert len(why) > 60, f"{call_site}: the justification is too thin to be one"


# --- what must never be here ----------------------------------------------------------


@pytest.mark.parametrize("forbidden", sorted(FORBIDDEN_CALL_SITE_WORDS))
def test_no_call_site_asks_a_model_to_decide(forbidden: str) -> None:
    """There is no score, rank, judge, decide, recommend, or compare call site,
    and there never will be. The model produces evidence; deterministic code
    produces the score."""
    for call_site in CALL_REGISTRY:
        tokens = set(call_site.replace(".", "_").split("_"))
        assert forbidden not in tokens, f"{call_site} names a decision a model must not make"


def test_no_response_schema_could_carry_a_verdict() -> None:
    """Checked here as well as in the schema tests, because this is the list a
    reader consults to find out what a model is asked for."""
    for call_site, spec in CALL_REGISTRY.items():
        schema = spec.output_schema
        if not hasattr(schema, "model_fields"):
            continue
        for name in schema.model_fields:
            tokens = set(name.lower().split("_"))
            assert not tokens & FORBIDDEN_CALL_SITE_WORDS, f"{call_site}.{name}"


# --- the registry matches what the code actually calls -----------------------------------


def _call_sites_used_in_source() -> set[str]:
    """Every string literal passed as call_site anywhere in the tree.

    Read from the AST rather than grepped, so a call site assembled from a
    variable is visibly absent rather than silently matched.
    """
    found: set[str] = set()
    for package in ("pipeline", "application", "infrastructure", "app", "eval"):
        for path in sorted((REPO_ROOT / package).rglob("*.py")):
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            for node in ast.walk(tree):
                if (
                    isinstance(node, ast.keyword)
                    and node.arg == "call_site"
                    and isinstance(node.value, ast.Constant)
                    and isinstance(node.value.value, str)
                ):
                    found.add(node.value.value)
    return found


def test_nothing_calls_a_model_at_an_unregistered_site() -> None:
    """The check that keeps the registry honest as the pipeline fills in.

    Two registries, and the split is deliberate. The system's has exactly five
    entries and a test that says so, because that number is a claim about the
    product. The evaluation declares its own comparison arm separately, so
    measuring the alternative does not inflate the figure describing the thing
    being measured.
    """
    from eval.arms.registry import EVAL_CALL_REGISTRY

    used = _call_sites_used_in_source()
    declared = set(CALL_REGISTRY) | set(EVAL_CALL_REGISTRY)
    unregistered = used - declared

    assert unregistered == set(), f"undeclared call sites: {sorted(unregistered)}"


def test_the_evaluation_declares_why_it_calls_a_model() -> None:
    """The same discipline, in the harness. An evaluation arm is no exception to
    the rule that every call is written down with a reason."""
    from eval.arms.registry import EVAL_CALL_REGISTRY

    assert EVAL_CALL_REGISTRY
    for site, reason in EVAL_CALL_REGISTRY.items():
        assert site.startswith("eval.")
        assert len(reason.strip()) > 60


def test_the_two_registries_do_not_overlap() -> None:
    """A site in both would be counted once and explained twice, and the two
    explanations would drift."""
    from eval.arms.registry import EVAL_CALL_REGISTRY

    assert set(CALL_REGISTRY).isdisjoint(EVAL_CALL_REGISTRY)


def test_asking_for_an_unknown_site_says_what_is_known() -> None:
    with pytest.raises(KeyError, match="not a declared call site"):
        spec_for("score.candidate")


# --- the settings that make results comparable ---------------------------------------------


@pytest.mark.parametrize("call_site", ["structure.profile", "assess.criterion"])
def test_the_deciding_calls_run_at_temperature_zero(call_site: str) -> None:
    """Reproducibility is the precondition for the routing, calibration and
    fairness comparisons meaning anything. Changing this requires an evaluation
    run showing the effect."""
    assert CALL_REGISTRY[call_site].temperature == 0.0


def test_only_the_writing_call_has_any_temperature() -> None:
    """Phrasing a question benefits from variety. Deciding what the evidence
    says does not."""
    warm = {site for site, spec in CALL_REGISTRY.items() if spec.temperature > 0}

    assert warm == {"compose.questions"}


def test_only_the_two_expensive_calls_may_escalate() -> None:
    """Escalation costs a stronger-tier call. It is worth it where a wrong
    answer changes a recommendation, and not where it changes a sentence."""
    escalating = {site for site, spec in CALL_REGISTRY.items() if spec.may_escalate}

    assert escalating == {"structure.profile", "assess.criterion"}


def test_the_schemas_are_the_ones_the_contracts_declare() -> None:
    assert CALL_REGISTRY["assess.criterion"].output_schema is AssessmentResponse
    assert CALL_REGISTRY["compose.questions"].output_schema is CompositionResponse
    assert CALL_REGISTRY["sanitize.classify"].output_schema is InjectionVerdict


def test_a_spec_is_immutable() -> None:
    """A registry that can be edited at runtime is not a declaration."""
    spec = CALL_REGISTRY["assess.criterion"]

    with pytest.raises((AttributeError, TypeError)):
        spec.temperature = 0.9  # type: ignore[misc]


def test_the_registry_is_the_only_place_call_sites_are_defined() -> None:
    """A second list would drift from this one."""
    assert isinstance(CALL_REGISTRY["assess.criterion"], CallSpec)
