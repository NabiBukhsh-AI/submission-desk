"""The derivation, rendered for a person.

Pure by necessity as well as by rule: ``domain/rules/`` may not import a YAML
reader, so the templates arrive as a mapping the caller loaded. That keeps the
wording editable in ``explanations.yaml`` without putting file access in the
domain layer.

A rule with no template falls back to the description the rule engine already
wrote. A missing entry should degrade to plainer wording, never to a blank line
in front of a recruiter.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from domain.contracts.recommendation import DerivationStep, Recommendation


@dataclass(frozen=True)
class Explanation:
    """One rendered line of reasoning, with the rule that produced it."""

    rule_id: str
    text: str


def render_step(step: DerivationStep, templates: Mapping[str, str]) -> Explanation:
    """Render one derivation step.

    A template that names a placeholder the step did not supply falls back to
    the engine's own description rather than raising: a formatting mistake in an
    editable file must not take down a reviewer's page.
    """
    template = templates.get(step.rule_id)
    if not template:
        return Explanation(rule_id=step.rule_id, text=step.description)

    try:
        text = template.format(**step.inputs)
    except (KeyError, IndexError, ValueError):
        return Explanation(rule_id=step.rule_id, text=step.description)

    return Explanation(rule_id=step.rule_id, text=text.strip())


def explain(
    derivation: Sequence[DerivationStep],
    templates: Mapping[str, str] | None = None,
) -> list[Explanation]:
    """Render a whole derivation, in order."""
    templates = templates or {}
    return [render_step(step, templates) for step in derivation]


def explain_reasons(
    recommendation: Recommendation,
    reason_templates: Mapping[str, str] | None = None,
) -> list[str]:
    """Why this run wants a person, in plain sentences.

    An unknown reason code is shown as itself rather than dropped. A reviewer
    seeing an unfamiliar phrase can ask; a reviewer shown nothing cannot.
    """
    reason_templates = reason_templates or {}
    return [
        reason_templates.get(reason, reason) for reason in recommendation.requires_human_reasons
    ]
