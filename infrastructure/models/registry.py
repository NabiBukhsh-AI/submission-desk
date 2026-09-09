"""Every call to a language model, declared in one place.

Five call sites. That is the entire probabilistic surface of the system, and a
test asserts nothing calls a model at a site not listed here.

The absences matter as much as the entries. There is no `score`, no `rank`, no
`judge`, no `decide`, no `recommend`, and no `compare_candidates`. A model is
asked to find and quote text, and to phrase a gap as a question. It is never
asked how good a candidate is, and this file is where that claim is checkable
rather than asserted.
"""

from __future__ import annotations

from dataclasses import dataclass

from domain.contracts.enums import ModelTier
from domain.contracts.profile import CandidateProfile
from domain.contracts.responses import AssessmentResponse, CompositionResponse, InjectionVerdict


@dataclass(frozen=True)
class CallSpec:
    """One declared reason to call a model.

    ``why_a_model`` is required and is prose. A call site that cannot justify
    itself in a sentence is a call site that should be deterministic code, and
    forcing the sentence at declaration time is cheaper than discovering it in
    review.
    """

    call_site: str
    why_a_model: str
    output_schema: type
    default_tier: ModelTier
    temperature: float
    max_repairs: int
    may_escalate: bool
    #: What happens when every attempt fails. Never an exception reaching a
    #: recruiter; always a state with a sentence.
    on_failure: str


#: Temperature 0 wherever the answer should be the same twice. Changing one of
#: these requires an evaluation run showing the effect, because reproducibility
#: is what the routing, calibration, and fairness comparisons rest on.
CALL_REGISTRY: dict[str, CallSpec] = {
    "structure.profile": CallSpec(
        call_site="structure.profile",
        why_a_model=(
            "Turning free-form prose into structured employment history requires "
            "semantic reading. A regular expression cannot resolve 'led the platform "
            "team from spring 2022' into a role, an employer, and a start date."
        ),
        output_schema=CandidateProfile,
        default_tier=ModelTier.CHEAP,
        temperature=0.0,
        max_repairs=1,
        may_escalate=True,
        on_failure="a partial profile, the run flagged for review",
    ),
    "assess.criterion": CallSpec(
        call_site="assess.criterion",
        why_a_model=(
            "Deciding whether a passage supports a criterion is genuine semantic "
            "judgment, and it is the only defensible use of a model here. Note what "
            "is being asked: find and quote text, not rate the candidate."
        ),
        output_schema=AssessmentResponse,
        default_tier=ModelTier.CHEAP,
        temperature=0.0,
        max_repairs=1,
        may_escalate=True,
        on_failure="insufficient_evidence, with the reason recorded",
    ),
    "compose.questions": CallSpec(
        call_site="compose.questions",
        why_a_model=(
            "Phrasing a gap as a question a recruiter can send is a writing task. "
            "Which gaps exist is decided deterministically before this call, so the "
            "model cannot invent one."
        ),
        output_schema=CompositionResponse,
        default_tier=ModelTier.CHEAP,
        temperature=0.2,
        max_repairs=1,
        may_escalate=False,
        on_failure="template questions built from the criterion definitions",
    ),
    "sanitize.classify": CallSpec(
        call_site="sanitize.classify",
        why_a_model=(
            "A second opinion on an excerpt the deterministic detectors found "
            "ambiguous. The detectors are primary; this only ever raises a "
            "severity and can never lower one."
        ),
        output_schema=InjectionVerdict,
        default_tier=ModelTier.CHEAP,
        temperature=0.0,
        max_repairs=1,
        may_escalate=False,
        on_failure="the detectors stand alone, classifier_unavailable recorded",
    ),
    "calibrate.embed": CallSpec(
        call_site="calibrate.embed",
        why_a_model=(
            "Semantic similarity over anonymised profile summaries, to retrieve "
            "two or three past decisions as anchors."
        ),
        output_schema=type(None),
        default_tier=ModelTier.CHEAP,
        temperature=0.0,
        max_repairs=0,
        may_escalate=False,
        on_failure="calibration skipped, the run continues without anchors",
    ),
}

#: Names that would describe a model deciding an outcome. A call site matching
#: one of these is a design error, and the test that checks it is not
#: negotiable: the whole architecture rests on the model never being asked.
FORBIDDEN_CALL_SITE_WORDS = frozenset(
    {
        "score",
        "rank",
        "rate",
        "grade",
        "judge",
        "decide",
        "recommend",
        "verdict",
        "compare",
        "band",
        "shortlist",
        "reject",
        "hire",
    }
)


def spec_for(call_site: str) -> CallSpec:
    """The declaration for a call site, or an error naming the omission."""
    try:
        return CALL_REGISTRY[call_site]
    except KeyError:
        raise KeyError(
            f"{call_site!r} is not a declared call site. Every call to a model is "
            f"declared in infrastructure/models/registry.py, with a sentence saying "
            f"why a model is needed. Known sites: {', '.join(sorted(CALL_REGISTRY))}"
        ) from None
