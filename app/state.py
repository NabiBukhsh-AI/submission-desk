"""Session state, as one typed object.

Streamlit's ``st.session_state`` is a dictionary with no schema, and a page that
writes ``st.session_state["reviewr_id"]`` fails silently at the far end of a
workflow. So the whole session is one frozen dataclass under a single key, and
every page reads and replaces it as a value.

The dataclass is frozen for the same reason the contracts are: a page that
mutated shared state in place would make the order pages ran in part of the
behaviour, and Streamlit reruns a script on every interaction.

Nothing here is business logic. This is a place to keep what the person is
currently looking at, plus the timer, which has to live across reruns because a
rerun is what happens every time they click anything.
"""

from __future__ import annotations

import os
import time
from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from typing import Any, Protocol
from uuid import UUID

#: The one key. Anything else in st.session_state is Streamlit's own.
SESSION_KEY = "submission_desk"


@dataclass(frozen=True)
class UploadDraft:
    """Files a recruiter has chosen but not yet confirmed.

    Grouping is a guess — filenames are how people name things, not how systems
    identify them — so the recruiter confirms or corrects it before anything is
    processed. Holding the guess here is what makes that correction possible.
    """

    #: candidate id -> [(filename, bytes)]
    grouped: dict[str, list[tuple[str, bytes]]] = field(default_factory=dict)
    role_id: str = ""

    @property
    def candidate_count(self) -> int:
        return len(self.grouped)

    @property
    def document_count(self) -> int:
        return sum(len(files) for files in self.grouped.values())


@dataclass(frozen=True)
class ReviewSession:
    """One candidate open in front of one person.

    ``started_at`` and ``run_version`` are the two fields that must survive a
    rerun. The timer measures what a review actually costs, which is one of the
    two numbers the whole evaluation rests on; the version is what makes a lost
    update visible rather than silent.
    """

    run_id: UUID | None = None
    run_version: int = 0
    started_at: float = 0.0
    #: criterion_id -> proposed new state, before the reviewer submits.
    pending_overrides: dict[str, Any] = field(default_factory=dict)
    trust_rating: int | None = None
    comments: str = ""

    @property
    def elapsed_seconds(self) -> int:
        if not self.started_at:
            return 0
        return int(time.monotonic() - self.started_at)

    def opened(self, run_id: UUID, version: int) -> ReviewSession:
        """Start the clock on a candidate, discarding any previous draft."""
        return ReviewSession(run_id=run_id, run_version=version, started_at=time.monotonic())


@dataclass(frozen=True)
class SessionState:
    """Everything the interface remembers between clicks."""

    reviewer_id: str = ""
    role_id: str = "ai-engineer"
    upload: UploadDraft = field(default_factory=UploadDraft)
    review: ReviewSession = field(default_factory=ReviewSession)
    #: Filters on the queue page, kept so a refresh does not lose them.
    queue_filter: str = "Needs attention"
    #: A sentence to show once at the top of the next page render.
    notice: str = ""
    debug: bool = False

    def with_notice(self, message: str) -> SessionState:
        return replace(self, notice=message)

    def cleared(self) -> SessionState:
        return replace(self, notice="")


class SessionStore(Protocol):
    """What the state needs from wherever it is kept.

    Streamlit's session proxy is one; a plain dict in a test is another. Named
    as a protocol because the proxy's type stubs do not declare the mapping
    interface it implements at runtime.
    """

    def __contains__(self, key: object) -> bool: ...

    def __getitem__(self, key: str) -> Any: ...

    def __setitem__(self, key: str, value: Any) -> None: ...


def initial(query: Mapping[str, Any] | None = None) -> SessionState:
    """The state a fresh session starts in.

    The reviewer id comes from the environment, never from a text box. A
    decision has to be attributable, and a name somebody typed into a browser is
    not an attribution.
    """
    params = query or {}
    return SessionState(
        reviewer_id=os.environ.get("REVIEWER_ID", ""),
        # ?debug=1 reveals raw payloads. Off unless asked for, and absent from
        # the recruiter guide, because a recruiter who finds it will reasonably
        # conclude the rest of the interface was hiding something.
        debug=str(params.get("debug", "")) in ("1", "true", "True"),
    )


def get(session: SessionStore, query: Mapping[str, Any] | None = None) -> SessionState:
    """Read the session, creating it on first use.

    Takes the store as an argument rather than importing Streamlit, so the state
    module is testable without a browser and the architecture test can assert
    that app/ holds no logic worth hiding behind a framework.
    """
    # Item access rather than .get(): the protocol asks for the least a store
    # can offer, and Streamlit's proxy types .get() in a way a dict does not.
    current = session[SESSION_KEY] if SESSION_KEY in session else None  # noqa: SIM401
    if not isinstance(current, SessionState):
        current = initial(query)
        session[SESSION_KEY] = current
    return current


def put(session: SessionStore, state: SessionState) -> SessionState:
    session[SESSION_KEY] = state
    return state
