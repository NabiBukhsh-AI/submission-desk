"""The six questions the operations page asks, as SQL.

One function per panel, each returning plain rows. The page renders; it does not
compute. That split means a number on the page can be checked by running the
query, and that the evaluation can call the same function to produce the same
number for a report.

Six panels and no more. A dashboard grows decorative charts the moment nobody is
watching, and a chart nobody acts on is a chart that makes the real ones harder
to find. Each of these answers a question somebody actually asks: is anything
stuck, what did it cost, how slow is it, does escalation earn its keep, where
does it break, and what is it doing to the people using it.

Cost figures are ``None`` when pricing is unconfigured, never zero. SQL's
``SUM`` over an all-NULL column returns NULL, which is the behaviour wanted, so
none of these queries defend against it with ``COALESCE(..., 0)``.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import Any

from domain.ports.metrics import MetricsSnapshot, PolicyCost

#: How far back the page looks by default. A week is what "is anything stuck"
#: means in practice.
DEFAULT_DAYS = 7


def _connect(db_path: Path | str) -> sqlite3.Connection:
    connection = sqlite3.connect(str(db_path), timeout=5.0)
    connection.row_factory = sqlite3.Row
    return connection


def _rows(db_path: Path | str, sql: str, params: tuple[Any, ...] = ()) -> list[sqlite3.Row]:
    connection = _connect(db_path)
    try:
        return connection.execute(sql, params).fetchall()
    except sqlite3.Error:
        # A missing table means a database from before a migration, not a
        # failure worth taking the page down for.
        return []
    finally:
        connection.close()


def _one(db_path: Path | str, sql: str, params: tuple[Any, ...] = ()) -> sqlite3.Row | None:
    rows = _rows(db_path, sql, params)
    return rows[0] if rows else None


def _since(days: int) -> str:
    return f"-{max(days, 1)} days"


# --- panel 1: runs ---------------------------------------------------------------


def runs_by_status(db_path: Path | str, *, days: int = DEFAULT_DAYS) -> list[tuple[str, int]]:
    """Is anything stuck.

    Counted by status rather than by outcome, because the useful signal is a
    run sitting in ASSESSED at four in the afternoon, not the final tally.
    """
    rows = _rows(
        db_path,
        """
        SELECT status, COUNT(*) AS n
        FROM runs
        WHERE started_at >= datetime('now', ?)
        GROUP BY status
        ORDER BY n DESC
        """,
        (_since(days),),
    )
    return [(row["status"], row["n"]) for row in rows]


def stuck_runs(db_path: Path | str, *, minutes: int = 15) -> list[tuple[str, str, str]]:
    """Runs that stopped mid-flight and never finished."""
    rows = _rows(
        db_path,
        """
        SELECT run_id, candidate_id, status
        FROM runs
        WHERE finished_at IS NULL
          AND status NOT IN (
              'ready_for_review', 'needs_review', 'needs_info', 'quarantined',
              'approved', 'rejected', 'delivered', 'failed_terminal'
          )
          AND started_at < datetime('now', ?)
        ORDER BY started_at
        """,
        (f"-{minutes} minutes",),
    )
    return [(row["run_id"], row["candidate_id"], row["status"]) for row in rows]


# --- panel 2: cost ----------------------------------------------------------------


@dataclass(frozen=True)
class CostSummary:
    """What a set of runs cost, or the honest absence of a figure."""

    candidates: int = 0
    total_usd: Decimal | None = None
    mean_per_candidate: Decimal | None = None
    per_hundred: Decimal | None = None
    total_input_tokens: int = 0
    total_output_tokens: int = 0
    llm_calls: int = 0

    @property
    def priced(self) -> bool:
        return self.total_usd is not None


def cost_by_policy(db_path: Path | str, *, days: int = DEFAULT_DAYS) -> dict[str, CostSummary]:
    """The routing claim, with a number behind it.

    Split by policy because that is the comparison: all-cheap and all-strong
    bound the cost, and routed has to earn its place between them. Tokens are
    reported whether or not money is, so the comparison still works unpriced.
    """
    rows = _rows(
        db_path,
        """
        SELECT
            routing_policy_id                AS policy,
            COUNT(*)                         AS candidates,
            SUM(total_cost_usd)              AS total_cost,
            SUM(total_input_tokens)          AS input_tokens,
            SUM(total_output_tokens)         AS output_tokens,
            SUM(llm_call_count)              AS calls
        FROM runs
        WHERE started_at >= datetime('now', ?)
        GROUP BY routing_policy_id
        ORDER BY policy
        """,
        (_since(days),),
    )

    summaries: dict[str, CostSummary] = {}
    for row in rows:
        candidates = row["candidates"] or 0
        total = _decimal(row["total_cost"])

        summaries[row["policy"]] = CostSummary(
            candidates=candidates,
            total_usd=total,
            mean_per_candidate=(total / candidates) if total is not None and candidates else None,
            per_hundred=(total / candidates * 100) if total is not None and candidates else None,
            total_input_tokens=row["input_tokens"] or 0,
            total_output_tokens=row["output_tokens"] or 0,
            llm_calls=row["calls"] or 0,
        )
    return summaries


def _decimal(value: Any) -> Decimal | None:
    """A stored cost, or None.

    Costs are stored as text to keep the decimal exact. NULL means unpriced and
    stays NULL: nothing here substitutes a zero.
    """
    if value is None or value == "":
        return None
    try:
        return Decimal(str(value))
    except Exception:
        return None


# --- panel 3: latency ---------------------------------------------------------------


def _percentile(values: list[float], fraction: float) -> float | None:
    """Nearest-rank, computed here rather than in SQL.

    SQLite has no percentile function, and the alternatives are a window-function
    expression nobody can read or an extension nobody has installed.
    """
    if not values:
        return None
    ordered = sorted(values)
    index = min(round(fraction * (len(ordered) - 1)), len(ordered) - 1)
    return ordered[index]


def run_latency(db_path: Path | str, *, days: int = DEFAULT_DAYS) -> dict[str, float | None]:
    """How long a candidate takes, end to end."""
    rows = _rows(
        db_path,
        """
        SELECT (julianday(finished_at) - julianday(started_at)) * 86400.0 AS seconds
        FROM runs
        WHERE finished_at IS NOT NULL AND started_at >= datetime('now', ?)
        """,
        (_since(days),),
    )
    values = [row["seconds"] for row in rows if row["seconds"] is not None]
    return {
        "count": len(values),
        "p50": _percentile(values, 0.5),
        "p95": _percentile(values, 0.95),
    }


def call_latency(
    db_path: Path | str, *, call_site: str = "assess.criterion", days: int = DEFAULT_DAYS
) -> dict[str, float | None]:
    """How long one model call takes, for the call site that dominates a run."""
    rows = _rows(
        db_path,
        """
        SELECT latency_ms FROM llm_calls
        WHERE call_site = ? AND occurred_at >= datetime('now', ?)
        """,
        (call_site, _since(days)),
    )
    values = [float(row["latency_ms"]) for row in rows if row["latency_ms"] is not None]
    return {
        "count": len(values),
        "p50": _percentile(values, 0.5),
        "p95": _percentile(values, 0.95),
    }


# --- panel 4: escalation -------------------------------------------------------------


def escalation_by_trigger(db_path: Path | str) -> list[tuple[str, int, int, float | None]]:
    """Whether escalation earns its cost.

    Two numbers per trigger, and the second is the one that matters. How often a
    trigger fires is a fact about the corpus; how often escalating *changed the
    answer* is a fact about whether the trigger is worth paying for.

    A trigger that changes nothing across a benchmark is a finding, and switching
    it off is a result worth writing down.
    """
    rows = _rows(
        db_path,
        """
        SELECT
            escalation_trigger                        AS trigger,
            COUNT(*)                                  AS escalations,
            SUM(CASE WHEN escalation_changed_state THEN 1 ELSE 0 END) AS changed
        FROM assessments
        WHERE escalated = 1 AND escalation_trigger IS NOT NULL
        GROUP BY escalation_trigger
        ORDER BY escalations DESC
        """,
    )
    return [
        (
            row["trigger"],
            row["escalations"],
            row["changed"] or 0,
            (row["changed"] or 0) / row["escalations"] if row["escalations"] else None,
        )
        for row in rows
    ]


def escalation_rate(db_path: Path | str) -> float | None:
    """The fraction of criteria that were read twice."""
    row = _one(
        db_path,
        "SELECT COUNT(*) AS total, SUM(CASE WHEN escalated THEN 1 ELSE 0 END) AS n"
        " FROM assessments",
    )
    if row is None or not row["total"]:
        return None
    return (row["n"] or 0) / row["total"]


# --- panel 5: failures ----------------------------------------------------------------


def error_codes(db_path: Path | str, *, days: int = DEFAULT_DAYS) -> list[tuple[str, int]]:
    """Where the system actually breaks, as opposed to where people expect it to."""
    rows = _rows(
        db_path,
        """
        SELECT error_code, COUNT(*) AS n
        FROM errors
        WHERE occurred_at >= datetime('now', ?)
        GROUP BY error_code
        ORDER BY n DESC
        """,
        (_since(days),),
    )
    return [(row["error_code"], row["n"]) for row in rows]


def failure_rates(db_path: Path | str, *, days: int = DEFAULT_DAYS) -> dict[str, float | None]:
    """The four rates worth watching, per run.

    Invalid spans are the headline: it is the hallucination rate, measured
    mechanically rather than judged.
    """
    row = _one(
        db_path,
        """
        SELECT
            COUNT(*)                       AS runs,
            SUM(validation_failure_count)  AS validation_failures,
            SUM(invalid_span_count)        AS invalid_spans,
            SUM(retry_count)               AS retries,
            SUM(repair_count)              AS repairs,
            SUM(llm_call_count)            AS calls
        FROM runs
        WHERE started_at >= datetime('now', ?)
        """,
        (_since(days),),
    )
    if row is None or not row["runs"]:
        return {"runs": 0}

    calls = row["calls"] or 0
    return {
        "runs": row["runs"],
        "validation_failure_rate": (row["validation_failures"] or 0) / calls if calls else None,
        "invalid_span_rate": (row["invalid_spans"] or 0) / calls if calls else None,
        "retry_rate": (row["retries"] or 0) / calls if calls else None,
        "repair_rate": (row["repairs"] or 0) / calls if calls else None,
    }


# --- panel 6: the people using it -------------------------------------------------------


def review_effort(db_path: Path | str) -> dict[str, float | None]:
    """The productivity claim, measured rather than estimated.

    Median as well as mean, because one interrupted review that stayed open over
    lunch would otherwise move the average and make the claim look worse than it
    is — or, with the opposite mistake, better.
    """
    rows = _rows(db_path, "SELECT elapsed_seconds FROM reviews")
    values = [float(row["elapsed_seconds"]) for row in rows if row["elapsed_seconds"] is not None]
    if not values:
        return {"reviews": 0}

    return {
        "reviews": len(values),
        "mean_minutes": sum(values) / len(values) / 60,
        "median_minutes": (_percentile(values, 0.5) or 0) / 60,
        "p95_minutes": (_percentile(values, 0.95) or 0) / 60,
    }


def override_rate(db_path: Path | str) -> dict[str, float | None]:
    """How often a person disagreed with the machine."""
    row = _one(
        db_path,
        """
        SELECT COUNT(*) AS decisions,
               SUM(CASE WHEN override_count > 0 THEN 1 ELSE 0 END) AS with_overrides,
               SUM(override_count) AS overrides
        FROM runs
        WHERE reviewer_action IS NOT NULL
        """,
    )
    if row is None or not row["decisions"]:
        return {"decisions": 0}

    return {
        "decisions": row["decisions"],
        "rate": (row["with_overrides"] or 0) / row["decisions"],
        "overrides": row["overrides"] or 0,
    }


def override_reasons(db_path: Path | str) -> list[tuple[str, int]]:
    """Which kind of disagreement, so the fix can be routed.

    The most valuable table in the system. Each reason points somewhere
    different: at a prompt, at the rubric, or at a limitation worth writing down
    rather than pretending away.
    """
    rows = _rows(
        db_path,
        """
        SELECT reason_code, COUNT(*) AS n
        FROM overrides
        GROUP BY reason_code
        ORDER BY n DESC
        """,
    )
    return [(row["reason_code"], row["n"]) for row in rows]


def trust_distribution(db_path: Path | str) -> list[tuple[int, int]]:
    """What reviewers said about whether they believed it."""
    rows = _rows(
        db_path,
        """
        SELECT trust_rating, COUNT(*) AS n
        FROM reviews
        WHERE trust_rating IS NOT NULL
        GROUP BY trust_rating
        ORDER BY trust_rating
        """,
    )
    return [(row["trust_rating"], row["n"]) for row in rows]


class SqliteMetricsReader:
    """The ``MetricsReader`` the operations page receives.

    One method that calls the queries above in one pass. It exists so the
    application layer can ask for a snapshot without knowing that the answers
    come out of SQLite, and so the same numbers can be produced for a report.
    """

    def __init__(self, db_path: Path | str) -> None:
        self.db_path = Path(db_path)

    def snapshot(
        self, *, days: int = DEFAULT_DAYS, stale_after_minutes: int = 15
    ) -> MetricsSnapshot:
        db = self.db_path

        return MetricsSnapshot(
            days=days,
            runs_by_status=tuple(runs_by_status(db, days=days)),
            stuck=tuple(stuck_runs(db, minutes=stale_after_minutes)),
            cost_by_policy={
                policy: PolicyCost(
                    candidates=summary.candidates,
                    total_usd=summary.total_usd,
                    mean_per_candidate=summary.mean_per_candidate,
                    per_hundred=summary.per_hundred,
                    total_input_tokens=summary.total_input_tokens,
                    total_output_tokens=summary.total_output_tokens,
                    llm_calls=summary.llm_calls,
                )
                for policy, summary in cost_by_policy(db, days=days).items()
            },
            run_latency=run_latency(db, days=days),
            call_latency=call_latency(db, days=days),
            escalation_rate=escalation_rate(db),
            escalation_by_trigger=tuple(escalation_by_trigger(db)),
            failure_rates=failure_rates(db, days=days),
            error_codes=tuple(error_codes(db, days=days)),
            review_effort=review_effort(db),
            override_rate=override_rate(db),
            override_reasons=tuple(override_reasons(db)),
            trust_distribution=tuple(trust_distribution(db)),
        )
