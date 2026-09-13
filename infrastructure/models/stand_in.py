"""A deterministic stand-in for a model, so the system can be run by anybody.

What this is for, stated plainly because it is the easiest thing in the whole
repository to misread.

The offline system — the demo, the test suite, arm C of the evaluation —
exercises the *pipeline*: span validation, the rule engine, the coverage gate,
abstention, the integrity path. Those are deterministic, and running them needs
a model-shaped thing that answers the same way every time. This is that thing.
It finds sentences in the document that match a criterion's own example phrases
and quotes them verbatim.

It is not a model, and no number produced with it is a claim about a model. With
this stand-in, band accuracy measures whether the rule engine turns evidence
into the right band — not whether a language model can read a CV. The evaluation
report says so at the top of every run that used it, and every result it
returns is marked as unmeasured usage, because a reader who mistook these for
model-quality figures would be badly misled and the mistake would be ours.

The figures it *does* support are the interesting ones anyway: whether the
system abstains when there is nothing to find, whether a fabricated quotation is
rejected, whether an injected document is flagged and costs nothing. Those are
properties of the architecture, and the architecture is what is being assessed.

It lives in infrastructure because it is a model client: the composition root
wires it in behind the fixture-replaying fake, so a call with no recorded
fixture is answered from the document rather than refused. Set MODEL_PROVIDER
and the same pipeline runs against a real provider instead.

A known limitation, recorded rather than tuned away: it matches on shared words
and does not stem, so a document saying "I am eligible to work in the United
Kingdom" does not match a criterion asking about "eligibility". On the benchmark
that costs one band, and the case that loses it is dev-001. Adding a stemmer
would raise the number without making the harness measure anything more, which
is the wrong direction to move a benchmark in.
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Any

from domain.ports.models import BlockKind, GenerationRequest, GenerationResult, Usage
from infrastructure.models.repairing import validate_response

#: How much of a criterion's example phrase must appear before a sentence counts
#: as evidence for it.
#:
#: Set by looking at what it does to the corpus, not by taste. Too high and the
#: stand-in abstains on documents that plainly answer the question, which makes
#: the abstention column meaningless in the flattering direction. Too low and it
#: finds evidence in the keyword-stuffed CV, which makes it meaningless in the
#: other. This value abstains on the sparse and stuffed cases and finds evidence
#: in the explicit one, which is the behaviour the benchmark needs in order to
#: distinguish them.
MATCH_THRESHOLD = 0.5

#: A phrase this short says nothing distinctive, so matching on it would find
#: evidence everywhere.
MIN_PHRASE_TOKENS = 2

#: Below this, a fragment is a heading or a line break rather than a claim, and
#: quoting one would produce a span with no meaning to check.
MIN_SENTENCE_CHARS = 20

#: Tokens too common to carry signal. Matching on these would make every
#: sentence evidence for every criterion.
NOISE = frozenset({"a", "an", "and", "the", "to", "of", "in", "on", "for", "with", "at", "by", "n"})


@dataclass
class StandInModelClient:
    """Answers from the document, deterministically, or says it cannot.

    Deliberately literal. It quotes what is there and abstains when nothing
    matches, which is the behaviour the pipeline is built to handle correctly
    and the behaviour that makes the abstention and hallucination columns mean
    something.
    """

    tier_binding_hash: str = "stand-in"
    provider_id: str = "stand-in"
    calls: list[GenerationRequest] = field(default_factory=list)
    #: Every criterion, by the label the prompt names it with.
    criteria_by_label: dict[str, Any] = field(default_factory=dict)

    def structured_generate(self, request: GenerationRequest) -> GenerationResult:
        self.calls.append(request)

        if request.call_site == "structure.profile":
            raw = self._profile(request)
        elif request.call_site == "compose.questions":
            raw = json.dumps({"interview_questions": [], "information_requests": []})
        elif request.call_site == "sanitize.classify":
            raw = json.dumps(
                {
                    "is_instruction": True,
                    "severity": "high",
                    "rationale": "the deterministic detectors already found this",
                }
            )
        else:
            raw = self._assessment(request)

        parsed, error = validate_response(raw, request.response_schema)
        return GenerationResult(
            parsed=parsed,
            raw_text=raw,
            usage=Usage(len(raw) // 4, 64),
            tier=request.tier,
            latency_ms=1,
            validation_error=error,
            # The usage above is a size estimate, not a provider's count. Marked
            # so nothing downstream presents it as measured.
            metadata={"source": "stand-in", "usage_is_measured": False},
        )

    # --- structure -------------------------------------------------------------

    def _profile(self, request: GenerationRequest) -> str:
        """A profile built from the document's own lines.

        Only what is actually there. A stand-in that invented an employer would
        put a fabricated fact into the calibration index.
        """
        text = _document_text(request)
        roles = [
            line.strip()
            for line in text.splitlines()
            if re.search(r"\b(19|20)\d{2}\b", line) and "," in line
        ][:4]

        return json.dumps(
            {
                "partial": not roles,
                "employment": [
                    {
                        "employer": {"value": role.split(",")[0].strip()},
                        "title": {"value": role.split(",")[1].strip() if "," in role else ""},
                        "start": {"value": _first_year(role)},
                        "end": {"value": ""},
                        "summary": {"value": ""},
                    }
                    for role in roles
                ],
                "education": [],
                "technologies": [],
            }
        )

    # --- assessment ------------------------------------------------------------

    def _assessment(self, request: GenerationRequest) -> str:
        """Quote the document, or say it does not address the point."""
        criterion = self._criterion_for(request)
        text = _document_text(request)
        document_id = _document_id(request)

        if criterion is None or not text or document_id is None:
            return _absent("unknown")

        sentence = _best_sentence(text, criterion)
        if sentence is None:
            return _absent(criterion.id)

        start = text.find(sentence)
        return json.dumps(
            {
                "criterion_id": criterion.id,
                "evidence": [
                    {
                        "state": "supported",
                        "claim": f"The document says: {sentence[:80]}",
                        "verbatim_span": sentence,
                        "document_id": str(document_id),
                        "page_start": 1,
                        "page_end": 1,
                        "norm_start": max(start, 0),
                        "norm_end": max(start, 0) + len(sentence),
                        "confidence": 0.8,
                    }
                ],
            }
        )

    def _criterion_for(self, request: GenerationRequest) -> Any:
        """Which criterion this call is about, from the label in the prompt.

        The prompt names the criterion by its label rather than its id, because
        that is what a reader of the prompt needs. Matching on the label follows
        the prompt rather than reaching past it.
        """
        return next(
            (
                criterion
                for label, criterion in self.criteria_by_label.items()
                if label and label in request.system_prompt
            ),
            None,
        )


def _absent(criterion_id: str) -> str:
    return json.dumps(
        {
            "criterion_id": criterion_id,
            "evidence": [
                {
                    "state": "insufficient_evidence",
                    "claim": "The documents do not address this point.",
                    "confidence": 0.8,
                }
            ],
        }
    )


def _document_text(request: GenerationRequest) -> str:
    """The candidate's document, and never a calibration block.

    Reading a calibration block here would make the stand-in quote another
    candidate's history — which the span validator would then reject, producing
    a hallucination figure that measured the stand-in rather than the system.
    """

    return "\n".join(
        block.content for block in request.user_blocks if block.kind is BlockKind.DOCUMENT
    )


def _document_id(request: GenerationRequest) -> Any:
    return next((block.document_id for block in request.user_blocks if block.document_id), None)


def _first_year(text: str) -> str:
    match = re.search(r"\b(19|20)\d{2}\b", text)
    return match.group(0) if match else ""


#: Words that appear in most rubric questions and carry no signal about which
#: criterion is being asked about.
QUESTION_NOISE = frozenset(
    {
        "does",
        "document",
        "show",
        "describe",
        "contain",
        "candidate",
        "this",
        "their",
        "such",
        "person",
        "that",
        "was",
        "are",
        "is",
        "it",
        "they",
        "which",
        "what",
        "role",
        "requires",
        "including",
        "about",
    }
)


def _distinctive(question: str) -> str:
    """The words in a question that say which question it is."""
    words = [
        word
        for word in re.findall(r"[a-z]{3,}", question.lower())
        if word not in QUESTION_NOISE and word not in NOISE
    ]
    return " ".join(words[:6])


def _tokens(text: str) -> set[str]:
    return {word for word in re.findall(r"[a-z0-9]+", text.lower()) if word not in NOISE}


def _sentences(text: str) -> list[str]:
    parts = re.split(r"(?<=[.!?])\s+|\n", text)
    return [part.strip() for part in parts if len(part.strip()) > MIN_SENTENCE_CHARS]


def _best_sentence(text: str, criterion: Any) -> str | None:
    """The sentence that best matches one of the criterion's example phrases.

    Matching on the rubric's own positive examples rather than on the criterion
    label, because the examples are the concrete language a recruiter wrote down
    and the label is a category name that appears in no CV.
    """
    phrases = [str(example) for example in (criterion.positive_examples or [])]
    if not phrases:
        # A criterion with no examples — the work-authorisation blocker is one —
        # falls back to the distinctive words of its own question. Falling back
        # to the label instead finds nothing: a label is a category name and
        # appears in no CV, which made a blocker look unaddressed on a document
        # that answered it in plain words.
        phrases = [_distinctive(criterion.question)]

    phrases = [phrase for phrase in phrases if phrase]

    best: tuple[float, str] | None = None

    for sentence in _sentences(text):
        sentence_tokens = _tokens(sentence)
        if not sentence_tokens:
            continue

        for phrase in phrases:
            phrase_tokens = _tokens(phrase)
            if len(phrase_tokens) < MIN_PHRASE_TOKENS:
                continue
            overlap = len(phrase_tokens & sentence_tokens) / len(phrase_tokens)
            if overlap >= MATCH_THRESHOLD and (best is None or overlap > best[0]):
                best = (overlap, sentence)

    return best[1] if best else None


def for_rubric(rubric: Any) -> StandInModelClient:
    """A stand-in that knows this rubric's criteria by label."""
    return StandInModelClient(
        criteria_by_label={criterion.label: criterion for criterion in rubric.criteria}
    )


def for_rubrics(rubrics: Iterable[Any]) -> StandInModelClient:
    """A stand-in that knows every rubric's criteria.

    For the composition root, which does not know which role a request is for
    and does not need to: the criterion is named in the prompt, and labels are
    distinct across the rubrics that ship.
    """
    return StandInModelClient(
        criteria_by_label={
            criterion.label: criterion for rubric in rubrics for criterion in rubric.criteria
        }
    )
