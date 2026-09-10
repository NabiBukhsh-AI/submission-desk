"""Reading the persona file, and refusing to let it vary more than one thing.

The personas are configuration. This module loads them and enforces the single
rule that makes a comparison interpretable: two variants of the same CV may
differ in identity tokens and in nothing else.

A persona that also changed a university, a city, or a job title would be
testing two things at once, and a flip could not be attributed to either. The
check lives here rather than in a review comment because the failure is silent:
the harness would run, produce numbers, and measure something nobody intended.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import yaml

DEFAULT_FILE = Path(__file__).with_name("personas.yaml")


@dataclass(frozen=True)
class Persona:
    """One identity, and nothing else about a person."""

    id: str
    name: str
    pronoun: str
    email: str
    note: str = ""

    @property
    def tokens(self) -> dict[str, str]:
        """Everything this persona substitutes, by placeholder."""
        return {
            "{{NAME}}": self.name,
            "{{PRONOUN}}": self.pronoun,
            "{{PRONOUN_OBJECT}}": _object_form(self.pronoun),
            "{{PRONOUN_POSSESSIVE}}": _possessive_form(self.pronoun),
            "{{EMAIL}}": f"{self.email}@example.com",
        }

    @property
    def identity_words(self) -> set[str]:
        """Every word this persona contributes, lowercased.

        Used to subtract identity from a document before comparing two variants:
        what remains must be identical, and this is the set that defines what
        "identity" means for that check.
        """
        words = {part.lower() for part in self.name.replace("-", " ").split() if part}
        words |= {value.lower() for value in self.tokens.values()}
        words |= {self.email.lower(), self.pronoun.lower()}
        words |= {_object_form(self.pronoun).lower(), _possessive_form(self.pronoun).lower()}
        return {word.strip(".,").lower() for word in words if word}


#: Pronoun forms. A CV written in the third person needs all three, and getting
#: one wrong would introduce a grammatical difference between variants — which
#: is a difference, and would make the comparison measure something else.
OBJECT_FORMS = {"she": "her", "he": "him", "they": "them"}
POSSESSIVE_FORMS = {"she": "her", "he": "his", "they": "their"}


def _object_form(pronoun: str) -> str:
    return OBJECT_FORMS.get(pronoun.lower(), "them")


def _possessive_form(pronoun: str) -> str:
    return POSSESSIVE_FORMS.get(pronoun.lower(), "their")


def load(path: Path | str = DEFAULT_FILE) -> list[Persona]:
    """Every persona in the file.

    Raises on an empty file rather than returning an empty list, because a
    fairness run with no personas would produce a flip rate of zero over zero
    comparisons and look like a clean result.
    """
    data = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
    entries = data.get("personas") or []

    if not entries:
        raise ValueError(
            f"{path} declares no personas. A fairness run with none would "
            "produce a rate over zero comparisons and read as a clean result."
        )

    return [
        Persona(
            id=str(entry["id"]),
            name=str(entry["name"]),
            pronoun=str(entry["pronoun"]),
            email=str(entry["email"]),
            note=str(entry.get("note", "")),
        )
        for entry in entries
    ]


def reference(personas: list[Persona]) -> Persona:
    """The persona everything else is compared against.

    The first, which the file documents as deliberately unmarked. Comparing
    every persona against a single reference rather than against each other
    keeps the number of comparisons linear and the attribution clear: a flip is
    a flip relative to a name that carries little signal.
    """
    return personas[0]
