"""The deterministic rule engine.

Two pure functions turn span-validated evidence into a band with an auditable
derivation. Nothing here imports a model client, a repository, or anything that
can reach a network: given the same evidence and the same rubric, this produces
the same recommendation on any machine, forever.

That property is not tidiness. It is the precondition for the routing,
calibration, and fairness comparisons meaning anything, because each of those
experiments holds everything constant except one variable and reads the
difference.
"""

from __future__ import annotations

from domain.contracts.rubric_loader import RubricInvalid, load_rubric, rubric_hash
from domain.rules.aggregate import AggregationFlags, aggregate
from domain.rules.explain import Explanation, explain, explain_reasons, render_step
from domain.rules.resolve import CriterionResolution, resolve_criterion

__all__ = [
    "AggregationFlags",
    "CriterionResolution",
    "Explanation",
    "RubricInvalid",
    "aggregate",
    "explain",
    "explain_reasons",
    "load_rubric",
    "render_step",
    "resolve_criterion",
    "rubric_hash",
]
