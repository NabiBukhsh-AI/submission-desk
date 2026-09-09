"""Prompts are files, and the files must declare what they are.

A prompt with an undeclared placeholder gets sent with a literal brace in it,
and the answer looks plausible enough that nobody notices for a while. A prompt
with no version cannot be pointed at when a result is questioned. A prompt whose
declared schema does not exist fails at the moment it is needed rather than at
the moment it was written.

All three are checked here, at load, so a prompt file that is wrong is wrong at
startup and not during a demo.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from domain.contracts import EXPORTED_MODELS
from infrastructure.prompts.registry import (
    REQUIRED_FIELDS,
    PromptInvalid,
    PromptRegistry,
    parse,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
PROMPT_DIR = REPO_ROOT / "prompts"

VALID_PROMPT = """---
id: assessment/example
version: 3
purpose: An example prompt for the tests.
inputs: [criterion_label]
output_schema: AssessmentResponse
invariants:
  - "Quotes the document character for character"
forbidden:
  - "Emitting a score"
---

Find text supporting {criterion_label}.
"""


def write(tmp_path: Path, body: str, name: str = "example.md") -> Path:
    path = tmp_path / name
    path.write_text(body, encoding="utf-8")
    return path


# --- the shipped prompts -----------------------------------------------------------


def test_every_shipped_prompt_loads() -> None:
    registry = PromptRegistry.load(PROMPT_DIR)

    assert registry.prompts


@pytest.mark.parametrize("field_name", REQUIRED_FIELDS)
def test_every_shipped_prompt_declares_every_field(field_name: str) -> None:
    for prompt in PromptRegistry.load(PROMPT_DIR).prompts.values():
        assert getattr(prompt, field_name.replace("output_schema", "output_schema")) is not None


def test_every_shipped_prompt_names_a_real_schema_or_none() -> None:
    """A prompt declaring a schema that does not exist fails when it is needed
    rather than when it was written."""
    known = {model.__name__ for model in EXPORTED_MODELS} | {"none"}

    for prompt in PromptRegistry.load(PROMPT_DIR).prompts.values():
        assert prompt.output_schema in known, f"{prompt.id} names {prompt.output_schema}"


def test_every_shipped_prompt_says_what_it_may_never_do() -> None:
    """The forbidden list is where "never emit a score" is written down next to
    the prompt it governs, rather than in a document nobody opens."""
    for prompt in PromptRegistry.load(PROMPT_DIR).prompts.values():
        assert prompt.forbidden, prompt.id


def test_the_repair_prompt_does_not_ask_for_invention() -> None:
    """A required field the model cannot support must be recorded as absent, not
    filled in. This is the one prompt where the temptation is built in."""
    prompt = PromptRegistry.load(PROMPT_DIR).get("repair/schema_repair")

    assert "invent" in prompt.body.lower() or "fabricat" in prompt.body.lower()
    assert "do not" in prompt.body.lower()


# --- validation at load ---------------------------------------------------------------


def test_a_valid_prompt_parses(tmp_path: Path) -> None:
    prompt = parse(write(tmp_path, VALID_PROMPT))

    assert prompt.versioned_id == "assessment/example@3"
    assert prompt.inputs == ("criterion_label",)


@pytest.mark.parametrize("missing", ["id", "version", "purpose", "output_schema"])
def test_a_missing_field_is_rejected(tmp_path: Path, missing: str) -> None:
    lines = [line for line in VALID_PROMPT.splitlines() if not line.startswith(f"{missing}:")]

    with pytest.raises(PromptInvalid, match=missing):
        parse(write(tmp_path, "\n".join(lines)))


def test_an_undeclared_placeholder_is_rejected(tmp_path: Path) -> None:
    """Otherwise the prompt is sent with a literal brace and the answer looks
    plausible enough that nobody notices."""
    body = VALID_PROMPT.replace(
        "Find text supporting {criterion_label}.",
        "Find text supporting {criterion_label} for {undeclared_thing}.",
    )

    with pytest.raises(PromptInvalid, match="undeclared_thing"):
        parse(write(tmp_path, body))


def test_an_unused_input_is_rejected(tmp_path: Path) -> None:
    """A declaration documenting something the prompt does not use is a
    declaration that has drifted from its body."""
    body = VALID_PROMPT.replace(
        "inputs: [criterion_label]", "inputs: [criterion_label, never_used]"
    )

    with pytest.raises(PromptInvalid, match="never_used"):
        parse(write(tmp_path, body))


def test_an_empty_forbidden_list_is_rejected(tmp_path: Path) -> None:
    body = VALID_PROMPT.replace('forbidden:\n  - "Emitting a score"', "forbidden: []")

    with pytest.raises(PromptInvalid, match="forbidden"):
        parse(write(tmp_path, body))


def test_a_file_without_front_matter_is_rejected(tmp_path: Path) -> None:
    with pytest.raises(PromptInvalid, match="front matter"):
        parse(write(tmp_path, "Just a prompt with no declaration.\n"))


def test_unclosed_front_matter_is_rejected(tmp_path: Path) -> None:
    with pytest.raises(PromptInvalid, match="not closed"):
        parse(write(tmp_path, "---\nid: x\nversion: 1\n"))


def test_broken_yaml_is_reported_as_a_prompt_problem(tmp_path: Path) -> None:
    with pytest.raises(PromptInvalid, match="not valid YAML"):
        parse(write(tmp_path, "---\nid: [unclosed\n---\nbody\n"))


def test_two_prompts_cannot_share_an_id(tmp_path: Path) -> None:
    write(tmp_path, VALID_PROMPT, "first.md")
    write(tmp_path, VALID_PROMPT, "second.md")

    with pytest.raises(PromptInvalid, match="duplicate prompt id"):
        PromptRegistry.load(tmp_path)


# --- rendering -------------------------------------------------------------------------


def test_rendering_fills_the_placeholders(tmp_path: Path) -> None:
    prompt = parse(write(tmp_path, VALID_PROMPT))

    assert prompt.render(criterion_label="evaluation practice") == (
        "Find text supporting evaluation practice."
    )


def test_rendering_without_an_input_is_refused(tmp_path: Path) -> None:
    """Better than sending a prompt with a brace in it."""
    prompt = parse(write(tmp_path, VALID_PROMPT))

    with pytest.raises(PromptInvalid, match="missing input"):
        prompt.render()


# --- the bundle hash --------------------------------------------------------------------


def test_the_bundle_hash_is_stable(tmp_path: Path) -> None:
    write(tmp_path, VALID_PROMPT)

    assert PromptRegistry.load(tmp_path).bundle_hash == PromptRegistry.load(tmp_path).bundle_hash


def test_editing_a_prompt_changes_the_bundle_hash(tmp_path: Path) -> None:
    """This is the chain that makes an evaluation honest: the hash goes into the
    content key, so a prompt edit invalidates the cache and forces a real run
    rather than comparing a change against its own old results."""
    write(tmp_path, VALID_PROMPT)
    before = PromptRegistry.load(tmp_path).bundle_hash

    write(tmp_path, VALID_PROMPT.replace("Find text", "Locate text"))

    assert PromptRegistry.load(tmp_path).bundle_hash != before


def test_bumping_a_version_changes_the_bundle_hash(tmp_path: Path) -> None:
    write(tmp_path, VALID_PROMPT)
    before = PromptRegistry.load(tmp_path).bundle_hash

    write(tmp_path, VALID_PROMPT.replace("version: 3", "version: 4"))

    assert PromptRegistry.load(tmp_path).bundle_hash != before


def test_the_versions_map_records_what_produced_each_result(tmp_path: Path) -> None:
    """Lands on every piece of evidence, so a questioned result can be traced to
    the exact prompt that produced it."""
    write(tmp_path, VALID_PROMPT)

    assert PromptRegistry.load(tmp_path).versions == {"assessment/example": "assessment/example@3"}


def test_asking_for_an_unknown_prompt_says_what_is_known(tmp_path: Path) -> None:
    write(tmp_path, VALID_PROMPT)

    with pytest.raises(KeyError, match="no prompt named"):
        PromptRegistry.load(tmp_path).get("nope/missing")
