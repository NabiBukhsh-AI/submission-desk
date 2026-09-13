"""The naive baseline against the final system, on a real model.

Arm B is what somebody reaches for on a Friday afternoon when asked to "just
use the LLM": the whole document and the whole rubric in one call, a band
back. Arm C is the eleven-node pipeline. Same twelve cases, same model, same
prices, run back to back, so the difference is the architecture and nothing
else.

Needs a provider: the stand-in would make both arms a claim about the
stand-in. Run with the key in the environment:

    MODEL_PROVIDER=anthropic MODEL_API_KEY=... MODEL_CHEAP_ID=claude-haiku-4-5 \\
    MODEL_STRONG_ID=claude-haiku-4-5 python -m eval.experiments.baseline

Writes ``eval/results/comparison/<date>-comparison.json`` and ``.md``: one row
per case per arm, then the summary of each arm with every proportion carrying
its n.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from domain.contracts.enums import ModelTier
from eval import metrics
from eval.arms import arm_b_naive
from eval.cases import documents as case_documents
from eval.runner import REPO_ROOT, RESULTS_DIR, labels_for, load_cases, run_suite, scratch_settings
from infrastructure.factory import build_deps

#: Statuses that mean a person has to act before anything leaves.
HUMAN_STATES = {"needs_review", "quarantined", "manual_review_required", "needs_info"}


@dataclass
class NaiveRow:
    case_id: str
    name: str
    expected_band: str | None
    band: str | None
    band_matches: bool | None
    forbidden_claim_present: bool
    justification: str
    input_tokens: int
    output_tokens: int
    cost_usd: str
    latency_ms: int
    failed_reason: str


@dataclass
class _Result:
    case_id: str
    band: Any
    criterion_states: dict[str, Any]
    cost_usd: Any
    latency_ms: int
    escalations: int
    invalid_spans: int
    metrics: dict[str, float]


def run_naive(deps: Any, cases: list[Any], root: Path) -> tuple[list[NaiveRow], list[_Result]]:
    """Every case through one call. Documents are read the way intake reads them."""
    rows: list[NaiveRow] = []
    results: list[_Result] = []
    for case in cases:
        rubric = deps.rubric_loader(case.role_id)
        documents = []
        for name in case.documents:
            path = root / name
            data = path.read_bytes()
            source = deps.extractor.extract(_document_for(case, path, data), data)
            documents.append((name, source.normalized_text))

        outcome = arm_b_naive.assess(documents, rubric, deps.models, nonce=f"naive-{case.case_id}")
        cost = deps.pricing.cost_of(
            ModelTier.CHEAP,
            input_tokens=outcome.input_tokens,
            output_tokens=outcome.output_tokens,
            cached_input_tokens=0,
        )
        forbidden = [phrase.lower() for phrase in (case.expected.forbidden_claims or [])]
        justification = outcome.justification or ""
        present = any(phrase in justification.lower() for phrase in forbidden)
        expected = _value(case.expected.expected_band)

        rows.append(
            NaiveRow(
                case_id=case.case_id,
                name=case.name,
                expected_band=expected,
                band=_value(outcome.band),
                band_matches=(_value(outcome.band) == expected) if expected else None,
                forbidden_claim_present=present,
                justification=justification[:400],
                input_tokens=outcome.input_tokens,
                output_tokens=outcome.output_tokens,
                cost_usd="" if cost is None else str(cost),
                latency_ms=outcome.latency_ms,
                failed_reason=outcome.failed_reason,
            )
        )
        results.append(
            _Result(
                case_id=case.case_id,
                band=outcome.band,
                criterion_states=arm_b_naive.criterion_states(outcome, rubric),
                cost_usd=cost,
                latency_ms=outcome.latency_ms,
                escalations=0,
                invalid_spans=0,
                metrics={
                    "evidence_items": 0,
                    "criteria_assessed": 0,
                    "integrity_flagged": 0,
                    "forbidden_claims_present": int(present),
                },
            )
        )
        answer = rows[-1].band or outcome.failed_reason
        print(f"  B {case.case_id:<9} {expected or '-':<26} -> {answer}")
    return rows, results


def _document_for(case: Any, path: Path, data: bytes) -> Any:
    from datetime import datetime as _dt  # noqa: PLC0415
    from hashlib import sha256  # noqa: PLC0415
    from uuid import uuid4  # noqa: PLC0415

    from domain.contracts import CandidateDocument, DocumentRole  # noqa: PLC0415

    digest = sha256(data).hexdigest()
    mime = {".pdf": "application/pdf", ".txt": "text/plain"}.get(
        path.suffix.lower(),
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    )
    return CandidateDocument(
        document_id=uuid4(),
        candidate_id=case.case_id,
        original_filename=path.name,
        document_sha256=digest,
        mime_type=mime,
        size_bytes=len(data),
        blob_path=f"blobs/{digest[:2]}/{digest}",
        doc_role=DocumentRole.CV,
        received_at=_dt.now(UTC),
    )


def _value(item: Any) -> str | None:
    if item is None:
        return None
    return str(getattr(item, "value", item))


def _proportion(item: metrics.Proportion) -> dict[str, Any]:
    return {"value": item.value, "n": item.n, "rendered": item.render()}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, default=RESULTS_DIR / "comparison")
    parser.add_argument("--split", default="all", choices=["dev", "holdout", "all"])
    parser.add_argument("--label", default="", help="a name for this run, in the file name")
    args = parser.parse_args(argv)

    prices = {
        "price_cheap_input": "1",
        "price_cheap_output": "5",
        "price_strong_input": "1",
        "price_strong_output": "5",
    }
    deps = build_deps(scratch_settings(args.out, **prices))
    if deps.settings.model_provider in ("", "fake"):
        print("A provider is needed: set MODEL_PROVIDER, MODEL_API_KEY and the tier ids.")
        return 1

    cases = load_cases(None if args.split == "all" else args.split)
    root = REPO_ROOT / "data" / "eval-documents"
    case_documents.materialise(root)
    labels = labels_for(cases)
    tiers = f"{deps.settings.model_cheap_id} / {deps.settings.model_strong_id}"
    print(f"{len(cases)} case(s), model {tiers}\n")

    started = time.monotonic()
    print("Arm B — one call, the whole rubric, a band back")
    naive_rows, naive_results = run_naive(deps, cases, root)
    naive_summary = metrics.summarise_arm("b", naive_results, labels)
    naive_seconds = time.monotonic() - started

    started = time.monotonic()
    print("\nArm C — the pipeline")
    final = run_suite(deps, split=f"final-{args.split}", cases=cases, documents_root=root)
    for row in final.rows:
        answer = row["band"] or row["status"]
        print(f"  C {row['case_id']:<9} {row['expected_band'] or '-':<26} -> {answer}")
    final_seconds = time.monotonic() - started

    run_at = datetime.now(UTC).isoformat()
    comparison: dict[str, Any] = {
        "run_at": run_at,
        "model": {"cheap": deps.settings.model_cheap_id, "strong": deps.settings.model_strong_id},
        "prices_usd_per_million": prices,
        "cases": len(cases),
        "arms": {
            "b": {
                "label": "one call, whole rubric, a band back",
                "summary": _summary(naive_summary),
                "wall_seconds": round(naive_seconds, 1),
                "human_touch": {
                    "rendered": "every case: nothing to check, so a person re-reads the document",
                },
                "rows": [asdict(row) for row in naive_rows],
            },
            "c": {
                "label": "the eleven-node pipeline",
                "summary": final.summaries["c"],
                "wall_seconds": round(final_seconds, 1),
                "human_touch": _human_touch(final.rows),
                "rows": final.rows,
            },
        },
    }

    args.out.mkdir(parents=True, exist_ok=True)
    stem = args.out / f"{run_at[:10]}-comparison{'-' + args.label if args.label else ''}"
    stem.with_suffix(".json").write_text(
        json.dumps(comparison, indent=2, default=str), encoding="utf-8"
    )
    stem.with_suffix(".md").write_text(render(comparison), encoding="utf-8")
    print(f"\nwrote {stem.with_suffix('.md')}")
    return 0


def _summary(summary: metrics.ArmSummary) -> dict[str, Any]:
    return {
        "band_accuracy": _proportion(summary.band_accuracy),
        "band_within_one": _proportion(summary.band_within_one),
        "criterion_accuracy": _proportion(summary.criterion_accuracy),
        "abstention_accuracy": _proportion(summary.abstention_accuracy),
        "hallucination_rate": _proportion(summary.hallucination_rate),
        "forbidden_claim_rate": _proportion(summary.forbidden_claim_rate),
        "integrity_flag_accuracy": _proportion(summary.integrity_flag_accuracy),
        "escalation_rate": _proportion(summary.escalation_rate),
        "cost": {
            "mean": summary.cost.mean if summary.cost else None,
            "n": summary.cost.n if summary.cost else 0,
        },
        "latency": {"mean": summary.latency.mean if summary.latency else None},
    }


def _human_touch(rows: list[dict[str, Any]]) -> dict[str, Any]:
    flagged = [row for row in rows if row.get("status") in HUMAN_STATES]
    return {
        "cases": len(flagged),
        "of": len(rows),
        "rendered": f"{len(flagged)} of {len(rows)} routed to a person with the reason on the run",
    }


def render(comparison: dict[str, Any]) -> str:
    """The comparison as a page somebody can read without the JSON."""
    b, c = comparison["arms"]["b"], comparison["arms"]["c"]
    lines = [
        f"# Baseline against final system — {comparison['run_at'][:10]}",
        "",
        f"{comparison['cases']} cases, model `{comparison['model']['cheap']}` on both tiers, "
        f"prices ${comparison['prices_usd_per_million']['price_cheap_input']} in / "
        f"${comparison['prices_usd_per_million']['price_cheap_output']} out per million tokens.",
        "",
        "| metric | B: one naive call | C: the pipeline |",
        "|---|---|---|",
    ]
    for key, label in (
        ("band_accuracy", "band accuracy"),
        ("band_within_one", "band within one"),
        ("abstention_accuracy", "abstention accuracy"),
        ("hallucination_rate", "hallucination rate"),
        ("forbidden_claim_rate", "forbidden-claim rate"),
        ("integrity_flag_accuracy", "integrity-flag accuracy"),
    ):
        lines.append(
            f"| {label} | {b['summary'][key]['rendered']} | {c['summary'][key]['rendered']} |"
        )
    cost_b, cost_c = _money(b["summary"]["cost"]), _money(c["summary"]["cost"])
    lat_b, lat_c = _seconds(b["summary"]["latency"]), _seconds(c["summary"]["latency"])
    lines += [
        f"| cost per candidate (USD) | {cost_b} | {cost_c} |",
        f"| latency per candidate | {lat_b} | {lat_c} |",
        f"| wall time, all cases | {b['wall_seconds']} s | {c['wall_seconds']} s |",
        f"| human touch | {b['human_touch']['rendered']} | {c['human_touch']['rendered']} |",
        "",
        "Arm B cannot answer per criterion, so it is scored as unable to say on every",
        "criterion. That makes its abstention accuracy 100% by construction — it abstains on",
        "everything, including the criteria the document does answer — and its criterion",
        "accuracy not a number. Its hallucination rate is not measured because it offers no",
        "quotation to check, which is the point: nothing it says can be verified.",
        "",
        "## Case by case",
        "",
        "| case | expected | B band | C band | C status | C invalid spans |",
        "|---|---|---|---|---|---|",
    ]
    by_id = {row["case_id"]: row for row in c["rows"]}
    for row in b["rows"]:
        final = by_id.get(row["case_id"], {})
        lines.append(
            f"| {row['case_id']} — {row['name']} | {row['expected_band'] or '—'} | "
            f"{row['band'] or row['failed_reason'] or '—'} | {final.get('band') or '—'} | "
            f"{final.get('status', '')} | {final.get('invalid_spans', '')} |"
        )
    lines += ["", "## What arm B said", ""]
    for row in b["rows"]:
        flag = " **(forbidden claim)**" if row["forbidden_claim_present"] else ""
        answer = row["band"] or row["failed_reason"]
        lines.append(f"- **{row['case_id']}** → {answer}{flag}: {row['justification']}")
    return "\n".join(lines) + "\n"


def _money(cost: dict[str, Any]) -> str:
    return (
        "not measured" if cost.get("mean") is None else f"{float(cost['mean']):.4f} (n={cost['n']})"
    )


def _seconds(latency: dict[str, Any]) -> str:
    return "—" if latency.get("mean") is None else f"{float(latency['mean']) / 1000:.1f} s"


if __name__ == "__main__":
    sys.exit(main())
