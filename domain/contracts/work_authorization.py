"""Work authorisation, kept deliberately apart.

This is not on ``CandidateProfile``, and that separation is the whole design.

Eligibility to work is a legitimate requirement and a legitimate blocker. It is
also adjacent to nationality, immigration status, and family circumstances,
which this system will not infer and must not be nudged toward. Holding it on
the profile would put it in front of the model during every criterion
assessment, where it could colour a judgment about someone's engineering
experience.

So it lives here, in its own record, extracted separately, never passed to an
assessment call, and revealed in the interface only after the recommendation has
been rendered. A test asserts it never reaches an assessment prompt.
"""

from __future__ import annotations

from enum import Enum
from uuid import UUID

from pydantic import Field, model_validator

from domain.contracts.base import Contract
from domain.contracts.source_text import Provenance


class AuthorizationStatement(str, Enum):
    """What the document says, not what it implies.

    ``NOT_STATED`` is the common case and is not a finding. Most CVs do not
    mention work authorisation, and treating silence as a problem would decline
    people for the shape of their CV rather than for anything about them.
    """

    STATED_ELIGIBLE = "stated_eligible"
    STATED_REQUIRES_SPONSORSHIP = "stated_requires_sponsorship"
    NOT_STATED = "not_stated"


class WorkAuthorizationNote(Contract):
    """What a document stated about eligibility to work, and where.

    Quoted rather than summarised, so a reviewer reads the candidate's own words
    on a subject where a paraphrase could be badly wrong.
    """

    candidate_id: str = Field(min_length=1)
    statement: AuthorizationStatement
    verbatim_span: str | None = Field(default=None, min_length=4, max_length=400)
    provenance: Provenance | None = None
    document_id: UUID | None = None

    @model_validator(mode="after")
    def _a_statement_is_quoted(self) -> WorkAuthorizationNote:
        """Anything but silence must cite the words it came from.

        This subject is close enough to protected ground that an unquoted claim
        would be indefensible, so the same rule that governs evidence governs
        this too.
        """
        if self.statement is not AuthorizationStatement.NOT_STATED and not self.verbatim_span:
            raise ValueError(
                "a statement about work authorisation must quote the document it came from"
            )
        if self.statement is AuthorizationStatement.NOT_STATED and self.verbatim_span:
            raise ValueError("not_stated records the absence of a statement, so it quotes nothing")
        return self
