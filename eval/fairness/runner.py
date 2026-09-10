"""``make eval-fairness``. Three arms, and the floor beside all of them.

Blind mode off, blind mode on, and a control that runs each base CV three times
with identity held fixed. The control is not optional and not a nice-to-have: it
is what makes the other two numbers mean anything, and the flip rate refuses to
render without it.

The comparison that matters is between the arms, not the size of any one figure.
If blind mode moves the flip rate below the floor, that is a result. If it does
not move it at all, that is also a result, and it belongs in the report rather
than in a drawer.
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from application.deps import Deps
from application.use_cases.process_candidate import process_candidate
from domain.ports.sources import CandidateRef, DocumentRef
from eval.arms import stand_in
from eval.fairness import build_pairs, flip_rate
from eval.fairness.personas import Persona, reference
from eval.fairness.personas import load as load_personas
from eval.runner import fingerprint
from infrastructure.factory import build_deps, settings_from_env
from infrastructure.integrations.local import LocalFolderSource

#: How many times each base CV is run with identity fixed. Three pairs per base
#: from three runs, which is enough to see whether the system is deterministic
#: at all without tripling the cost of the whole experiment.
CONTROL_RUNS = 3

#: The role every variant is assessed against. One role, so the rubric is not a
#: second variable.
ROLE_ID = "ai-engineer"


@dataclass
class RunOutcome:
    """What one run of one variant produced."""

    band: Any = None
    criterion_states: dict[str, Any] | None = None
    score: float | None = None


def run_variant(text: str, name: str, deps: Deps, root: Path, *, model_for: Any) -> RunOutcome:
    """One document, through the real use case."""
    path = root / name
    path.write_text(text, encoding="utf-8")

    wired = Deps(
        **{
            **deps.__dict__,
            "source": LocalFolderSource(root),
            "models": model_for(deps),
        }
    )

    candidate = CandidateRef(
        candidate_id=Path(name).stem,
        documents=(
            DocumentRef(candidate_id=Path(name).stem, filename=name, external_ref=str(path)),
        ),
        source_id="fairness",
    )

    result = process_candidate(candidate, ROLE_ID, wired, force=True)
    recommendation = wired.evidence.recommendation_for_run(result.run_id)

    return RunOutcome(
        band=getattr(recommendation, "band", None),
        criterion_states=dict(getattr(recommendation, "criterion_states", {}) or {}),
        score=getattr(recommendation, "score", None),
    )


def measure_arm(
    label: str,
    *,
    blind_mode: bool,
    pairs: build_pairs.PairSet,
    who: Persona,
    root: Path,
    floor: flip_rate.FlipRate | None,
) -> flip_rate.FlipRate:
    """One configuration, across every pair."""
    settings = settings_from_env(
        db_path=str(root / f"{label}.sqlite"),
        blob_dir=str(root / "blobs"),
        blind_mode=blind_mode,
        sanitize_render_diff=False,
    )
    deps = build_deps(settings)
    documents = root / label
    documents.mkdir(parents=True, exist_ok=True)

    comparisons = []
    for base, variant in pairs.pairs_against(who):
        first = run_variant(base.text, base.filename, deps, documents, model_for=_model_for)
        second = run_variant(variant.text, variant.filename, deps, documents, model_for=_model_for)

        comparisons.append(
            flip_rate.Comparison(
                base_id=base.base_id,
                persona_a=base.persona.id,
                persona_b=variant.persona.id,
                band_a=first.band,
                band_b=second.band,
                states_a=first.criterion_states or {},
                states_b=second.criterion_states or {},
                score_a=first.score,
                score_b=second.score,
            )
        )

    return flip_rate.compute(
        comparisons,
        label=label,
        noise_floor=floor,
        config_fingerprint=fingerprint(deps),
    )


def measure_floor(pairs: build_pairs.PairSet, who: Persona, root: Path) -> flip_rate.FlipRate:
    """The same document, three times, with identity fixed.

    Measured the same way the disparity is — same pairing, same denominator
    shape, same arithmetic — because a floor computed differently from the thing
    it bounds does not bound it.
    """
    settings = settings_from_env(
        db_path=str(root / "control.sqlite"),
        blob_dir=str(root / "blobs"),
        sanitize_render_diff=False,
    )
    deps = build_deps(settings)
    documents = root / "control"
    documents.mkdir(parents=True, exist_ok=True)

    runs: dict[str, list[RunOutcome]] = {}
    for base_id, group in pairs.variants.items():
        variant = next(v for v in group if v.persona.id == who.id)
        runs[base_id] = [
            run_variant(
                variant.text,
                f"{base_id}-run{index}.txt",
                deps,
                documents,
                model_for=_model_for,
            )
            for index in range(CONTROL_RUNS)
        ]

    return flip_rate.compute(
        flip_rate.self_consistency(runs),
        label="control: identity held fixed",
        # This measurement *is* the floor. Nothing is read against it, which is
        # a different state from having no floor, and the two must not render
        # the same way.
        is_control=True,
        config_fingerprint=fingerprint(deps),
    )


def _model_for(deps: Deps) -> Any:
    """The deterministic stand-in, so a flip is the system changing its mind
    rather than a model sampling differently.

    That matters more here than anywhere else: with a sampling model, the noise
    floor would absorb the entire experiment and the result would be
    uninterpretable by construction.
    """
    return stand_in.for_rubric(deps.rubric_loader(ROLE_ID))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, default=Path("eval/results/fairness"))
    args = parser.parse_args(argv)

    args.out.mkdir(parents=True, exist_ok=True)

    personas = load_personas()
    who = reference(personas)
    pairs = build_pairs.build(personas)

    print(
        f"{pairs.total} variants from {len(pairs.variants)} base CVs and "
        f"{len(personas)} personas.\n"
    )

    floor = measure_floor(pairs, who, args.out)
    blind_off = measure_arm(
        "blind mode off", blind_mode=False, pairs=pairs, who=who, root=args.out, floor=floor
    )
    blind_on = measure_arm(
        "blind mode on", blind_mode=True, pairs=pairs, who=who, root=args.out, floor=floor
    )

    report = flip_rate.render_comparison([floor, blind_off, blind_on])
    print(report)

    (args.out / "fairness.txt").write_text(report, encoding="utf-8")
    print(f"\nwritten to {args.out / 'fairness.txt'}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
