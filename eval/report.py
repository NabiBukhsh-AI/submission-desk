"""The report, and the one thing it will not do.

It will not render a proportion without an n. Not "should not" — the function
that formats a rate raises if the denominator is missing, so a template cannot
produce "94% accurate" by leaving out the part that makes it meaningful.

That is the only real rule here. Everything else is layout: a table of cases so
a reader can find the one that surprises them, a summary so they can see the
shape, and the caveats at the top rather than in a footnote, because a report
whose limitations are at the bottom is a report designed to be misread.

No template engine. The HTML is small, the substitution is one function, and a
dependency that exists to insert a variable into a string is a dependency that
also has to be kept, audited and explained.
"""

from __future__ import annotations

import html
from typing import Any


class UnmeasuredMetric(Exception):
    """A rate was asked for where there is no denominator.

    Raised rather than rendered as "0%" or "—", because a template that can
    quietly print a rate with no n is the mechanism by which a report ends up
    claiming something nobody measured.
    """


def rate(entry: dict[str, Any] | None, places: int = 1) -> str:
    """A proportion as a reader should see it: value, interval, and n.

    Raises when the entry has no n. The caller's options are to show the figure
    honestly or to say it was not measured; there is no third option where the
    number appears without its denominator.
    """
    if not entry:
        raise UnmeasuredMetric("no metric was supplied")

    n = entry.get("n")
    if n is None:
        raise UnmeasuredMetric("a proportion was supplied without its n")

    if not n or entry.get("value") is None:
        return "not measured (n=0)"

    value = float(entry["value"])
    interval = entry.get("interval")

    if not interval:
        return f"{value * 100:.{places}f}% (n={n})"

    low, high = interval
    return f"{value * 100:.{places}f}% [{low * 100:.{places}f}–{high * 100:.{places}f}] (n={n})"


def number(value: Any, places: int = 2, suffix: str = "") -> str:
    """A figure, or the honest absence of one. Never a zero standing in for
    nothing."""
    if value is None:
        return "not measured"
    return f"{float(value):.{places}f}{suffix}"


#: What each headline metric is, in the words a reader needs to judge it.
#: Written here rather than in the template so the wording is reviewable as a
#: set, and so a number never appears without an explanation of what it means.
DESCRIPTIONS: dict[str, tuple[str, str]] = {
    "band_accuracy": (
        "Band matches the label",
        "How often the recommendation matched the recorded decision exactly.",
    ),
    "band_within_one": (
        "Band within one step",
        "Bands are ordered, so being one step out is a different kind of error "
        "from being three. Reported alongside the exact figure because a single "
        "accuracy number hides which is happening.",
    ),
    "criterion_accuracy": (
        "Criterion matches the label",
        "Per criterion rather than per candidate, so getting a band right by "
        "getting two criteria wrong in opposite directions is visible.",
    ),
    "abstention_accuracy": (
        "Said so when it could not tell",
        "The half most evaluations omit, and the one this system's argument "
        "rests on. Measured over the criteria the label marks as unanswerable "
        "from the document: inventing an answer there is wrong, not lucky.",
    ),
    "hallucination_rate": (
        "Quotations not found in the document",
        "Measured mechanically. Every quotation offered is an opportunity and "
        "every one the validator could not locate is a failure. Lower is "
        "better.",
    ),
    "forbidden_claim_rate": (
        "Claims the case forbids",
        "Specific fabrications each document invites — a number an injection "
        "asks for, an inference a sparse CV tempts. Lower is better.",
    ),
    "integrity_flag_accuracy": (
        "Flagged the right documents",
        "Both directions in one figure, because a scanner that flags everything "
        "is as useless as one that flags nothing.",
    ),
    "escalation_rate": (
        "Criteria read twice",
        "Not a quality measure. Reported because escalation costs money and the "
        "question is whether it changes any answers.",
    ),
}

#: Metrics where a lower number is the better one, so a reader is not left
#: inferring direction from the metric's name.
LOWER_IS_BETTER = frozenset({"hallucination_rate", "forbidden_claim_rate"})


def render(result: Any) -> str:
    """The whole report as one HTML page."""
    summary = (result.summaries or {}).get("c", {})

    return "\n".join(
        [
            _head(result),
            _notes(result),
            _headline(summary),
            _cost(summary),
            _cases(result.rows),
            _footer(result),
        ]
    )


