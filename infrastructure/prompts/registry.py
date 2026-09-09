"""Prompts as files, versioned and hashed into every run.

A prompt inlined in Python is a prompt nobody can diff, nobody can review
without reading code, and nobody can change without a deployment. These live in
``prompts/**.md`` with front matter declaring what they take, what schema they
fill, and what they must never do.

The bundle hash is what makes the evaluation honest. It goes into the content
key, so changing a prompt changes the key, which invalidates the cache and
forces a real run. Without that chain, "we improved the prompt" would compare a
change against its own cached results.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from domain.errors import SubmissionDeskError
from domain.ports.models import placeholders_in

#: Every prompt must declare all of these. A missing field is an error rather
#: than a default, because each one records a decision someone made.
REQUIRED_FIELDS = ("id", "version", "purpose", "inputs", "output_schema", "invariants", "forbidden")


class PromptInvalid(SubmissionDeskError):
    """A prompt file is missing something it must declare."""

    error_code = "PROMPT_INVALID"

    def __init__(self, source: str, problems: list[str]) -> None:
        self.source = source
        self.problems = problems
        listed = "\n".join(f"  - {problem}" for problem in problems)
        super().__init__(f"{source} is not a valid prompt:\n{listed}")


@dataclass(frozen=True)
class Prompt:
    """One prompt file: its declaration and its body."""

    id: str
    version: int
    purpose: str
    inputs: tuple[str, ...]
    output_schema: str
    invariants: tuple[str, ...]
    forbidden: tuple[str, ...]
    body: str
    path: Path

    @property
    def versioned_id(self) -> str:
        """What lands on every piece of evidence this prompt produced."""
        return f"{self.id}@{self.version}"

    @property
    def content_hash(self) -> str:
        return hashlib.sha256(self.body.encode("utf-8")).hexdigest()

    def render(self, **values: Any) -> str:
        """Fill the placeholders, refusing if any are missing.

        A prompt sent with an unfilled ``{criterion_question}`` would ask the
        model about a literal brace, and the answer would look plausible enough
        that nobody would notice for a while.
        """
        missing = set(self.inputs) - set(values)
        if missing:
            raise PromptInvalid(
                self.versioned_id, [f"missing input: {name}" for name in sorted(missing)]
            )
        return self.body.format(**values)


def parse(path: Path) -> Prompt:
    """Read one prompt file, validating its declaration."""
    text = path.read_text(encoding="utf-8")
    problems: list[str] = []

    if not text.startswith("---"):
        raise PromptInvalid(path.name, ["the file must begin with YAML front matter"])

    _, _, remainder = text.partition("---")
    front_matter, separator, body = remainder.partition("---")
    if not separator:
        raise PromptInvalid(path.name, ["the front matter is not closed with ---"])

    try:
        declared = yaml.safe_load(front_matter) or {}
    except yaml.YAMLError as error:
        raise PromptInvalid(path.name, [f"the front matter is not valid YAML: {error}"]) from error

    for field_name in REQUIRED_FIELDS:
        if field_name not in declared:
            problems.append(f"missing required field: {field_name}")

    if problems:
        raise PromptInvalid(path.name, problems)

    inputs = tuple(declared.get("inputs") or ())
    body = body.strip()

    # Every placeholder must be declared, or a caller has no way to know what to
    # supply, and every declared input should appear, or the declaration is
    # documenting something the prompt does not use.
    used = placeholders_in(body)
    undeclared = used - set(inputs)
    unused = set(inputs) - used

    if undeclared:
        problems.append(f"body uses placeholders not in inputs: {', '.join(sorted(undeclared))}")
    if unused:
        problems.append(f"inputs declared but never used: {', '.join(sorted(unused))}")
    if not declared.get("forbidden"):
        problems.append("forbidden must list at least one thing this prompt may never do")

    if problems:
        raise PromptInvalid(path.name, problems)

    return Prompt(
        id=str(declared["id"]),
        version=int(declared["version"]),
        purpose=str(declared["purpose"]),
        inputs=inputs,
        output_schema=str(declared["output_schema"]),
        invariants=tuple(declared.get("invariants") or ()),
        forbidden=tuple(declared.get("forbidden") or ()),
        body=body,
        path=path,
    )


@dataclass
class PromptRegistry:
    """Every prompt, loaded once at startup."""

    prompts: dict[str, Prompt]

    @classmethod
    def load(cls, root: Path) -> PromptRegistry:
        found: dict[str, Prompt] = {}
        for path in sorted(root.rglob("*.md")):
            prompt = parse(path)
            if prompt.id in found:
                raise PromptInvalid(path.name, [f"duplicate prompt id: {prompt.id}"])
            found[prompt.id] = prompt
        return cls(prompts=found)

    def get(self, prompt_id: str) -> Prompt:
        try:
            return self.prompts[prompt_id]
        except KeyError:
            raise KeyError(
                f"no prompt named {prompt_id!r}. Known: {', '.join(sorted(self.prompts))}"
            ) from None

    @property
    def bundle_hash(self) -> str:
        """A fingerprint of every prompt, in id order.

        Recorded on every run and part of the content key, so a prompt edit
        invalidates cached responses instead of silently comparing a change
        against its own old results.
        """
        digest = hashlib.sha256()
        for prompt_id in sorted(self.prompts):
            prompt = self.prompts[prompt_id]
            digest.update(f"{prompt.versioned_id}:{prompt.content_hash}".encode())
        return digest.hexdigest()

    @property
    def versions(self) -> dict[str, str]:
        """Prompt id to versioned id, for the run record."""
        return {prompt.id: prompt.versioned_id for prompt in self.prompts.values()}
