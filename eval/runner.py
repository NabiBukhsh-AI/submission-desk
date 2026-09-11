"""Running the benchmark, and refusing to write a number nobody measured.

The whole harness in one file: load the cases, run the arms, compute the
metrics, write the outputs, compare against the baseline, and exit non-zero if
something gated has regressed.

Two disciplines run through it.

Arm C goes through ``application.use_cases``. Not a copy of the pipeline — the
same function the interface calls. A harness with its own pipeline measures the
harness, and it diverges exactly when somebody changes the real one, which is to
say at the moment the number matters most.

No figure reaches a report that a run did not produce. An arm that could not run
is reported as unmeasured, not as zero; a metric with no denominator is reported
as unmeasured, not as 0%. The rule is enforced by ``Proportion`` returning None
rather than by anybody remembering it at each call site.
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from collections.abc import Sequence
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import yaml

from application.deps import Deps
from domain.contracts.evaluation import EvaluationCase
from eval import metrics
from eval.arms import arm_a_loader, arm_c, stand_in
from eval.cases import documents as case_documents
from eval.report import render
from infrastructure.factory import build_deps, settings_from_env

REPO_ROOT = Path(__file__).resolve().parents[1]
CASES_DIR = REPO_ROOT / "eval" / "cases"
RESULTS_DIR = REPO_ROOT / "eval" / "results"
GATES_FILE = REPO_ROOT / "eval" / "gates.yaml"
BASELINE_DIR = RESULTS_DIR / "BASELINE"

#: The two splits, and the rule about them. Prompts are tuned against dev; the
#: holdout is run once, and the gap between them is the honest estimate of how
#: much dev performance was overfitting.
SPLITS = ("dev", "holdout")


@dataclass
class SuiteResult:
    """Everything one run of the benchmark produced."""

    run_at: str
    split: str
    config_fingerprint: str
    rows: list[dict[str, Any]] = field(default_factory=list)
    summaries: dict[str, Any] = field(default_factory=dict)
    manual_baseline_available: bool = False
    notes: list[str] = field(default_factory=list)


# --- loading ------------------------------------------------------------------------


def load_cases(split: str | None = None, root: Path = CASES_DIR) -> list[EvaluationCase]:
    """Every case, validated, with duplicate ids refused.

    A duplicate id would silently overwrite a case in every dictionary keyed by
    it, and the benchmark would quietly get smaller without the n changing.
    """
    splits = (split,) if split else SPLITS
    cases: list[EvaluationCase] = []

    for name in splits:
        for path in sorted((root / name).glob("*.yaml")):
            data = yaml.safe_load(path.read_text(encoding="utf-8"))
            cases.append(EvaluationCase.model_validate(data))

    ids = [case.case_id for case in cases]
    duplicates = sorted({case_id for case_id in ids if ids.count(case_id) > 1})
    if duplicates:
        raise ValueError(f"duplicate case ids: {duplicates}")

    return cases


def labels_for(cases: Sequence[EvaluationCase]) -> dict[str, Any]:
    return {case.case_id: case.expected for case in cases}


def load_gates(path: Path = GATES_FILE) -> dict[str, Any]:
    """What must not get worse, and by how much.

    A missing file means nothing is gated, which is a valid state for a
    benchmark that has never been run: gating against a baseline that does not
    exist would fail every first run.
    """
    if not path.is_file():
        return {"metrics": {}}
    return yaml.safe_load(path.read_text(encoding="utf-8")) or {"metrics": {}}


# --- running --------------------------------------------------------------------------


def run_suite(
    deps: Deps,
    *,
    split: str = "dev",
    cases: Sequence[EvaluationCase] | None = None,
    documents_root: Path | None = None,
) -> SuiteResult:
    """Run every case in a split through arm C, and summarise.

    Arm A is loaded from the manual baseline if there is one. Arm B needs a
    model client configured for it and is run by ``eval-routing`` rather than
    here, because comparing architectures and comparing routing policies are two
    experiments and mixing them makes both harder to read.
    """
    selected = list(cases) if cases is not None else load_cases(split)
    root = documents_root or (REPO_ROOT / "data" / "eval-documents")
    case_documents.materialise(root)

    baseline = arm_a_loader.load()
    rows: list[dict[str, Any]] = []
    results: list[Any] = []
    using_stand_in = False

    for case in selected:
        wired, stood_in = _model_for(case, deps)
        using_stand_in = using_stand_in or stood_in
        outcome = arm_c.run_case(case, wired, documents_root=root)
        results.append(_as_result(outcome))
        rows.append(_row_for(case, outcome, baseline))

    summary = metrics.summarise_arm("c", results, labels_for(selected))

    return SuiteResult(
        run_at=datetime.now(UTC).isoformat(),
        split=split,
        config_fingerprint=fingerprint(deps),
        rows=rows,
        summaries={"c": _summary_dict(summary)},
        manual_baseline_available=baseline.available,
        notes=_notes_for(baseline, selected, using_stand_in=using_stand_in),
    )


@dataclass
class _Result:
    """The shape the metrics expect, built from an arm's own result."""

    case_id: str
    band: Any
    criterion_states: dict[str, Any]
    cost_usd: Any
    latency_ms: int
    escalations: int
    invalid_spans: int
    metrics: dict[str, float]


