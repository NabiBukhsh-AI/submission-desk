"""The ASSESS node.

One call per criterion, each independent of every other. That independence is
not an implementation detail: it is what makes the results reproducible, what
lets the criteria run in parallel, and what stops a weak answer on one point
colouring the reading of the next.

What comes back is evidence, never a judgment. Each item is a claim with a
quotation, and every quotation is looked for in the document before the item is
allowed to count. Items whose quotation cannot be found are kept, shown to the
reviewer, and counted as a hallucination rate, but they never reach the rule
engine.

Escalation replaces the cheap answer with the strong one for scoring, and keeps
both. That is what makes the value of escalating measurable after the fact
rather than requiring a separate experiment.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID, uuid4

from application.accounting import record_call
from application.deps import Deps
from domain.contracts.assessment import CriterionAssessment
from domain.contracts.enums import (
    VALID_SPAN_VALIDATIONS,
    CriterionState,
    EscalationState,
    EvidenceState,
    ExtractionMethod,
    ModelTier,
    RunStatus,
    SpanValidation,
)
from domain.contracts.errors import ErrorRecord
from domain.contracts.evidence import EvidenceItem
from domain.contracts.responses import AssessmentResponse, EvidenceCandidate
from domain.contracts.rubric import Criterion, RoleRubric
from domain.contracts.run_state import DomainEvent, NodeResult, NodeStatus, RunState
from domain.contracts.source_text import Provenance, SourceText
from domain.fairness import mentions_protected_attribute
from domain.ports.models import BlockKind, GenerationRequest, ModelUnavailable, PromptBlock
from domain.ports.routing import BudgetState, RoutingRequest
from domain.provenance import chunking
from domain.provenance.validator import SpanThresholds, validate
from domain.rules.resolve import CriterionResolution, resolve_criterion

#: What a model claims about where a span sits. Neither value is trusted: the
#: validator relocates the quotation and reads the real extraction method off
#: the page the span lands on. They exist because Provenance requires them, and
#: a contract that let them be omitted would let an unlocatable span look
#: complete.
CLAIMED_METHOD = ExtractionMethod.DIGITAL_PDF
CLAIMED_PROFILE = "np-v1-nfkc-ws-dash-hyphen"


@dataclass
class CriterionOutcome:
    """What assessing one criterion produced, including what was rejected."""

    criterion_id: str
    accepted: list[EvidenceItem]
    rejected: list[EvidenceItem]
    resolution: CriterionResolution
    tier_used: ModelTier
    escalated: bool = False
    escalation_trigger: str | None = None
    pre_escalation_state: CriterionState | None = None
    chunks_shown: list[int] | None = None
    unassessed_reason: str | None = None
    dropped_for_forbidden_content: int = 0
    input_tokens: int = 0
    output_tokens: int = 0


def node(state: RunState, deps: Deps) -> NodeResult:
    """Assess every criterion in the rubric, independently."""
    rubric = getattr(state, "rubric", None)
    if rubric is None:
        return _failed(state, "No role definition was loaded for this run.", "NO_RUBRIC")

    sources = _sources_for(deps, state)
    if not sources:
        return _failed(state, "The documents could not be read into text.", "NO_SOURCE_TEXT")

    outcomes = _assess_all(state, deps, rubric, sources)
    events = _events_for(outcomes)

    for outcome in outcomes:
        _persist(deps, state, outcome)

    capped = any(outcome.unassessed_reason == "budget_ceiling" for outcome in outcomes)
    carried = state.model_copy(
        update={
            "assessments": tuple(_assessment_for(outcome) for outcome in outcomes),
            "budget_capped": capped,
            "invalid_span_present": any(outcome.rejected for outcome in outcomes),
        }
    )

    if capped:
        events.append(DomainEvent(name="assess.budget_capped", payload={}))
        return NodeResult(
            state=carried.degraded_by("budget_ceiling_reached"),
            status=NodeStatus.DEGRADED,
            events=tuple(events),
            next_status=RunStatus.MANUAL_REVIEW_REQUIRED,
        )

    return NodeResult(
        state=carried,
        status=NodeStatus.OK,
        events=tuple(events),
        next_status=RunStatus.ASSESSED,
    )


def _assess_all(
    state: RunState, deps: Deps, rubric: RoleRubric, sources: dict[UUID, SourceText]
) -> list[CriterionOutcome]:
    """Run every criterion, in parallel, then restore rubric order.

    Parallelism is safe precisely because criteria are independent. The results
    are sorted back into rubric order before anything reads them, so the output
    does not depend on which thread finished first: a run whose evidence
    ordering varied would produce a different derivation trace each time.
    """
    workers = max(1, deps.settings.assess_concurrency)

    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {
            criterion.id: pool.submit(_assess_one, state, deps, criterion, sources)
            for criterion in rubric.criteria
        }
        results = {criterion_id: future.result() for criterion_id, future in futures.items()}

    return [results[criterion.id] for criterion in rubric.criteria]


def _assess_one(
    state: RunState, deps: Deps, criterion: Criterion, sources: dict[UUID, SourceText]
) -> CriterionOutcome:
    """One criterion, start to finish, with at most one escalation."""
    if deps.budget is not None and not deps.budget.allows(state):
        return _unassessed(criterion, "budget_ceiling")

    first = _attempt(state, deps, criterion, sources, attempt_index=0)

    decision = deps.router.select(
        _routing_request(deps, state, criterion, attempt_index=1, outcome=first)
    )

    if decision.selected_tier is not ModelTier.STRONG or first.tier_used is ModelTier.STRONG:
        return first

    if deps.budget is not None and not deps.budget.may_escalate(state.run_id):
        return first

    second = _attempt(state, deps, criterion, sources, attempt_index=1, tier=ModelTier.STRONG)

    # The strong result replaces the cheap one for scoring, and both are
    # persisted. That asymmetry is what makes escalation value computable
    # afterwards rather than needing its own experiment.
    second.escalated = True
    second.escalation_trigger = decision.trigger
    second.pre_escalation_state = first.resolution.state
    second.rejected = [*first.rejected, *second.rejected]
    return second


def _attempt(
    state: RunState,
    deps: Deps,
    criterion: Criterion,
    sources: dict[UUID, SourceText],
    *,
    attempt_index: int,
    tier: ModelTier | None = None,
) -> CriterionOutcome:
    """One model call for one criterion, validated end to end."""
    chosen_tier = (
        tier
        or deps.router.select(
            _routing_request(deps, state, criterion, attempt_index=attempt_index)
        ).selected_tier
    )

    blocks, chunks_shown = _document_blocks(deps, state, criterion, sources)
    prompt_id = "assessment/criterion_blind" if state.blind_mode else "assessment/criterion"
    prompt = deps.prompts.get(prompt_id)

    request = GenerationRequest(
        call_site="assess.criterion",
        tier=chosen_tier,
        system_prompt=prompt.render(
            criterion_label=criterion.label,
            criterion_question=criterion.question,
            positive_examples="\n".join(f"- {example}" for example in criterion.positive_examples)
            or "- (none given)",
            negative_examples="\n".join(f"- {example}" for example in criterion.negative_examples)
            or "- (none given)",
        ),
        user_blocks=tuple(blocks),
        response_schema=AssessmentResponse,
        temperature=0.0,
        nonce=state.nonce or "",
    )

    try:
        result = deps.models.structured_generate(request)
    except ModelUnavailable:
        return _unassessed(criterion, "model_unavailable")

    # Guard, ledger, and the run's totals, in one call. Recording into the
    # guard alone is what left llm_calls empty and every run reporting zero
    # tokens while the budget ceiling worked perfectly.
    record_call(
        deps,
        state.run_id,
        call_site="assess.criterion",
        tier=chosen_tier,
        input_tokens=result.usage.input_tokens,
        output_tokens=result.usage.output_tokens,
        cached_input_tokens=getattr(result.usage, "cached_input_tokens", 0),
        latency_ms=result.latency_ms,
        escalated=attempt_index > 0,
    )

    if not result.ok or not isinstance(result.parsed, AssessmentResponse):
        return _unassessed(criterion, "repair_failed", tier=chosen_tier)

    accepted, rejected, dropped = _to_evidence(
        result.parsed, criterion, sources, chosen_tier, prompt.versioned_id, attempt_index
    )

    return CriterionOutcome(
        criterion_id=criterion.id,
        accepted=accepted,
        rejected=rejected,
        resolution=resolve_criterion(accepted, criterion),
        tier_used=chosen_tier,
        chunks_shown=chunks_shown,
        dropped_for_forbidden_content=dropped,
        input_tokens=result.usage.input_tokens,
        output_tokens=result.usage.output_tokens,
    )


def _to_evidence(
    response: AssessmentResponse,
    criterion: Criterion,
    sources: dict[UUID, SourceText],
    tier: ModelTier,
    prompt_version: str,
    attempt_index: int,
) -> tuple[list[EvidenceItem], list[EvidenceItem], int]:
    """Turn a model's answer into evidence, validating every quotation.

    ``span_validation`` is set here and only here. The response schema does not
    contain the field, so nothing a model returns can mark its own quotation as
    verified.
    """
    accepted: list[EvidenceItem] = []
    rejected: list[EvidenceItem] = []
    dropped = 0

    for candidate in response.evidence:
        if _mentions_forbidden(candidate.claim):
            dropped += 1
            continue

        item = _item_from(candidate, criterion, tier, prompt_version, attempt_index)
        if item is None:
            continue

        check = validate(item, sources, SpanThresholds())
        located = item.model_copy(
            update={
                "span_validation": check.validation,
                "span_match_ratio": check.ratio,
                "validated_norm_start": check.validated_norm_start,
            }
        )

        if (
            located.state is EvidenceState.INSUFFICIENT_EVIDENCE
            or check.validation in VALID_SPAN_VALIDATIONS
        ):
            accepted.append(located)
        else:
            rejected.append(located)

    return accepted, rejected, dropped


def _item_from(
    candidate: EvidenceCandidate,
    criterion: Criterion,
    tier: ModelTier,
    prompt_version: str,
    attempt_index: int,
) -> EvidenceItem | None:
    """Build an evidence item from what the model returned.

    A candidate the contract refuses is dropped rather than repaired: patching
    a response into validity would be the system inventing the part the model
    left out.
    """
    provenance = None
    if candidate.document_id is not None and candidate.verbatim_span:
        # The model reports a page and an offset. Neither is trusted: the
        # validator relocates the quotation and corrects the offset, and the
        # extraction method comes from the page the span actually lands on.
        start = candidate.norm_start or 0
        provenance = Provenance(
            document_id=candidate.document_id,
            page_start=candidate.page_start or 1,
            page_end=candidate.page_end or candidate.page_start or 1,
            norm_start=start,
            norm_end=max(candidate.norm_end or 0, start + len(candidate.verbatim_span)),
            extraction_method=CLAIMED_METHOD,
            normalization_profile_id=CLAIMED_PROFILE,
        )

    try:
        return EvidenceItem(
            evidence_id=uuid4(),
            criterion_id=criterion.id,
            state=candidate.state,
            claim=candidate.claim,
            verbatim_span=candidate.verbatim_span,
            provenance=provenance,
            confidence=candidate.confidence,
            model_tier=tier,
            prompt_version=prompt_version,
            escalation_state=(
                EscalationState.ESCALATED if attempt_index > 0 else EscalationState.NOT_ESCALATED
            ),
            span_validation=(
                SpanValidation.NOT_APPLICABLE
                if candidate.state is EvidenceState.INSUFFICIENT_EVIDENCE
                else SpanValidation.INVALID_NOT_FOUND
            ),
        )
    except ValueError:
        return None


def _mentions_forbidden(claim: str, criterion: Criterion | None = None) -> bool:
    """Whether a claim strayed onto protected ground.

    The schema stops a model recording such a thing as a field; this stops it
    arriving as prose in a claim. Both are needed, because the claim is free
    text and free text is where an instruction-following model would put it.

    The matching lives in ``domain.fairness`` so that this and composition
    cannot drift apart, and so the word-boundary rule is stated once. Substring
    matching here previously deleted every claim containing "language".
    """
    return mentions_protected_attribute(claim)


def _document_blocks(
    deps: Deps, state: RunState, criterion: Criterion, sources: dict[UUID, SourceText]
) -> tuple[list[PromptBlock], list[int] | None]:
    """The passages this criterion is judged against.

    Selection is recorded, because an insufficient-evidence answer on selected
    chunks means "not in what we showed" rather than "not in the CV", and the
    evaluation reports the two separately.
    """
    blocks: list[PromptBlock] = []
    shown: list[int] = []
    selected_any = False

    terms = chunking.query_terms_for(
        criterion.label, criterion.question, list(criterion.positive_examples)
    )

    for source in sources.values():
        chunks, was_selected = chunking.select_for_criterion(
            source,
            query_terms=terms,
            max_tokens=deps.settings.assess_max_input_tokens,
            top_k=deps.settings.assess_chunk_k,
        )
        selected_any = selected_any or was_selected

        for chunk in chunks:
            blocks.append(
                PromptBlock(
                    kind=BlockKind.DOCUMENT,
                    content=chunk.text,
                    document_id=source.document_id,
                )
            )
            shown.extend(chunk.page_numbers)

    calibration = getattr(state, "calibration_block", None)
    if calibration:
        # Labelled and separate. A quotation taken from here resolves to a
        # document that is not in sources and is rejected by name.
        blocks.append(
            PromptBlock(
                kind=BlockKind.CALIBRATION,
                content=(
                    "Reference only: past decisions for this role, for calibration. "
                    "Do not quote from this section.\n\n" + calibration
                ),
            )
        )

    return blocks, sorted(set(shown)) if selected_any else None


def _routing_request(
    deps: Deps,
    state: RunState,
    criterion: Criterion,
    *,
    attempt_index: int,
    outcome: CriterionOutcome | None = None,
):
    budget = deps.budget.state_for(state.run_id) if deps.budget is not None else None
    return RoutingRequest(
        call_site="assess.criterion",
        criterion_id=criterion.id,
        criterion_kind=criterion.kind,
        attempt_index=attempt_index,
        prior_confidence=_lowest_confidence(outcome),
        prior_invalid_span_count=len(outcome.rejected) if outcome else None,
        prior_repair_failed=bool(outcome and outcome.unassessed_reason == "repair_failed"),
        budget=BudgetState(
            tokens_used=budget.total_tokens if budget else 0,
            token_ceiling=deps.settings.token_ceiling_per_run,
            remaining_usd=None,
            escalations_used=budget.escalations if budget else 0,
        ),
    )


def _lowest_confidence(outcome: CriterionOutcome | None) -> float | None:
    """The least confident item, since one shaky finding taints the criterion."""
    if outcome is None or not outcome.accepted:
        return None
    return min(item.confidence for item in outcome.accepted)


def _unassessed(
    criterion: Criterion, reason: str, *, tier: ModelTier = ModelTier.CHEAP
) -> CriterionOutcome:
    """A criterion nobody could assess, recorded as such.

    Not not_met. "We did not check" and "the document says no" are different
    facts, and collapsing them would let a budget ceiling look like a finding
    about a candidate.
    """
    return CriterionOutcome(
        criterion_id=criterion.id,
        accepted=[],
        rejected=[],
        resolution=resolve_criterion([], criterion, unassessed_reason=reason),
        tier_used=tier,
        unassessed_reason=reason,
    )


def _assessment_for(outcome: CriterionOutcome) -> CriterionAssessment:
    return CriterionAssessment(
        criterion_id=outcome.criterion_id,
        evidence=outcome.accepted,
        rejected_evidence=outcome.rejected,
        resolved_state=outcome.resolution.state,
        resolution_rule_id=outcome.resolution.rule_id,
        tier_used=outcome.tier_used,
        escalated=outcome.escalated,
        escalation_trigger=outcome.escalation_trigger,
        pre_escalation_state=outcome.pre_escalation_state,
        escalation_changed_state=(
            outcome.pre_escalation_state is not outcome.resolution.state
            if outcome.escalated
            else None
        ),
        chunks_shown=outcome.chunks_shown,
        unassessed_reason=outcome.unassessed_reason,
    )


def _persist(deps: Deps, state: RunState, outcome: CriterionOutcome) -> None:
    if outcome.accepted:
        deps.evidence.save_evidence(state.run_id, outcome.accepted, rejected=False)
    if outcome.rejected:
        deps.evidence.save_evidence(state.run_id, outcome.rejected, rejected=True)
    deps.evidence.save_assessment(state.run_id, _assessment_for(outcome))


def _events_for(outcomes: list[CriterionOutcome]) -> list[DomainEvent]:
    events: list[DomainEvent] = []
    for outcome in outcomes:
        events.append(
            DomainEvent(
                name="assess.criterion_assessed",
                payload={
                    "criterion_id": outcome.criterion_id,
                    "tier": outcome.tier_used.value,
                    "escalated": outcome.escalated,
                    "escalation_trigger": outcome.escalation_trigger,
                    "state": outcome.resolution.state.value,
                    "accepted": len(outcome.accepted),
                    "rejected": len(outcome.rejected),
                    "dropped_forbidden": outcome.dropped_for_forbidden_content,
                    "chunked": outcome.chunks_shown is not None,
                    "input_tokens": outcome.input_tokens,
                    "output_tokens": outcome.output_tokens,
                },
            )
        )
    return events


def _sources_for(deps: Deps, state: RunState) -> dict[UUID, SourceText]:
    profile_id = deps.source_profile_id
    found: dict[UUID, SourceText] = {}
    for document in deps.candidates.documents_for_run(state.run_id):
        source = deps.candidates.get_source_text(document.document_sha256, profile_id)
        if source is not None:
            found[source.document_id] = source
    return found


def _failed(state: RunState, message: str, error_code: str) -> NodeResult:
    return NodeResult(
        state=state,
        status=NodeStatus.FAILED,
        events=(DomainEvent(name="assess.failed", payload={"reason": error_code}),),
        error=ErrorRecord(
            error_id=uuid4(),
            run_id=state.run_id,
            node="ASSESS",
            error_code=error_code,
            error_class="AssessFailed",
            message_redacted=message,
            retryable=False,
            attempt=1,
            resulting_state=RunStatus.MANUAL_REVIEW_REQUIRED,
            occurred_at=datetime.now(UTC),
        ),
    )
