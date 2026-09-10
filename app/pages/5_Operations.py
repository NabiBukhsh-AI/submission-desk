"""Six panels, and no seventh.

Each answers a question somebody actually asks. A dashboard grows decorative
charts the moment nobody is watching, and a chart nobody acts on makes the real
ones harder to find.

Nothing is computed here. Every number comes from a query in ``metrics_sql``, so
a figure on this page can be checked by running the query, and the evaluation
produces the same number for a report by calling the same function.

Cost is the one to read carefully. When pricing is not configured, this page says
so in those words. It never shows a zero, because a zero would read as free.
"""

from __future__ import annotations

import streamlit as st

from app.main import deps, state
from application.use_cases.operations_report import (
    UNCONFIGURED_MESSAGE,
    format_cost,
    operations_report,
)

#: How many times a trigger has to fire before "it never changed anything" is
#: evidence rather than a small sample.
ENOUGH_TO_JUDGE = 5

#: The default window. A week is what "is anything stuck" means in practice.
DEFAULT_DAYS = 7


def render() -> None:
    current = state()

    st.header("Operations")

    days = st.slider("Days", min_value=1, max_value=30, value=DEFAULT_DAYS)
    report = operations_report(deps(), days=days)

    _runs(report)
    _cost(report)
    _latency(report)
    _escalation(report)
    _failures(report)
    _people(report)

    if current.debug:
        st.divider()
        st.caption(f"Pricing: {report.pricing_source or 'absent'}")


def _runs(report) -> None:
    st.subheader("Runs")
    st.caption("Is anything stuck.")

    if not report.has_runs:
        st.info("Nothing has been processed in this period.", icon="📭")
        return

    rows = report.metrics.runs_by_status
    columns = st.columns(min(len(rows), 5))
    for column, (status, count) in zip(columns, rows[:5], strict=False):
        column.metric(status.replace("_", " ").capitalize(), count)

    if report.metrics.stuck:
        st.warning(
            f"{len(report.metrics.stuck)} run(s) stopped part-way and never finished.", icon="⚠️"
        )
        for run_id, candidate_id, status in report.metrics.stuck[:10]:
            st.caption(f"{candidate_id} · {status} · {run_id[:8]}")


def _cost(report) -> None:
    st.subheader("Cost")
    st.caption(
        "The routing claim, with a number behind it. All-cheap and all-strong bound "
        "the cost; routed has to earn its place between them."
    )

    if not report.metrics.cost_by_policy:
        st.info("No runs in this period.", icon="📭")
        return

    if not report.pricing_configured:
        st.info(UNCONFIGURED_MESSAGE, icon="ℹ️")

    for policy, summary in report.metrics.cost_by_policy.items():
        with st.container(border=True):
            st.markdown(f"**{policy}** — {summary.candidates} candidate(s)")

            total, per_candidate, per_hundred = st.columns(3)
            total.metric("Total", format_cost(summary.total_usd))
            per_candidate.metric("Per candidate", format_cost(summary.mean_per_candidate))
            per_hundred.metric("Per 100 candidates", format_cost(summary.per_hundred))

            st.caption(
                f"{summary.total_input_tokens:,} input and "
                f"{summary.total_output_tokens:,} output tokens across "
                f"{summary.llm_calls:,} call(s)."
            )


def _latency(report) -> None:
    st.subheader("Latency")
    st.caption("The operational claim.")

    run, call = report.metrics.run_latency, report.metrics.call_latency

    left, right = st.columns(2)
    with left:
        st.markdown("**Whole run**")
        st.metric("Median", _seconds(run.get("p50")))
        st.metric("95th percentile", _seconds(run.get("p95")))
        st.caption(f"{run.get('count', 0)} completed run(s).")
    with right:
        st.markdown("**One assessment call**")
        st.metric("Median", _millis(call.get("p50")))
        st.metric("95th percentile", _millis(call.get("p95")))
        st.caption(f"{call.get('count', 0)} call(s).")


def _escalation(report) -> None:
    st.subheader("Escalation")
    st.caption(
        "Whether reading something twice earns its cost. The second column is the "
        "one that matters: how often escalating actually changed the answer."
    )

    st.metric("Criteria read twice", _percent(report.metrics.escalation_rate))

    rows = report.metrics.escalation_by_trigger
    if not rows:
        st.caption("Nothing has been escalated yet.")
        return

    for trigger, count, changed, changed_rate in rows:
        st.markdown(
            f"**{trigger}** — fired {count} time(s), changed the answer "
            f"{changed} time(s) ({_percent(changed_rate)})"
        )
        if count >= ENOUGH_TO_JUDGE and changed == 0:
            st.caption(
                "This trigger has never changed an answer. Switching it off would "
                "be a result worth writing down."
            )


def _failures(report) -> None:
    st.subheader("Failures")
    st.caption("Where the system actually breaks, rather than where people expect it to.")

    rates = report.metrics.failure_rates
    if not rates.get("runs"):
        st.info("No runs in this period.", icon="📭")
        return

    spans, validation, retries, repairs = st.columns(4)
    spans.metric("Quotations not found", _percent(rates.get("invalid_span_rate")))
    validation.metric("Schema failures", _percent(rates.get("validation_failure_rate")))
    retries.metric("Retries", _percent(rates.get("retry_rate")))
    repairs.metric("Repairs", _percent(rates.get("repair_rate")))

    codes = report.metrics.error_codes
    if codes:
        st.markdown("**Errors**")
        for code, count in codes[:10]:
            st.markdown(f"- `{code}` — {count}")


def _people(report) -> None:
    st.subheader("The people using it")
    st.caption("The productivity and trust claims, measured rather than estimated.")

    effort = report.metrics.review_effort
    overrides = report.metrics.override_rate

    if not effort.get("reviews"):
        st.info("Nobody has reviewed anything yet.", icon="📭")
        return

    mean, median, rate = st.columns(3)
    mean.metric("Mean minutes per review", f"{effort['mean_minutes']:.1f}")
    median.metric("Median minutes", f"{effort['median_minutes']:.1f}")
    rate.metric("Reviews with a correction", _percent(overrides.get("rate")))

    reasons = report.metrics.override_reasons
    if reasons:
        st.markdown("**Why reviewers disagreed**")
        for reason, count in reasons:
            st.markdown(f"- {reason.replace('_', ' ')} — {count}")

    trust = report.metrics.trust_distribution
    if trust:
        st.markdown("**How much they trusted it**")
        st.bar_chart({str(rating): count for rating, count in trust})


def _percent(value: float | None) -> str:
    return "—" if value is None else f"{value * 100:.1f}%"


def _seconds(value: float | None) -> str:
    return "—" if value is None else f"{value:.1f}s"


def _millis(value: float | None) -> str:
    return "—" if value is None else f"{value:,.0f}ms"


render()
