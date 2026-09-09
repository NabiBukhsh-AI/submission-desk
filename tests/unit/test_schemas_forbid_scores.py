"""The structural half of the evidence-first contract.

A prompt asking a model not to produce a score is an instruction. A schema with
nowhere to put one is a guarantee. This test walks every model a language model
is allowed to fill, including nested models, and fails if a field appears that
could carry a judgment about a candidate or a protected attribute.

Names are matched by token rather than by substring, because "page_start"
contains "star" and "page_end" contains "age", and a check that flags those
teaches people to route around it.

Do not weaken this test. If a change needs a field this rejects, the change is
wrong, not the test.
"""

from __future__ import annotations

from typing import Any, get_args, get_origin

import pytest
from pydantic import BaseModel
from pydantic.fields import FieldInfo

from domain.contracts import RESPONSE_MODELS, CandidateProfile, Contract

#: Name tokens that would let a model grade a candidate.
FORBIDDEN_JUDGMENT_TOKENS = frozenset(
    {
        "score",
        "scores",
        "rating",
        "ratings",
        "rank",
        "ranking",
        "band",
        "bands",
        "grade",
        "verdict",
        "recommendation",
        "recommend",
        "decision",
        "percentile",
        "percentage",
        "fit",
        "suitability",
        "quality",
        "strength",
        "weakness",
        "overall",
        "hire",
        "hireable",
        "reject",
        "advance",
        "star",
        "stars",
    }
)

#: Name tokens for attributes a document cannot support and this system will
#: not infer.
FORBIDDEN_ATTRIBUTE_TOKENS = frozenset(
    {
        "age",
        "gender",
        "sex",
        "nationality",
        "ethnicity",
        "ethnic",
        "race",
        "religion",
        "marital",
        "family",
        "disability",
        "personality",
        "culture",
        "appearance",
        "photo",
        "birth",
        "dob",
    }
)

#: The single judgment-adjacent numeric field a model may return. It is
#: self-reported, documented as weakly calibrated, and never summed into a
#: score.
ALLOWED_NUMERIC_FIELDS = frozenset({"confidence"})

#: Numeric fields that address text rather than judge it.
ALLOWED_LOCATION_FIELDS = frozenset({"page_start", "page_end", "norm_start", "norm_end"})


def _tokens(name: str) -> set[str]:
    return set(name.lower().replace("-", "_").split("_"))


def _reachable_models(model: type[BaseModel]) -> set[type[BaseModel]]:
    """Every model reachable from one response schema, including nested ones.

    Checking only the top level would miss a score one list deep, which is
    exactly where it would end up.
    """
    seen: set[type[BaseModel]] = set()
    queue = [model]
    while queue:
        current = queue.pop()
        if current in seen:
            continue
        seen.add(current)
        for field in current.model_fields.values():
            queue.extend(_nested_models(field.annotation))
    return seen


def _nested_models(annotation: Any) -> list[type[BaseModel]]:
    found: list[type[BaseModel]] = []
    if isinstance(annotation, type) and issubclass(annotation, BaseModel):
        found.append(annotation)
    for argument in get_args(annotation):
        found.extend(_nested_models(argument))
    return found


def _is_numeric(annotation: Any) -> bool:
    if annotation in (int, float):
        return True
    if get_origin(annotation) is not None:
        return any(_is_numeric(argument) for argument in get_args(annotation))
    return False


def _all_response_fields() -> list[tuple[str, str, FieldInfo]]:
    fields: list[tuple[str, str, FieldInfo]] = []
    for response_model in RESPONSE_MODELS:
        for model in sorted(_reachable_models(response_model), key=lambda m: m.__name__):
            for name, field in model.model_fields.items():
                fields.append((model.__name__, name, field))
    return fields


def _schema_names(node: Any, found: set[str]) -> None:
    """Property names and enum values in a JSON Schema, ignoring prose.

    Titles and descriptions are excluded deliberately. The invariant is that no
    field can hold a judgment, not that the word never appears: the docstring on
    ``EvidenceState`` explains why there is no score, and a check that failed on
    that would be an argument for deleting the explanation.
    """
    if isinstance(node, dict):
        for key, value in node.items():
            if key in ("title", "description"):
                continue
            if key == "properties" and isinstance(value, dict):
                found.update(value.keys())
            if key == "enum" and isinstance(value, list):
                found.update(str(item) for item in value)
            _schema_names(value, found)
    elif isinstance(node, list):
        for item in node:
            _schema_names(item, found)


