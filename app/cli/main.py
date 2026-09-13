"""``submission-desk``: the command line.

Every subcommand is a thin call into a use case. Nothing here computes, moves a
run, or decides anything; the interface layer's job is to parse arguments,
call one function, and print the sentence it returns.

    submission-desk doctor              is this deployment going to work
    submission-desk process             assess every candidate in the source folder
    submission-desk deliver             send every approved package
    submission-desk retry-deliveries    try again where a destination failed
    submission-desk reconcile           mark stalled runs so they can resume
    submission-desk purge               remove runs past the retention period
    submission-desk api                 serve the HTTP interface for the React frontend
"""

from __future__ import annotations

import argparse
import os
import sys
from collections.abc import Callable

import uvicorn

from app.cli import doctor
from application.deps import Deps
from application.recovery import reconcile
from application.use_cases.deliver_approved import deliver_approved
from application.use_cases.process_batch import process_batch
from application.use_cases.purge import DEFAULT_RETENTION_DAYS, purge
from application.use_cases.retry_deliveries import retry_deliveries
from domain.ports.sources import SourceUnavailable
from infrastructure.factory import build_deps, settings_from_env


def _deps() -> Deps:
    return build_deps(settings_from_env())


# --- subcommands ----------------------------------------------------------------


def cmd_process(args: argparse.Namespace) -> int:
    deps = _deps()
    source = deps.source
    if source is None:
        print("No document source is configured.", file=sys.stderr)
        return 1

    try:
        candidates = source.list_candidates(limit=args.limit)
    except SourceUnavailable as unavailable:
        print(str(unavailable), file=sys.stderr)
        return 1

    if not candidates:
        print(f"No candidates found in {getattr(source, 'root', 'the source')}.")
        return 0

    def tick(done: int, total: int, candidate_id: str) -> None:
        if candidate_id:
            print(f"  {done + 1}/{total}  {candidate_id}")

    summary = process_batch(candidates, args.role, deps, force=args.force, on_progress=tick)
    print(summary.sentence())
    for candidate_id, reason in summary.failures:
        print(f"  {candidate_id}: {reason}")

    return 0 if summary.reviewable or summary.quarantined or summary.reused else 1


def cmd_deliver(args: argparse.Namespace) -> int:
    summary = deliver_approved(_deps(), limit=args.limit)
    print(summary.sentence())
    for run_id, reason in summary.skipped:
        print(f"  {run_id}: {reason}")
    return 0


def cmd_retry_deliveries(args: argparse.Namespace) -> int:
    summary = retry_deliveries(_deps(), limit=args.limit)
    print(summary.sentence())
    for run_id, reason in summary.refused:
        print(f"  {run_id}: {reason}")
    for run_id, sinks in summary.still_failing:
        print(f"  {run_id}: still failing at {sinks}")
    return 0


def cmd_reconcile(_args: argparse.Namespace) -> int:
    result = reconcile(_deps())
    print(f"{result.count} run(s) marked interrupted and ready to resume.")
    return 0


def cmd_api(args: argparse.Namespace) -> int:
    """Serve the HTTP interface. A subcommand so `--demo` applies to it too."""
    if args.logs:
        # One line per node and per model call, as they happen. The request
        # log is off because the queue polls and would drown them.
        os.environ["LOG_CONSOLE"] = "1"
    uvicorn.run(
        "app.api.main:app",
        host=args.host,
        port=args.port,
        access_log=not args.logs,
        # Behind Render's (or any) TLS-terminating proxy, the forwarded
        # scheme is what makes the session cookie secure.
        proxy_headers=True,
        forwarded_allow_ips="*",
    )
    return 0


def cmd_purge(args: argparse.Namespace) -> int:
    """Preview by default. The confirmation is the same in demo mode as in a
    pilot on purpose: a habit formed on synthetic data is carried into the
    real thing."""
    deps = _deps()
    days = args.days if args.days is not None else deps.settings.retention_days
    summary = purge(deps, retention_days=days, dry_run=args.dry_run)
    print(summary.sentence())
    if args.dry_run and summary.runs:
        print("Nothing was removed. Pass --yes to remove it.")
    return 0


COMMANDS: dict[str, tuple[Callable[[argparse.Namespace], int], str]] = {
    "doctor": (doctor.run, "check this deployment and say what to fix"),
    "process": (cmd_process, "assess every candidate in the source folder"),
    "deliver": (cmd_deliver, "send every approved package"),
    "retry-deliveries": (cmd_retry_deliveries, "try again where a destination failed"),
    "reconcile": (cmd_reconcile, "mark stalled runs so they can resume"),
    "purge": (cmd_purge, "remove runs past the retention period"),
    "api": (cmd_api, "serve the HTTP interface for the React frontend"),
}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="submission-desk",
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    # A flag rather than an environment variable, so the Makefile runs the
    # same way from every shell: `VAR=value command` works in bash and not in
    # cmd.exe, and a target that works only from Git Bash is a target that
    # fails on the first Windows machine.
    parser.add_argument(
        "--demo",
        action="store_true",
        help="demo mode: synthetic documents only, nothing sent anywhere",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    for name, (_, help_text) in COMMANDS.items():
        sub = subparsers.add_parser(name, help=help_text)
        if name == "doctor":
            doctor.add_arguments(sub)
        elif name == "process":
            sub.add_argument("--role", default="ai-engineer", help="rubric to assess against")
            sub.add_argument("--limit", type=int, default=None, help="at most this many")
            sub.add_argument("--force", action="store_true", help="re-run finished candidates")
        elif name in ("deliver", "retry-deliveries"):
            sub.add_argument("--limit", type=int, default=50)
        elif name == "api":
            sub.add_argument("--host", default="127.0.0.1")
            sub.add_argument("--port", type=int, default=8000)
            sub.add_argument(
                "--logs", action="store_true", help="print one line per node and model call"
            )
        elif name == "purge":
            sub.add_argument(
                "--days",
                type=int,
                default=None,
                help=f"retention period; default RETENTION_DAYS or {DEFAULT_RETENTION_DAYS}",
            )
            group = sub.add_mutually_exclusive_group()
            group.add_argument(
                "--dry-run", action="store_true", default=True, help="preview (default)"
            )
            group.add_argument("--yes", dest="dry_run", action="store_false", help="remove")

    return parser


def main(argv: list[str] | None = None) -> int:
    doctor.make_output_safe()
    args = build_parser().parse_args(argv)
    if args.demo:
        # Set before anything reads configuration. The factory reads it once,
        # at startup, and the demo-mode refusals run there.
        os.environ["DEMO_MODE"] = "true"
    handler, _ = COMMANDS[args.command]
    return handler(args)


if __name__ == "__main__":
    sys.exit(main())
