"""Arm A: what a person did, read from a file.

The human baseline. Not a run, not a model, not anything this system produced —
a CSV of decisions a recruiter made before any of this existed, loaded so the
other two arms have something to be compared against.

It reads from ``eval/gold/``, which nothing outside ``eval/`` may touch. That
separation is enforced by a test rather than by discipline: a benchmark whose
answers are reachable from the system under test measures nothing, and the
failure would be invisible because the numbers would look excellent.

When the file is absent, this arm is reported as unmeasured. A baseline nobody
has recorded is not a baseline of zero, and inventing one would make every
comparison in the report a comparison against a number somebody made up.
"""

from __future__ import annotations

import csv
from dataclasses import dataclass
from pathlib import Path

from domain.contracts.enums import Band

#: Where the manual baseline lives. One path, so the leakage test has one thing
#: to look for.
GOLD_DIR = Path("eval/gold")
BASELINE_FILE = GOLD_DIR / "manual_baseline.csv"

#: What the file must contain. Named rather than inferred, so a column renamed
#: upstream produces a clear failure instead of a silently empty baseline.
COLUMNS = ("case_id", "band", "minutes", "reviewer_id")


@dataclass(frozen=True)
class ManualDecision:
    """One decision a person made, and how long it took them."""

    case_id: str
    band: Band
    minutes: float | None = None
    reviewer_id: str = ""


@dataclass(frozen=True)
class ManualBaseline:
    """The human arm, or the honest absence of one."""

    decisions: dict[str, ManualDecision]
    source: str = ""
    available: bool = False

    @property
    def minutes(self) -> list[float]:
        return [
            decision.minutes for decision in self.decisions.values() if decision.minutes is not None
        ]

    def band_for(self, case_id: str) -> Band | None:
        decision = self.decisions.get(case_id)
        return decision.band if decision else None


def load(path: Path | str = BASELINE_FILE) -> ManualBaseline:
    """Read the manual baseline, or report that there is not one.

    A missing file is not an error. This repository ships without one, because
    the decisions in it would be real people's judgements about real
    applications, and the alternative is inventing twelve of them.
    """
    path = Path(path)
    if not path.is_file():
        return ManualBaseline(decisions={}, source=str(path), available=False)

    decisions: dict[str, ManualDecision] = {}

    with path.open(encoding="utf-8", newline="") as handle:
        for row in csv.DictReader(handle):
            case_id = (row.get("case_id") or "").strip()
            band = _band(row.get("band"))
            if not case_id or band is None:
                continue

            decisions[case_id] = ManualDecision(
                case_id=case_id,
                band=band,
                minutes=_float(row.get("minutes")),
                reviewer_id=(row.get("reviewer_id") or "").strip(),
            )

    return ManualBaseline(decisions=decisions, source=str(path), available=bool(decisions))


def _band(value: object) -> Band | None:
    try:
        return Band(str(value).strip().lower())
    except ValueError:
        return None


def _float(value: object) -> float | None:
    try:
        return float(str(value).strip())
    except (TypeError, ValueError):
        return None