def _head(result: Any) -> str:
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<title>Submission Desk — evaluation, {esc(result.split)} split</title>
<style>
 body {{ font: 15px/1.55 system-ui, sans-serif; margin: 2rem auto; max-width: 62rem;
        color: #1a1a1a; }}
 h1 {{ margin-bottom: 0.2rem; }}
 .meta {{ color: #555; font-size: 0.9rem; }}
 .notes {{ background: #fff8e1; border-left: 3px solid #e0a800; padding: 0.8rem 1rem;
          margin: 1.4rem 0; }}
 .notes p {{ margin: 0.35rem 0; }}
 table {{ border-collapse: collapse; width: 100%; margin: 1rem 0 2rem; }}
 th, td {{ border-bottom: 1px solid #e3e3e3; padding: 0.45rem 0.6rem;
           text-align: left; vertical-align: top; }}
 th {{ background: #f6f6f6; font-weight: 600; }}
 td.num {{ font-variant-numeric: tabular-nums; white-space: nowrap; }}
 .what {{ color: #555; font-size: 0.87rem; }}
 .miss {{ color: #b00020; }}
 .unmeasured {{ color: #777; font-style: italic; }}
</style></head><body>
<h1>Evaluation — {esc(result.split)} split</h1>
<p class="meta">Run {esc(result.run_at)} · configuration
<code>{esc(result.config_fingerprint)}</code></p>"""


def _notes(result: Any) -> str:
    """The caveats, first.

    A report whose limitations are at the bottom is a report designed to be
    misread, and the limitation that matters most here is the size of n.
    """
    items = "".join(f"<p>{esc(note)}</p>" for note in result.notes)
    return f'<div class="notes"><strong>Before reading these numbers</strong>{items}</div>'


def _headline(summary: dict[str, Any]) -> str:
    rows = []
    for key, (label, explanation) in DESCRIPTIONS.items():
        entry = summary.get(key)
        try:
            figure = rate(entry)
        except UnmeasuredMetric:
            figure = '<span class="unmeasured">not measured</span>'

        direction = " (lower is better)" if key in LOWER_IS_BETTER else ""
        rows.append(
            f"<tr><td><strong>{esc(label)}</strong>{esc(direction)}"
            f'<div class="what">{esc(explanation)}</div></td>'
            f'<td class="num">{figure}</td></tr>'
        )

    kappa = summary.get("kappa_against_gold")
    rows.append(
        "<tr><td><strong>Agreement with the label, chance-corrected</strong>"
        '<div class="what">Raw agreement flatters any rater on a skewed set: '
        "answering advance every time scores well if most cases are advance. "
        "Kappa subtracts what chance would have achieved.</div></td>"
        f'<td class="num">{number(kappa)}</td></tr>'
    )

    return (
        "<h2>What was measured</h2>"
        "<table><tr><th>Metric</th><th>Result</th></tr>" + "".join(rows) + "</table>"
    )


def _cost(summary: dict[str, Any]) -> str:
    cost = summary.get("cost") or {}
    latency = summary.get("latency") or {}

    priced = cost.get("n", 0) and cost.get("mean") is not None
    cost_cell = (
        f"{number(cost.get('mean'), 4, ' USD')} mean, "
        f"{number(cost.get('total'), 4, ' USD')} total (n={cost.get('n', 0)})"
        if priced
        else '<span class="unmeasured">pricing not configured — token counts '
        "are real, the conversion to money is not available</span>"
    )

    return (
        "<h2>Cost and speed</h2>"
        "<table>"
        f"<tr><th>Cost per candidate</th><td class='num'>{cost_cell}</td></tr>"
        f"<tr><th>Latency per candidate</th><td class='num'>"
        f"{number(latency.get('median'), 0, ' ms')} median, "
        f"{number(latency.get('p95'), 0, ' ms')} at the 95th percentile "
        f"(n={latency.get('n', 0)})</td></tr>"
        "</table>"
    )


def _cases(rows: list[dict[str, Any]]) -> str:
    """Every case, so a reader can find the one that surprises them.

    An aggregate nobody can decompose is an aggregate nobody can check.
    """
    if not rows:
        return "<h2>Cases</h2><p>No cases were run.</p>"

    body = []
    for row in rows:
        matched = row.get("band_matches")
        cell_class = "num miss" if matched == 0 else "num"
        body.append(
            "<tr>"
            f"<td><code>{esc(row['case_id'])}</code><br>{esc(row.get('name', ''))}</td>"
            f"<td class='num'>{esc(row.get('expected_band') or '—')}</td>"
            f"<td class='{cell_class}'>{esc(row.get('band') or '—')}</td>"
            f"<td class='num'>{row.get('invalid_spans', 0)}/{row.get('evidence_items', 0)}</td>"
            f"<td class='num'>{row.get('escalations', 0)}</td>"
            f"<td class='num'>{esc(row.get('cost_usd') or 'n/a')}</td>"
            f"<td class='num'>{row.get('latency_ms', 0)} ms</td>"
            "</tr>"
        )

    return (
        "<h2>Cases</h2><table><tr>"
        "<th>Case</th><th>Label</th><th>Result</th>"
        "<th>Quotations rejected</th><th>Escalations</th>"
        "<th>Cost</th><th>Latency</th></tr>" + "".join(body) + "</table>"
    )


def _footer(result: Any) -> str:
    return (
        '<p class="meta">Every proportion above carries the number of '
        "observations it was computed over. A figure without one is not "
        "reported.</p></body></html>"
    )


def esc(value: Any) -> str:
    return html.escape(str(value if value is not None else ""))
