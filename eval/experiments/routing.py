"""Three routing policies over the same cases.

The experiment the architecture argues for and does not assume the answer to.
``all_cheap`` and ``all_strong`` are the controls: they bound what quality and
what cost are achievable, and ``routed`` has to earn its place between them.

If routing turns out to cost as much as all-strong and score like all-cheap,
that is the result, and it belongs in the report rather than in a drawer.
Switching the policy is one environment variable, which is what makes running
this cheap enough to actually do.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from eval.runner import RESULTS_DIR, load_cases, run_suite, write_outputs
from infrastructure.factory import build_deps, settings_from_env

#: The three arms of this experiment. Two controls and the thing being tested.
POLICIES = ("all_cheap", "routed", "all_strong")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--split", default="dev")
    parser.add_argument("--out", type=Path, default=RESULTS_DIR / "routing")
    args = parser.parse_args(argv)

    cases = load_cases(args.split)
    print(f"{len(cases)} case(s), three policies.\n")

    for policy in POLICIES:
        settings = settings_from_env(routing_policy_id=policy)
        deps = build_deps(settings)

        result = run_suite(deps, split=f"{args.split}-{policy}", cases=cases)
        write_outputs(result, args.out)

        summary = result.summaries["c"]
        print(f"{policy}:")
        print(f"  band accuracy      {summary['band_accuracy']['rendered']}")
        print(f"  abstention         {summary['abstention_accuracy']['rendered']}")
        print(f"  hallucination      {summary['hallucination_rate']['rendered']}")
        print(f"  escalations        {summary['escalation_rate']['rendered']}")

        cost = summary["cost"]
        if cost.get("mean") is None:
            print("  cost               not measured (pricing not configured)")
        else:
            print(f"  cost per candidate {cost['mean']:.4f} USD (n={cost['n']})")
        print()

    print(
        "Read the two controls first. If routed does not sit between them on "
        "both cost and quality, it is not earning its complexity."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
