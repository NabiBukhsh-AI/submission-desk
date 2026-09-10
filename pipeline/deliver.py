"""Sending a reviewed package, and refusing to send an unreviewed one.

The approval check here is deliberately redundant. The state machine already
forbids reaching DELIVERED except from APPROVED, and the runner already stops
before this node without a decision. This checks anyway, against the decision
table rather than the status, because a status is a column somebody could update
and a decision is a row somebody had to create.

So an APPROVED run with no decision behind it still cannot be delivered, even by
calling this node directly. That is the one control that survives every other
one being wrong.

CSV first, always, and not removable. Then each configured sink, each with its
own record, so partial delivery is representable: the file was written, the
spreadsheet was not, and the reviewer sees exactly that rather than one word
covering both.
"""

from __future__ import annotations

import time
from datetime import UTC, datetime
from uuid import uuid4

from application.deps import Deps
from domain.calibration import card_from, should_create, summary_of
from domain.contracts.delivery import DeliveryRecord
from domain.contracts.enums import DeliveryStatus, RunStatus
from domain.contracts.errors import ErrorRecord
from domain.contracts.run_state import DomainEvent, NodeResult, NodeStatus, RunState
from domain.ports.calibration import embedding_text
from domain.ports.sinks import AdapterError, AdapterResult, DeliveryPayload

#: What a reviewer is told when delivery was refused for lack of a decision.
NOT_APPROVED = (
    "This candidate has not been approved, so nothing was sent. Open the review "
    "page and decide first."
)


def node(state: RunState, deps: Deps) -> NodeResult:
    """Write the package to every configured destination."""
    refusal = refuse_reason(state, deps)
    if refusal is not None:
        return _refused(state, refusal)

    payload = build_payload(state, deps)
    sinks = list(deps.sinks or ())

    results: list[tuple[str, AdapterResult]] = []
    events: list[DomainEvent] = []

    for sink in sinks:
        if not sink.healthy():
            # Disabled earlier in the session by an auth or configuration
            # failure. Attempting it again would produce one identical message
            # per candidate and bury the one that says what to fix.
            results.append(
                (sink.sink_id, AdapterResult.failed(AdapterError.CONFIG, _unhealthy_message(sink)))
            )
            continue

        started = time.monotonic()
        result = sink.deliver(payload)
        latency = int((time.monotonic() - started) * 1000)

        results.append((sink.sink_id, result))
        deps.deliveries.record(_record_for(state, sink.sink_id, result))

        events.append(
            DomainEvent(
                name="deliver.sink_attempted",
                payload={
                    "sink": sink.sink_id,
                    "ok": result.ok,
                    "attempts": result.attempts,
                    "latency_ms": result.latency_ms or latency,
                    "error_code": result.error_code.value if result.error_code else None,
                },
            )
        )

    delivered = [sink_id for sink_id, result in results if result.ok]
    failed = [(sink_id, result) for sink_id, result in results if not result.ok]

    if failed:
        events.append(
            DomainEvent(
                name="deliver.degraded",
                payload={
                    "delivered": delivered,
                    "failed": [sink_id for sink_id, _ in failed],
                    "reasons": {
                        sink_id: (result.error_code.value if result.error_code else "unknown")
                        for sink_id, result in failed
                    },
                },
            )
        )

    if not delivered:
        return NodeResult(
            state=state,
            status=NodeStatus.FAILED,
            events=tuple(events),
            error=_error(
                state,
                "The result could not be sent anywhere. It is saved and will be "
                "retried; nothing has been lost.",
                "DELIVERY_FAILED",
                RunStatus.DELIVERY_PENDING_RETRY,
            ),
        )

    if failed:
        # Some sinks worked. The run is not delivered, because delivery means
        # everywhere it was meant to go, and a retry completes the rest.
        return NodeResult(
            state=state,
            status=NodeStatus.DEGRADED,
            events=tuple(events),
            next_status=RunStatus.DELIVERY_PENDING_RETRY,
        )

    _write_calibration_card(state, deps, events)

    return NodeResult(
        state=state,
        status=NodeStatus.OK,
        events=tuple(events),
        next_status=RunStatus.DELIVERED,
    )