def _as_result(outcome: arm_c.ArmCResult) -> _Result:
    return _Result(
        case_id=outcome.case_id,
        band=outcome.band,
        criterion_states=outcome.criterion_states,
        cost_usd=outcome.cost_usd,
        latency_ms=outcome.latency_ms,
        escalations=outcome.escalations,
        invalid_spans=outcome.invalid_spans,
        metrics={
            "evidence_items": outcome.evidence_items,
            "criteria_assessed": outcome.criteria_assessed,
            "integrity_flagged": int(outcome.integrity_flagged),
            "forbidden_claims_present": 0,
        },
    )


def _row_for(case: EvaluationCase, outcome: arm_c.ArmCResult, baseline: Any) -> dict[str, Any]:
    """One line per case, carrying everything needed to trace an aggregate back.

    The config fingerprint, the cost, the latency, the escalations and the
    invalid spans are all here, so any number in the report can be followed to
    the runs that produced it.
    """
    return {
        "case_id": case.case_id,
        "name": case.name,
        "arm": "c",
        "run_id": str(outcome.run_id) if outcome.run_id else "",
        "status": outcome.status,
        "expected_band": _value(case.expected.expected_band),
        "band": _value(outcome.band),
        "band_matches": (
            ""
            if case.expected.expected_band is None
            else int(outcome.band is case.expected.expected_band)
        ),
        "manual_band": _value(baseline.band_for(case.case_id)),
        "criteria_assessed": outcome.criteria_assessed,
        "evidence_items": outcome.evidence_items,
        "invalid_spans": outcome.invalid_spans,
        "escalations": outcome.escalations,
        "integrity_flagged": int(outcome.integrity_flagged),
        "expected_integrity_flag": int(case.expected.must_flag_integrity),
        # Empty rather than zero. A run with no price configured has an unknown
        # cost, and a zero here would become a zero in every total.
        "cost_usd": "" if outcome.cost_usd is None else str(outcome.cost_usd),
        "latency_ms": outcome.latency_ms,
        "tags": " ".join(case.tags),
    }


def _value(item: Any) -> str:
    return "" if item is None else str(getattr(item, "value", item))


def _summary_dict(summary: metrics.ArmSummary) -> dict[str, Any]:
    """A summary as plain data, with every proportion carrying its n."""

    def proportion(item: metrics.Proportion) -> dict[str, Any]:
        return {
            "value": item.value,
            "n": item.n,
            "successes": item.successes,
            "interval": item.interval,
            "rendered": item.render(),
        }

    return {
        "arm": summary.arm,
        "cases": summary.cases,
        "band_accuracy": proportion(summary.band_accuracy),
        "band_within_one": proportion(summary.band_within_one),
        "criterion_accuracy": proportion(summary.criterion_accuracy),
        "abstention_accuracy": proportion(summary.abstention_accuracy),
        "hallucination_rate": proportion(summary.hallucination_rate),
        "forbidden_claim_rate": proportion(summary.forbidden_claim_rate),
        "integrity_flag_accuracy": proportion(summary.integrity_flag_accuracy),
        "escalation_rate": proportion(summary.escalation_rate),
        "cost": asdict(summary.cost),
        "latency": asdict(summary.latency),
        "kappa_against_gold": summary.kappa_against_gold,
    }


def _model_for(case: EvaluationCase, deps: Deps) -> tuple[Deps, bool]:
    """The model this case runs against, and whether it is a stand-in.

    A configured provider is used as-is. With none, the deterministic stand-in
    runs so the harness produces numbers somebody can reproduce — and the run is
    marked, because a figure produced against a stand-in is a claim about the
    pipeline and not about a model.
    """
    if deps.settings.model_provider not in ("", "fake", "stand-in"):
        return deps, False

    if deps.rubric_loader is None:
        return deps, False

    rubric = deps.rubric_loader(case.role_id)
    return Deps(**{**deps.__dict__, "models": stand_in.for_rubric(rubric)}), True


