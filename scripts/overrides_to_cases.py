"""Turn what reviewers corrected into draft evaluation cases.

Every override is a reviewer saying "the system got this criterion wrong, and
here is the right answer". That is a gold label, produced by the person best
placed to produce one, in the course of work they were doing anyway. Leaving it
in a table is how an improvement loop stays a diagram.

So this reads the overrides, groups them by run, and writes one draft
``EvaluationCase`` per run with the reviewer's states as the expected ones.
Draft, in the file name and in the header, because two things are missing that
only a person can supply: the document (real pilot documents never enter the
repository, so the case points at a path somebody has to replace with a
synthetic equivalent) and the band (an override changes criteria, not the
decision, and inferring the band from the corrected states would be the system
grading its own homework).

    python -m scripts.overrides_to_cases
    python -m scripts.overrides_to_cases --role ai-engineer --out eval/cases/drafts

Nothing here reads a document or a quotation. The override table holds
criterion ids and states, and that is all that is written out.
"""

from __future__ import annotations

import argparse
from collections import defaultdict
from pathlib import Path
from uuid import UUID

import yaml

from infrastructure.factory import build_deps, settings_from_env

DEFAULT_OUT = Path("eval/cases/drafts")

#: Written at the top of every draft so nobody runs one as a real case.
HEADER = (
    "# DRAFT. Generated from reviewer overrides by scripts/overrides_to_cases.py.\n"
    "# Before this becomes a case: replace `documents` with a synthetic document\n"
    "# that reproduces what the reviewer saw, decide `expected_band` or leave it\n"
    "# null, and move the file out of drafts/.\n"
)


def collect(deps: object, *, role_id: str | None = None) -> dict[UUID, dict[str, str]]:
    """Corrected states, by run. The reviewer's answer wins over the system's."""
    by_run: dict[UUID, dict[str, str]] = defaultdict(dict)
    for run_id, criterion_id, _previous, new_state, _reason in deps.reviews.list_overrides(  # type: ignore[attr-defined]
        role_id=role_id
    ):
        by_run[run_id][criterion_id] = new_state
    return dict(by_run)


def draft_for(deps: object, run_id: UUID, states: dict[str, str]) -> dict[str, object]:
    """One case, with what is known filled in and what is not marked."""
    record = deps.runs.get(run_id)  # type: ignore[attr-defined]
    role_id = getattr(record, "role_id", "unknown")
    candidate = getattr(record, "candidate_id", str(run_id)[:8])

    return {
        "case_id": f"draft-{str(run_id)[:8]}",
        "name": f"Reviewer corrected {len(states)} criterion(s) on {candidate}",
        "description": (
            "Drafted from overrides. The reviewer changed the criteria below; the "
            "document must be replaced with a synthetic one before this is run."
        ),
        "role_id": role_id,
        "documents": [f"REPLACE-ME/{candidate}.txt"],
        "tags": ["draft", "from-overrides"],
        "arms": ["c"],
        "expected": {
            "expected_band": None,
            "criterion_states": dict(sorted(states.items())),
            "must_be_insufficient": sorted(
                criterion for criterion, state in states.items() if state == "insufficient_evidence"
            ),
            "must_flag_integrity": False,
            "forbidden_claims": [],
            "notes": "States come from a reviewer's overrides. Band not asserted.",
        },
    }


def write_drafts(deps: object, out: Path, *, role_id: str | None = None) -> list[Path]:
    out.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []

    for run_id, states in collect(deps, role_id=role_id).items():
        case = draft_for(deps, run_id, states)
        path = out / f"{case['case_id']}.yaml"
        body = yaml.safe_dump(case, sort_keys=False, allow_unicode=True)
        path.write_text(HEADER + body, encoding="utf-8")
        written.append(path)

    return written


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Draft evaluation cases from reviewer overrides.")
    parser.add_argument("--role", default=None, help="only this role's runs")
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    args = parser.parse_args(argv)

    deps = build_deps(settings_from_env())
    written = write_drafts(deps, args.out, role_id=args.role)

    if not written:
        print("No overrides recorded yet, so there is nothing to draft.")
        return 0

    print(f"wrote {len(written)} draft case(s) under {args.out}:")
    for path in written:
        print(f"  {path}")
    print("Each is a draft: replace the document and decide the band before using it.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