def _write_calibration_card(state: RunState, deps: Deps, events: list[DomainEvent]) -> None:
    """Turn this decision into an anchor for the next candidate.

    Here and nowhere else, and only from an approval. A card built anywhere
    earlier would be built from output nobody had checked, and a system that
    learns from its own unreviewed opinions compounds its errors instead of
    correcting them.

    Failing to write one is not a delivery failure. The candidate's result has
    already been sent; losing an anchor costs the next run a reference, which is
    a cost worth exactly nothing next to failing a completed delivery.
    """
    if deps.calibration is None or deps.embedder is None:
        return

    decision = deps.reviews.get_for_run(state.run_id)
    if not should_create(decision):
        return

    recommendation = deps.evidence.recommendation_for_run(state.run_id)
    if recommendation is None:
        return

    try:
        summary_source = state.profile
        summary = summary_of(summary_source)
        vector = deps.embedder.embed(
            embedding_text(summary, getattr(recommendation, "criterion_states", {}))
        )
        if vector is None:
            events.append(
                DomainEvent(name="deliver.card_skipped", payload={"reason": "no_embedding"})
            )
            return

        card = card_from(
            run_id=state.run_id,
            role_id=state.role_id,
            rubric_version=getattr(state.rubric, "version", "1.0.0"),
            profile=summary_source,
            recommendation=recommendation,
            decision=decision,
            embedding=vector,
            redact=deps.redactor,
        )
        deps.calibration.add(card)
    except Exception:
        events.append(DomainEvent(name="deliver.card_skipped", payload={"reason": "unexpected"}))
        return

    events.append(
        DomainEvent(
            name="deliver.card_written",
            payload={"card_id": str(card.card_id), "band": card.final_band.value},
        )
    )


def refuse_reason(state: RunState, deps: Deps) -> str | None:
    """Why this run may not be delivered, or None.

    Two checks, and the second is the one that matters. The status can be
    updated by anything that can write to the runs table; the decision row had
    to be created by ``submit_review``, which means a person chose it.
    """
    if state.status not in (RunStatus.APPROVED, RunStatus.DELIVERY_PENDING_RETRY):
        return NOT_APPROVED

    decision = deps.reviews.get_for_run(state.run_id)
    if decision is None:
        return NOT_APPROVED
    if decision.action.value != "approve":
        return NOT_APPROVED

    return None


def build_payload(state: RunState, deps: Deps) -> DeliveryPayload:
    """What every sink is given.

    Built once so two destinations cannot disagree about what was decided, and
    narrow on purpose: the outcome and the reasoning, never the evidence.
    """
    recommendation = deps.evidence.recommendation_for_run(state.run_id)
    decision = deps.reviews.get_for_run(state.run_id)
    package = state.package

    return DeliveryPayload(
        run_id=str(state.run_id),
        candidate_id=state.candidate_id,
        role_id=state.role_id,
        band=_band_of(recommendation, decision),
        score=getattr(recommendation, "score", None),
        coverage=getattr(recommendation, "coverage", 0.0),
        derivation=tuple(step.description for step in getattr(recommendation, "derivation", ())),
        reviewer_id=getattr(decision, "reviewer_id", ""),
        reviewer_action=getattr(getattr(decision, "action", None), "value", ""),
        decided_at=(decision.decided_at.isoformat() if decision and decision.decided_at else ""),
        criterion_states={
            criterion_id: value.value
            for criterion_id, value in getattr(recommendation, "criterion_states", {}).items()
        },
        override_count=len(getattr(decision, "overrides", ()) or ()),
        integrity_tier=state.integrity_tier.value,
        information_requests=tuple(
            text for _, text in getattr(package, "information_requests", ())
        ),
    )


def _band_of(recommendation: object, decision: object) -> str:
    """The band that was actually decided.

    A reviewer's post-override band wins over the computed one, because that is
    what they approved. Falling back to the computed band would send a figure
    the reviewer had corrected.
    """
    reviewed = getattr(decision, "post_override_band", None)
    if reviewed is not None:
        return str(getattr(reviewed, "value", reviewed))

    band = getattr(recommendation, "band", None)
    return str(getattr(band, "value", band or "unknown"))


def _record_for(state: RunState, sink_id: str, result: AdapterResult) -> DeliveryRecord:
    return DeliveryRecord(
        delivery_id=uuid4(),
        run_id=state.run_id,
        sink_id=sink_id,
        status=DeliveryStatus.DELIVERED if result.ok else DeliveryStatus.FAILED,
        attempts=result.attempts,
        external_ref=result.external_ref,
        last_error=None if result.ok else result.message,
        delivered_at=datetime.now(UTC) if result.ok else None,
    )


def _unhealthy_message(sink: object) -> str:
    return (
        f"{getattr(sink, 'sink_id', 'This destination')} is switched off for this "
        "session because it could not be reached with the configured credentials."
    )


def _refused(state: RunState, message: str) -> NodeResult:
    return NodeResult(
        state=state,
        status=NodeStatus.FAILED,
        events=(DomainEvent(name="deliver.refused", payload={"reason": "not_approved"}),),
        error=_error(state, message, "NOT_APPROVED", RunStatus.NEEDS_REVIEW),
    )


def _error(state: RunState, message: str, code: str, resulting: RunStatus) -> ErrorRecord:
    return ErrorRecord(
        error_id=uuid4(),
        run_id=state.run_id,
        node="DELIVER",
        error_code=code,
        error_class="DeliveryRefused" if code == "NOT_APPROVED" else "DeliveryFailed",
        message_redacted=message,
        retryable=code != "NOT_APPROVED",
        attempt=1,
        resulting_state=resulting,
        occurred_at=datetime.now(UTC),
    )
