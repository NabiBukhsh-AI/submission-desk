"""``submission-desk doctor``: print every check and exit non-zero on a failure.

Rendering only. What is checked and how is decided in
``infrastructure/doctor.py``, where the adapters it reads are allowed to be
named. This file turns a list of checks into lines on a console that may not
be able to print anything beyond ASCII.
"""

from __future__ import annotations

import argparse
import contextlib
import sys

from infrastructure.doctor import Level, run_all


def make_output_safe() -> None:
    """Never fail on a console that cannot print a character.

    A diagnostic that raises UnicodeEncodeError while reporting a problem has
    added a second problem, and the person running it now has to debug the
    debugger.
    """
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if callable(reconfigure):
            with contextlib.suppress(ValueError, OSError):
                reconfigure(errors="replace")


def add_arguments(parser: argparse.ArgumentParser) -> None:
    """No options. The doctor reads configuration and never the network."""


def run(_args: argparse.Namespace) -> int:
    make_output_safe()

    checks = run_all()

    print("Submission Desk - deployment check\n")
    for check in checks:
        print(check.render())

    failures = [check for check in checks if check.level is Level.FAIL]
    warnings = [check for check in checks if check.level is Level.WARN]

    print(
        f"\n{len(checks) - len(failures) - len(warnings)} passed, "
        f"{len(warnings)} warning(s), {len(failures)} failure(s)."
    )

    if failures:
        print("\nThe failures above will stop this deployment working.")
        return 1

    if warnings:
        print("\nThe warnings are things to know about, not things to fix now.")

    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    add_arguments(parser)
    return run(parser.parse_args(argv))


if __name__ == "__main__":
    sys.exit(main())