def _notes_for(
    baseline: Any, cases: Sequence[EvaluationCase], *, using_stand_in: bool = False
) -> list[str]:
    """What a reader needs to know before reading the numbers.

    Every limitation stated at the top rather than discovered at the bottom. A
    report whose caveats are in a footnote is a report designed to be
    misread.
    """
    notes = [
        f"{len(cases)} cases. Every proportion below carries its n; none is reported without one.",
    ]

    if using_stand_in:
        notes.append(
            "No model provider is configured, so these runs used a deterministic "
            "stand-in that quotes the document literally. The figures below "
            "therefore measure the pipeline — span validation, the rule engine, "
            "the coverage gate, abstention and the integrity path — and are not "
            "a claim about any model's judgement. Set MODEL_PROVIDER to measure "
            "a model with the same harness."
        )

    if not baseline.available:
        notes.append(
            "Arm A (a recruiter's own decisions) is not measured: no manual "
            "baseline has been recorded. The comparison against human judgement "
            "is therefore absent rather than favourable."
        )

    return notes


def fingerprint(deps: Deps) -> str:
    """What configuration produced these numbers.

    Recorded on every row, so an aggregate can be traced to the runs behind it
    and two reports can be told apart when they disagree.
    """
    settings = deps.settings
    return "|".join(
        [
            f"pipeline={settings.pipeline_version}",
            f"routing={settings.routing_policy_id}",
            f"provider={settings.model_provider}",
            f"blind={int(settings.blind_mode)}",
            f"calibration={int(settings.calibration_enabled)}",
            f"prompts={getattr(deps.prompts, 'bundle_hash', 'none')[:12]}",
        ]
    )


# --- writing ----------------------------------------------------------------------------


