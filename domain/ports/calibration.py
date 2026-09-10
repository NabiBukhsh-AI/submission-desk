"""Past decisions, offered as anchors and never as evidence.

Retrieval exists here for one purpose: showing the assessment step two or three
previously decided candidates for the same role, with the reasons a recruiter
recorded. It is not a knowledge base and it is not decoration.

Three properties define the whole design, and each is a refusal.

A card cannot be cited. It reaches the model in a block whose kind is
CALIBRATION, the prompt says the text is reference-only, and — because a prompt
is a request rather than a control — the span validator resolves every quotation
against the candidate's own documents. A quotation drawn from a card resolves
nowhere and is rejected as INVALID_WRONG_DOCUMENT.

A card cannot come from unreviewed output. Cards are written in DELIVER, only
from an APPROVE decision, which means a person read the run and agreed with it.
Anything else would be the system learning from its own opinions.

Calibration is off by default. It is a feature that plausibly helps and has not
been measured, and the honest place for such a thing is behind a flag with an
experiment attached.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any, Protocol
from uuid import UUID

from domain.contracts.calibration import CalibrationCard

#: How many anchors are offered. Three, because two is not a range and four
#: starts to read like a pattern the model should match rather than a reference.
DEFAULT_TOP_K = 3

#: How old a decision may be before it stops being a useful anchor. Roles drift,
#: rubrics change, and a decision from last year was made against a different
#: bar.
DEFAULT_STALENESS_DAYS = 180


def states_rendering(states: dict[str, Any]) -> str:
    """The outcome as text, for the embedding.

    Included because two candidates with similar histories and different
    outcomes are exactly the pair a reviewer wants to see side by side, and an
    embedding of the history alone could not tell them apart.

    Sorted, so the same states always produce the same string on any machine.
    That determinism is what makes a similarity score in a report checkable.
    """
    return " ".join(
        f"{criterion_id}={getattr(state, 'value', state)}"
        for criterion_id, state in sorted(states.items())
    )


def embedding_text(summary: str, states: dict[str, Any]) -> str:
    """What is actually embedded.

    The summary and the outcome. Never the reviewer's free text: that is a
    person's own words about a specific candidate, and embedding it would make
    the index searchable by things a reviewer wrote in confidence.

    It lives in the port because both sides need it and neither owns it — the
    index builds a query vector with it, and the card builder embeds a new card
    with it, and the two must agree exactly or every stored vector is compared
    against a differently-shaped question.
    """
    return f"{summary}\n\n{states_rendering(states)}"


class CalibrationStatus(str, Enum):
    """What calibration did, recorded on every run.

    A status rather than a boolean, because "off", "nothing to show" and "it
    broke" are three different facts about a run, and a reader comparing two
    results needs to know which one applies.
    """

    DISABLED = "disabled"
    APPLIED = "applied"
    EMPTY_CORPUS = "empty_corpus"
    NO_MATCHES = "no_matches"
    EMBEDDING_FAILED = "embedding_failed"
    INDEX_UNAVAILABLE = "index_unavailable"

    @property
    def anchored(self) -> bool:
        return self is CalibrationStatus.APPLIED


@dataclass(frozen=True)
class CalibrationQuery:
    """What to look for, with the filters that must be applied first.

    Filters before similarity, always. A nearest-neighbour search that then
    discards the wrong role is a search that can return nothing when the corpus
    is small, and worse, one whose behaviour changes with corpus size.
    """

    role_id: str
    rubric_version: str
    summary: str
    criterion_states: dict[str, str]
    #: Never itself. A run finding its own card would be a system agreeing with
    #: a decision nobody had made yet.
    exclude_run_id: UUID | None = None
    top_k: int = DEFAULT_TOP_K
    staleness_days: int = DEFAULT_STALENESS_DAYS


@dataclass(frozen=True)
class CalibrationMatch:
    """One anchor, and how close it was."""

    card: CalibrationCard
    similarity: float


@dataclass(frozen=True)
class CalibrationResult:
    """What the search produced, and what it had to say about it."""

    status: CalibrationStatus
    matches: tuple[CalibrationMatch, ...] = ()
    #: How many cards existed before filtering, and how many each filter removed.
    #: Reported because "no matches" and "no corpus" look the same to a reader
    #: and mean different things to somebody deciding whether this is worth
    #: keeping.
    considered: int = 0
    excluded_stale: int = 0
    diversity_applied: bool = False

    @property
    def bands(self) -> tuple[str, ...]:
        return tuple(match.card.final_band.value for match in self.matches)


class CalibrationIndex(Protocol):
    """Finds past decisions like this one."""

    def search(self, query: CalibrationQuery) -> CalibrationResult:
        """The closest approved decisions for this role.

        Never raises. Every failure path produces a status and no anchors, so a
        calibration problem degrades an assessment rather than failing it: the
        run proceeds without anchors and says so.
        """
        ...
