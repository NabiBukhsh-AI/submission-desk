"""The rubric is edited by a recruiter, so its errors must read like advice.

Each malformed fixture is a mistake someone would plausibly make in a text
editor, and each is asserted to be rejected with a message naming the field to
fix.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml

from domain.contracts import CriterionKind, RoleRubric
from domain.rules import RubricInvalid, load_rubric, rubric_hash
from scripts.rubric_lint import check, parse

REPO_ROOT = Path(__file__).resolve().parents[2]
FIXTURES = REPO_ROOT / "tests" / "fixtures" / "rubrics"
SHIPPED = REPO_ROOT / "rubrics" / "ai-engineer.yaml"

#: Each malformed rubric, with a fragment the message must contain. The
#: fragment is what tells the person editing the file where to look.
MALFORMED = [
    ("non_monotonic_bands.yaml", "strictly decreasing"),
    ("zero_scoring_weight.yaml", "at least one criterion"),
    ("duplicate_criterion_id.yaml", "duplicate criterion ids"),
    ("missing_state_points.yaml", "state_points is missing"),
    ("points_on_insufficient.yaml", "must map to null"),
    ("blocker_zero_min_supported.yaml", "at least one supporting item"),
]


@pytest.mark.parametrize(("filename", "fragment"), MALFORMED)
def test_a_malformed_rubric_is_rejected(filename: str, fragment: str) -> None:
    ok, message = check(FIXTURES / filename)

    assert not ok, f"{filename} was accepted"
    assert fragment in message, f"{filename} was rejected without naming the problem:\n{message}"


@pytest.mark.parametrize(("filename", "fragment"), MALFORMED)
def test_the_message_names_the_file(filename: str, fragment: str) -> None:
    _, message = check(FIXTURES / filename)
    assert filename in message


def test_there_are_six_malformed_fixtures() -> None:
    """A fixture deleted to make the suite pass would otherwise go unnoticed."""
    on_disk = {path.name for path in FIXTURES.glob("*.yaml")}
    assert on_disk == {name for name, _ in MALFORMED}


# --- the shipped rubric ------------------------------------------------------


def test_the_shipped_rubric_is_valid() -> None:
    ok, message = check(SHIPPED)
    assert ok, message


def test_the_shipped_rubric_has_the_shape_the_role_needs() -> None:
    rubric = load_rubric(parse(SHIPPED), source=SHIPPED.name)
    kinds = [criterion.kind for criterion in rubric.criteria]

    assert 8 <= len(rubric.criteria) <= 10
    assert kinds.count(CriterionKind.BLOCKER) >= 1
    assert kinds.count(CriterionKind.HIGH_STAKES) >= 2


def test_the_shipped_rubric_forbids_the_protected_attributes() -> None:
    rubric = load_rubric(parse(SHIPPED), source=SHIPPED.name)

    for attribute in ("age", "gender", "nationality", "ethnicity", "religion", "disability"):
        assert attribute in rubric.forbidden_attributes


def test_the_shipped_rubric_defaults_to_blind_review() -> None:
    assert load_rubric(parse(SHIPPED), source=SHIPPED.name).blind_mode_default is True


def test_every_criterion_asks_a_question_a_document_could_answer() -> None:
    """A criterion whose question invites inference rather than quotation would
    push the model toward exactly what the forbidden-attribute list bans."""
    rubric = load_rubric(parse(SHIPPED), source=SHIPPED.name)

    for criterion in rubric.criteria:
        assert criterion.question.strip().lower().startswith("does the document"), criterion.id


# --- hashing -----------------------------------------------------------------


def test_the_hash_is_stable_across_reformatting() -> None:
    """Reindenting a YAML file must not invalidate every past run."""
    original = parse(SHIPPED)
    reformatted = yaml.safe_load(yaml.safe_dump(original, default_flow_style=True))

    assert rubric_hash(load_rubric(original)) == rubric_hash(load_rubric(reformatted))


def test_the_hash_changes_when_a_weight_changes() -> None:
    """A run's hash is what binds it to the rules that produced it."""
    data = parse(SHIPPED)
    before = rubric_hash(load_rubric(data))

    data["criteria"][0]["weight"] = 1
    assert rubric_hash(load_rubric(data)) != before


def test_the_hash_changes_when_a_band_cutoff_moves() -> None:
    data = parse(SHIPPED)
    before = rubric_hash(load_rubric(data))

    data["bands"][0]["min_score"] = 0.8
    assert rubric_hash(load_rubric(data)) != before


def test_the_hash_is_a_full_sha256() -> None:
    assert len(rubric_hash(load_rubric(parse(SHIPPED)))) == 64


# --- failure handling --------------------------------------------------------


def test_broken_yaml_is_reported_as_a_rubric_problem(tmp_path: Path) -> None:
    """To the person editing the file, a syntax error is the same kind of
    problem as a validation error, and should read like one."""
    broken = tmp_path / "broken.yaml"
    broken.write_text("role_id: [unclosed\n", encoding="utf-8")

    with pytest.raises(RubricInvalid, match="could not be parsed as YAML"):
        parse(broken)


def test_a_non_mapping_file_is_rejected(tmp_path: Path) -> None:
    listy = tmp_path / "listy.yaml"
    listy.write_text("- one\n- two\n", encoding="utf-8")

    with pytest.raises(RubricInvalid, match="mapping at the top level"):
        parse(listy)


def test_the_error_carries_every_problem_not_just_the_first() -> None:
    """Fixing one error at a time through six lint runs is how people give up
    on editing a file themselves."""
    with pytest.raises(RubricInvalid) as caught:
        load_rubric({"role_id": "x", "role_title": "y"}, source="tiny.yaml")

    assert len(caught.value.problems) >= 2


def test_the_editor_schema_matches_the_contract() -> None:
    """The schema an editor validates against is the schema the pipeline
    enforces, so the two cannot drift."""
    committed = json.loads((REPO_ROOT / "rubrics" / "_schema.json").read_text(encoding="utf-8"))
    assert committed == RoleRubric.model_json_schema(mode="serialization")