RESPONSE_FIELDS = _all_response_fields()


def test_there_are_response_models_to_check() -> None:
    """A silent pass because nothing was collected would be the worst outcome."""
    assert len(RESPONSE_MODELS) == 3
    assert len(RESPONSE_FIELDS) > 10


@pytest.mark.parametrize(("model_name", "field_name", "field"), RESPONSE_FIELDS)
def test_no_response_field_can_hold_a_judgment(
    model_name: str, field_name: str, field: FieldInfo
) -> None:
    offending = _tokens(field_name) & FORBIDDEN_JUDGMENT_TOKENS
    assert not offending, (
        f"{model_name}.{field_name} suggests a {', '.join(sorted(offending))}. The model "
        f"produces evidence; deterministic code produces the score."
    )


@pytest.mark.parametrize(("model_name", "field_name", "field"), RESPONSE_FIELDS)
def test_no_response_field_can_hold_a_forbidden_attribute(
    model_name: str, field_name: str, field: FieldInfo
) -> None:
    offending = _tokens(field_name) & FORBIDDEN_ATTRIBUTE_TOKENS
    assert not offending, (
        f"{model_name}.{field_name} suggests the protected attribute {', '.join(sorted(offending))}"
    )


@pytest.mark.parametrize(("model_name", "field_name", "field"), RESPONSE_FIELDS)
def test_the_only_judgment_numeric_is_confidence(
    model_name: str, field_name: str, field: FieldInfo
) -> None:
    """A float is how a score gets in, so every numeric field is accounted for."""
    if not _is_numeric(field.annotation):
        return
    assert field_name in ALLOWED_NUMERIC_FIELDS | ALLOWED_LOCATION_FIELDS, (
        f"{model_name}.{field_name} is numeric and unaccounted for. Add it to the "
        f"allowed sets only if it addresses text; never if it grades a candidate."
    )


@pytest.mark.parametrize("response_model", RESPONSE_MODELS, ids=lambda m: m.__name__)
def test_the_serialised_schema_exposes_no_judgment_field(
    response_model: type[Contract],
) -> None:
    """The provider is shown the JSON Schema, not the Python class, so the
    guarantee is checked in the serialised form including nested definitions."""
    names: set[str] = set()
    _schema_names(response_model.model_json_schema(), names)
    assert names, "no property names were collected, so this test proves nothing"

    for name in names:
        offending = _tokens(name) & (FORBIDDEN_JUDGMENT_TOKENS | FORBIDDEN_ATTRIBUTE_TOKENS)
        assert not offending, (
            f"{response_model.__name__} schema exposes {name!r}, which reads as "
            f"{', '.join(sorted(offending))}"
        )


@pytest.mark.parametrize("response_model", RESPONSE_MODELS, ids=lambda m: m.__name__)
def test_response_models_forbid_unknown_fields(response_model: type[Contract]) -> None:
    """Otherwise a model could return a score and have it silently dropped, which
    hides the fact that it tried."""
    assert response_model.model_config.get("extra") == "forbid"


# --- the profile, where absence is the control -------------------------------


@pytest.mark.parametrize(
    "forbidden_field",
    [
        "age",
        "gender",
        "nationality",
        "ethnicity",
        "religion",
        "marital_status",
        "family_status",
        "disability",
        "personality",
        "culture_fit",
        "appearance",
        "photo",
        "date_of_birth",
    ],
)
def test_the_profile_has_no_field_for_a_protected_attribute(forbidden_field: str) -> None:
    """A model cannot report what the schema cannot hold."""
    assert forbidden_field not in CandidateProfile.model_fields


def test_the_profile_has_no_judgment_field() -> None:
    for name in CandidateProfile.model_fields:
        assert not _tokens(name) & FORBIDDEN_JUDGMENT_TOKENS, f"CandidateProfile.{name}"


def test_the_profile_has_no_field_for_work_authorisation() -> None:
    """Held separately, so it cannot colour the assessment that precedes it."""
    assert "work_authorization" not in CandidateProfile.model_fields
    assert "work_authorisation" not in CandidateProfile.model_fields
