"""Arm B: one call, the whole document, the whole rubric, a band back.

The comparison, not a strawman. It gets a competent prompt — the same rubric,
the same criteria, clearly asked — because an arm built to lose proves nothing,
and the interesting result is the one where this arm does *well*.

What it deliberately lacks is the architecture: no per-criterion isolation, no
span validation, no deterministic scoring, no abstention vocabulary beyond what
it chooses to say. If the eleven-node pipeline cannot beat this on abstention
accuracy and hallucination rate, that is the finding, and it belongs in the
report rather than in a drawer.

The comparison that matters is not band accuracy. It is what happens on the
sparse CV and the injected one, where this arm has nothing to stop it inventing
an answer.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from typing import Any

from domain.contracts.enums import Band, CriterionState, ModelTier
from domain.ports.models import BlockKind, GenerationRequest, ModelUnavailable, PromptBlock

#: What this arm is asked for. One band, one short justification, nothing else.
#: A richer schema would be a different arm; this is the shape somebody reaches
#: for on a Friday afternoon when asked to "just use the LLM".
SYSTEM_PROMPT = """You are screening a candidate for the role described below.

Read the candidate's documents and decide which band this application belongs in.

The role asks for these things:

{criteria}

The bands are:
- advance: clearly meets the bar for this role
- advance_with_reservations: meets it, with something worth asking about
- hold: uncertain, worth a second opinion
- decline: does not meet the bar
- insufficient_information: the documents do not say enough to judge

Answer with JSON only, of exactly this shape:
{{"band": "<one of the bands above>", "justification": "<two sentences>"}}

Choose insufficient_information if the documents genuinely do not cover enough
of the role to decide. Do not guess a band to avoid saying so."""


@dataclass(frozen=True)
class NaiveResult:
    """What one call produced."""

    band: Band | None
    justification: str = ""
    input_tokens: int = 0
    output_tokens: int = 0
    latency_ms: int = 0
    failed_reason: str = ""

    @property
    def ok(self) -> bool:
        return self.band is not None


def assess(
    documents: list[tuple[str, str]],
    rubric: Any,
    models: Any,
    *,
    tier: ModelTier = ModelTier.CHEAP,
    nonce: str = "",
) -> NaiveResult:
    """One model call for the whole candidate.

    ``documents`` is (document_id, text). They go in as untrusted blocks, the
    same way the real pipeline sends them: this arm is a comparison of
    architecture, not a demonstration that skipping prompt hygiene is bad.
    """
    started = time.monotonic()

    request = GenerationRequest(
        call_site="eval.arm_b",
        tier=tier,
        system_prompt=SYSTEM_PROMPT.format(criteria=_criteria(rubric)),
        user_blocks=tuple(
            PromptBlock(kind=BlockKind.DOCUMENT, content=text) for _, text in documents
        ),
        # No schema on purpose. This arm asks for prose and parses it, which
        # is the thing being compared against; a schema here would make it a
        # worse version of arm C rather than a different architecture.
        response_schema=None,  # type: ignore[arg-type]
        temperature=0.0,
        nonce=nonce,
    )

    try:
        result = models.structured_generate(request)
    except ModelUnavailable as unavailable:
        return NaiveResult(band=None, failed_reason=str(unavailable))

    elapsed = int((time.monotonic() - started) * 1000)
    band, justification = _parse(getattr(result, "raw_text", "") or "")
    usage = getattr(result, "usage", None)

    return NaiveResult(
        band=band,
        justification=justification,
        input_tokens=getattr(usage, "input_tokens", 0),
        output_tokens=getattr(usage, "output_tokens", 0),
        latency_ms=getattr(result, "latency_ms", elapsed) or elapsed,
        failed_reason="" if band else "unparseable",
    )


def criterion_states(result: NaiveResult, rubric: Any) -> dict[str, CriterionState]:
    """What this arm says about each criterion: nothing.

    Returned explicitly rather than left empty, because the difference is the
    point. This arm produces a band and no per-criterion answer, so every
    criterion-level metric scores it as unable to say — which is what it is.

    Scoring it as wrong would be unfair; omitting it from the table would hide
    that a single-call approach cannot answer the question the rubric asks.
    """
    return {criterion.id: CriterionState.INSUFFICIENT_EVIDENCE for criterion in rubric.criteria}


def _criteria(rubric: Any) -> str:
    """The rubric as the prompt sees it. Weights included, because withholding
    them would be handicapping the arm rather than comparing it."""
    return "\n".join(
        f"- {criterion.label} (weight {criterion.weight}): {criterion.question.strip()}"
        for criterion in rubric.criteria
    )


def _parse(raw: str) -> tuple[Band | None, str]:
    """Read the answer, tolerating the wrappers models put around JSON.

    Tolerant on purpose. Failing this arm on a code fence would be measuring
    output formatting rather than screening quality, and the whole point is a
    fair comparison.
    """
    text = raw.strip()
    if text.startswith("```"):
        text = text.split("```")[1] if "```" in text[3:] else text[3:]
        text = text.removeprefix("json").strip()

    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end <= start:
        return None, ""

    try:
        parsed = json.loads(text[start : end + 1])
    except json.JSONDecodeError:
        return None, ""

    try:
        band = Band(str(parsed.get("band", "")).strip().lower())
    except ValueError:
        return None, str(parsed.get("justification", ""))

    return band, str(parsed.get("justification", ""))