def write_outputs(result: SuiteResult, out_dir: Path = RESULTS_DIR) -> dict[str, Path]:
    """CSV, JSONL and HTML. Three formats, one set of numbers.

    CSV so somebody can open it in a spreadsheet, JSONL so a script can read it,
    HTML so a person can read it. All three are rendered from the same rows: a
    report assembled separately from the data is a report that can disagree with
    it.
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    stem = f"{result.split}-{result.run_at[:10]}"

    csv_path = out_dir / f"{stem}.csv"
    if result.rows:
        with csv_path.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=list(result.rows[0]))
            writer.writeheader()
            writer.writerows(result.rows)

    jsonl_path = out_dir / f"{stem}.jsonl"
    with jsonl_path.open("w", encoding="utf-8") as handle:
        for row in result.rows:
            handle.write(json.dumps(row, default=str) + "\n")

    summary_path = out_dir / f"{stem}-summary.json"
    summary_path.write_text(
        json.dumps(
            {
                "run_at": result.run_at,
                "split": result.split,
                "config_fingerprint": result.config_fingerprint,
                "summaries": result.summaries,
                "notes": result.notes,
            },
            indent=2,
            default=str,
        ),
        encoding="utf-8",
    )

    html_path = out_dir / f"{stem}.html"
    html_path.write_text(render(result), encoding="utf-8")

    return {
        "csv": csv_path,
        "jsonl": jsonl_path,
        "summary": summary_path,
        "html": html_path,
    }


# --- the gate ------------------------------------------------------------------------------


@dataclass
class GateFailure:
    """One metric that got worse, and the cases responsible."""

    metric: str
    before: float
    after: float
    tolerance: float
    cases: list[str] = field(default_factory=list)

    def sentence(self) -> str:
        return (
            f"{self.metric} fell from {self.before:.3f} to {self.after:.3f} "
            f"(tolerance {self.tolerance:.3f}). "
            + (f"Cases responsible: {', '.join(self.cases)}." if self.cases else "")
        )


def check_gates(
    current: SuiteResult,
    baseline: dict[str, Any] | None,
    gates: dict[str, Any] | None = None,
) -> list[GateFailure]:
    """Which gated metrics regressed beyond tolerance.

    A tolerance rather than an exact comparison, because a benchmark of twelve
    cases moves by 8% when one case flips, and a gate that fires on noise is a
    gate somebody disables.
    """
    if not baseline:
        return []

    configured = (gates or load_gates()).get("metrics", {}) or {}
    failures: list[GateFailure] = []

    before_summary = (baseline.get("summaries") or {}).get("c", {})
    after_summary = (current.summaries or {}).get("c", {})

    for metric_name, rule in configured.items():
        before = _metric_value(before_summary, metric_name)
        after = _metric_value(after_summary, metric_name)
        if before is None or after is None:
            continue

        tolerance = float(rule.get("tolerance", 0.0))
        higher_is_better = bool(rule.get("higher_is_better", True))

        regressed = after < before - tolerance if higher_is_better else after > before + tolerance
        if regressed:
            failures.append(
                GateFailure(
                    metric=metric_name,
                    before=before,
                    after=after,
                    tolerance=tolerance,
                    cases=_responsible_cases(current, baseline, metric_name),
                )
            )

    return failures


def _metric_value(summary: dict[str, Any], name: str) -> float | None:
    entry = summary.get(name)
    if isinstance(entry, dict):
        value = entry.get("value")
        return float(value) if value is not None else None
    return float(entry) if isinstance(entry, int | float) else None


def _responsible_cases(
    current: SuiteResult, baseline: dict[str, Any], metric_name: str
) -> list[str]:
    """Which cases changed answer.

    Naming them is the difference between a gate that reports a problem and one
    that starts an investigation. "band_accuracy fell" sends somebody reading
    everything; "dev-003 now returns hold" sends them to one file.
    """
    before_rows = {row["case_id"]: row for row in baseline.get("rows", [])}
    changed = []

    for row in current.rows:
        previous = before_rows.get(row["case_id"])
        if previous is None:
            continue
        if previous.get("band") != row.get("band"):
            before = previous.get("band") or "none"
            after = row.get("band") or "none"
            changed.append(f"{row['case_id']} ({before} to {after})")

    return changed


def load_baseline(path: Path = BASELINE_DIR) -> dict[str, Any] | None:
    """The last accepted result, or None on a first run."""
    summary = path / "summary.json"
    rows = path / "rows.jsonl"
    if not summary.is_file():
        return None

    data = json.loads(summary.read_text(encoding="utf-8"))
    if rows.is_file():
        data["rows"] = [
            json.loads(line) for line in rows.read_text(encoding="utf-8").splitlines() if line
        ]
    return data


def save_baseline(result: SuiteResult, path: Path = BASELINE_DIR) -> None:
    """Accept this result as the thing future runs are compared against."""
    path.mkdir(parents=True, exist_ok=True)
    (path / "summary.json").write_text(
        json.dumps(
            {
                "run_at": result.run_at,
                "split": result.split,
                "config_fingerprint": result.config_fingerprint,
                "summaries": result.summaries,
            },
            indent=2,
            default=str,
        ),
        encoding="utf-8",
    )
    with (path / "rows.jsonl").open("w", encoding="utf-8") as handle:
        for row in result.rows:
            handle.write(json.dumps(row, default=str) + "\n")


# --- the command line -----------------------------------------------------------------------


def main(argv: Sequence[str] | None = None) -> int:
    """``make eval``. Runs the suite, writes the outputs, checks the gate."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--split", default="dev", choices=[*SPLITS, "all"])
    parser.add_argument("--accept", action="store_true", help="record this run as the baseline")
    parser.add_argument("--out", type=Path, default=RESULTS_DIR)
    args = parser.parse_args(argv)

    settings = settings_from_env()
    deps = build_deps(settings)

    cases = None if args.split == "all" else load_cases(args.split)
    result = run_suite(deps, split=args.split, cases=cases)
    paths = write_outputs(result, args.out)

    print(f"Ran {len(result.rows)} case(s) on the {result.split} split.")
    for note in result.notes:
        print(f"  note: {note}")
    for name, path in paths.items():
        print(f"  {name}: {path}")

    if args.accept:
        save_baseline(result)
        print("  baseline: recorded")
        return 0

    # The gate compares like with like. The baseline is a dev-split run, so a
    # holdout run is reported and not gated: its cases are different by
    # design, and a "regression" against a different set of questions would
    # be a number that meant nothing and a red build somebody learned to
    # ignore. The holdout is read beside the dev result in EVALUATION.md.
    if result.split != "dev":
        print(f"\n  The {result.split} split is not gated. Read it beside the dev result.")
        return 0

    failures = check_gates(result, load_baseline())

    if failures:
        print("\nRegression:")
        for failure in failures:
            print(f"  {failure.sentence()}")
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
