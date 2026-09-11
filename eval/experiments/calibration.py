"""With and without calibration, over the same cases.

The experiment the disabled-by-default flag exists to wait for. Calibration
plausibly helps; nobody has shown that it does, and the honest place for such a
feature is behind a flag with this attached.

Everything else is held constant, so the difference between the two runs is
attributable to one variable. That is the only reason the comparison means
anything.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from eval.runner import RESULTS_DIR, load_cases, run_suite, scratch_settings, write_outputs
from infrastructure.factory import build_deps


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--split", default="dev")
    parser.add_argument("--out", type=Path, default=RESULTS_DIR / "calibration")
    args = parser.parse_args(argv)

    cases = load_cases(args.split)
    print(f"{len(cases)} case(s), calibration off then on.\n")

    for enabled in (False, True):
        deps = build_deps(scratch_settings(args.out, calibration_enabled=enabled))

        label = "on" if enabled else "off"
        result = run_suite(deps, split=f"{args.split}-calibration-{label}", cases=cases)
        write_outputs(result, args.out)

        summary = result.summaries["c"]
        print(f"calibration {label}:")
        print(f"  band accuracy   {summary['band_accuracy']['rendered']}")
        print(f"  criterion       {summary['criterion_accuracy']['rendered']}")
        print(f"  abstention      {summary['abstention_accuracy']['rendered']}")
        print()

    print(
        "A difference smaller than the intervals overlap is not a difference. "
        "At this n, only a large effect would be visible, and reporting a small "
        "one as a finding would be reading noise."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
