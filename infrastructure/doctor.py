"""Is this deployment going to work, and if not, what should somebody do.

The checks behind ``submission-desk doctor``. They live here rather than in the
command because each one reads a concrete adapter — the migration table, the
prompt registry, the OCR binary — and the interface layer is not allowed to
know those exist. The command renders; this decides.

Every check reports pass, warn, or fail with a suggested action, because a
diagnostic that says "FAIL: database" tells somebody they have a problem and
nothing else.

Two rules.

A credential is checked for presence and never printed. Not the first four
characters, not the length, not a masked form: a diagnostic that echoes part of
a key is a diagnostic somebody pastes into a ticket.

Nothing here reaches the network. A doctor that hung for thirty seconds
against an unreachable provider would be run once and never again, so the
provider check reads configuration and the wiring, and the first real call is
what contacts anything.
"""

from __future__ import annotations

import os
import shutil
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any

from infrastructure.extraction.ocr import find_binary
from infrastructure.factory import DemoModeViolation, build_deps, settings_from_env
from infrastructure.storage.sqlite.schema import current_version, discover

#: Free space below which blob storage is a problem waiting to happen.
MIN_FREE_MB = 500

#: How many decorators may sit between Deps.models and the client that makes
#: the call. Repair and cache make two; a third would be a design change.
MAX_STACK_DEPTH = 4

#: Credentials, by the environment variable that carries them. Presence only:
#: the value is never read, logged, or echoed. The model key is checked
#: separately because the admin page can set it too.
CREDENTIALS = {
    "ADMIN_PASSWORD": "the fixed admin account",
    "GOOGLE_APPLICATION_CREDENTIALS": "Google Drive and Sheets",
    "SLACK_BOT_TOKEN": "Slack",
}

#: A credential that may arrive under another name. Presence of either counts.
ALTERNATIVES = {"GOOGLE_APPLICATION_CREDENTIALS": "GOOGLE_APPLICATION_CREDENTIALS_JSON"}


class Level(str, Enum):
    PASS = "pass"
    WARN = "warn"
    FAIL = "fail"

    @property
    def marker(self) -> str:
        return {"pass": "[ ok ]", "warn": "[warn]", "fail": "[fail]"}[self.value]


@dataclass(frozen=True)
class Check:
    """One thing that was looked at, and what to do about it."""

    name: str
    level: Level
    detail: str
    action: str = ""

    def render(self) -> str:
        line = f"{self.level.marker} {self.name}: {self.detail}"
        # ASCII on purpose. This runs on whatever console somebody has, and a
        # diagnostic that cannot be printed is worse than no diagnostic.
        return f"{line}\n         -> {self.action}" if self.action else line


def check_database(settings: Any) -> Check:
    path = Path(settings.db_path)

    if not path.exists():
        return Check(
            "database",
            Level.WARN,
            f"no database at {path}",
            "Run `make setup`, which creates it and applies every migration.",
        )

    return Check("database", Level.PASS, f"present at {path}")


def check_migrations(settings: Any) -> Check:
    """Every migration on disk applied, and none applied that is not on disk.

    The second half matters: a database ahead of the code means somebody
    checked out an older revision, and the failure that follows is confusing.
    """
    path = Path(settings.db_path)
    if not path.exists():
        return Check("migrations", Level.WARN, "no database to check", "Run `make setup`.")

    try:
        on_disk = [version for version, _, _ in discover()]
        applied = current_version(path)
    except Exception as error:
        return Check("migrations", Level.FAIL, str(error)[:120], "Check the migrations directory.")

    if applied < max(on_disk):
        return Check(
            "migrations",
            Level.FAIL,
            f"database is at {applied}, code expects {max(on_disk)}",
            "Run `make setup` to apply the outstanding migrations.",
        )

    if applied > max(on_disk):
        return Check(
            "migrations",
            Level.FAIL,
            f"database is at {applied}, ahead of the code at {max(on_disk)}",
            "This database was written by a newer revision. Check out that "
            "revision, or start from a fresh database.",
        )

    return Check("migrations", Level.PASS, f"at version {applied}")


def check_prompts(deps: Any) -> Check:
    registry = getattr(deps, "prompts", None)
    if registry is None:
        return Check("prompts", Level.FAIL, "no prompt registry", "Check prompts/.")

    count = len(getattr(registry, "prompts", {}) or {})
    if not count:
        return Check(
            "prompts",
            Level.FAIL,
            "the prompt registry is empty",
            "Prompts live in prompts/. Check the directory exists and has .md files.",
        )

    return Check("prompts", Level.PASS, f"{count} prompt(s) loaded")


def check_rubrics(deps: Any) -> Check:
    loader = getattr(deps, "rubric_loader", None)
    available = getattr(loader, "available", None)
    roles = list(available()) if callable(available) else []

    if loader is None or not roles:
        return Check(
            "rubrics",
            Level.FAIL,
            "no role definitions found",
            "Add a YAML file to rubrics/. Nothing can be processed without one.",
        )

    broken = []
    for role in roles:
        try:
            loader(role)
        except Exception as error:
            broken.append(f"{role}: {str(error)[:60]}")

    if broken:
        return Check(
            "rubrics",
            Level.FAIL,
            "; ".join(broken),
            "Run `make rubric-lint` for the full list of problems.",
        )

    return Check("rubrics", Level.PASS, f"{len(roles)} role(s): {', '.join(roles)}")


def _innermost_transport(client: Any) -> Any:
    """Walk the decorator stack to the client that would perform the call."""
    for _ in range(MAX_STACK_DEPTH):
        if client is None:
            return None
        if hasattr(client, "transport"):
            return client.transport
        client = getattr(client, "inner", None)
    return None


def check_provider(settings: Any, deps: Any = None) -> Check:
    provider = settings.model_provider

    if provider in ("", "fake"):
        return Check(
            "model provider",
            Level.WARN,
            "the offline fake is configured",
            "Fine for the demo and the test suite. Choose a provider on the admin page "
            "(or set MODEL_PROVIDER) to assess real candidates.",
        )

    bound = [
        tier
        for tier, model_id in (
            ("tier_cheap", settings.model_cheap_id),
            ("tier_strong", settings.model_strong_id),
        )
        if model_id.strip()
    ]
    transport = _innermost_transport(getattr(deps, "models", None))

    if transport is None:
        return Check(
            "model provider",
            Level.FAIL,
            f"{provider} configured, but no API key is set for it",
            "Save the provider's key on the admin page, or set MODEL_API_KEY. Without it "
            "the offline stand-in answers every call.",
        )

    return Check(
        "model provider",
        Level.PASS,
        f"{provider} configured, {len(bound)} tier(s) bound. "
        "Not contacted: the connection test on the admin page is the probe.",
    )


def check_pricing(deps: Any) -> Check:
    """Configured or not. Never a guess either way.

    A warning rather than a failure: the system works unpriced, and the only
    thing missing is the conversion from real token counts to money.
    """
    pricing = getattr(deps, "pricing", None)

    if pricing is None or not getattr(pricing, "configured", False):
        return Check(
            "pricing",
            Level.WARN,
            "not configured, so every cost figure reads 'not configured'",
            "Add rates to config/pricing.yaml to see money. Token counts come from the "
            "provider's usage metadata once one is configured.",
        )

    return Check("pricing", Level.PASS, f"configured from {pricing.source}")


def check_credentials(settings: Any = None) -> list[Check]:
    """Presence, never the value.

    A diagnostic that echoes four characters of a key is a diagnostic somebody
    pastes into a ticket, and four characters is enough to confirm a guess.
    """
    key_set = bool(getattr(settings, "model_api_key", "").strip())
    checks = [
        Check(
            "credential: the model provider",
            Level.PASS if key_set else Level.WARN,
            "set" if key_set else "not set",
            "" if key_set else "Optional. Save a key on the admin page, or set MODEL_API_KEY.",
        )
    ]

    for variable, what in CREDENTIALS.items():
        names = (variable, *filter(None, (ALTERNATIVES.get(variable),)))
        present = any(os.environ.get(name, "").strip() for name in names)
        checks.append(
            Check(
                f"credential: {what}",
                Level.PASS if present else Level.WARN,
                "set" if present else "not set",
                "" if present else f"Optional. Set {' or '.join(names)} to enable {what}.",
            )
        )

    return checks


def check_disk(settings: Any) -> Check:
    blob_dir = Path(settings.blob_dir)
    target = blob_dir if blob_dir.exists() else blob_dir.parent
    while not target.exists() and target != target.parent:
        target = target.parent

    free_mb = shutil.disk_usage(target).free / (1024 * 1024)

    if free_mb < MIN_FREE_MB:
        return Check(
            "disk space",
            Level.FAIL,
            f"{free_mb:.0f} MB free where documents are stored",
            f"Free some space. Documents are stored by content hash under "
            f"{blob_dir}, and a full disk fails intake rather than corrupting "
            "anything.",
        )

    return Check("disk space", Level.PASS, f"{free_mb:.0f} MB free")


def check_reviewer(settings: Any) -> Check:
    """A decision has to be attributable.

    Warn rather than fail: the system runs and produces recommendations without
    one. What it cannot do is record who approved anything.
    """
    if not settings.reviewer_id:
        return Check(
            "reviewer",
            Level.WARN,
            "REVIEWER_ID is not set, so no decision can be attributed",
            "Set REVIEWER_ID before anybody reviews a candidate.",
        )

    return Check("reviewer", Level.PASS, f"decisions attributed to {settings.reviewer_id}")


def check_ocr() -> Check:
    binary = find_binary()
    if binary is None:
        return Check(
            "OCR",
            Level.WARN,
            "Tesseract was not found",
            "Scanned documents will be rejected rather than read. Install "
            "Tesseract, or set TESSERACT_BINARY.",
        )

    return Check("OCR", Level.PASS, f"Tesseract at {binary}")


def check_demo_mode(settings: Any) -> Check:
    if not settings.demo_mode:
        return Check("demo mode", Level.PASS, "off")

    return Check(
        "demo mode",
        Level.WARN,
        "on: synthetic documents only, and every sink and notifier disabled",
        "Nothing will be sent anywhere. Unset DEMO_MODE to process real candidates.",
    )


def check_source(deps: Any) -> Check:
    """Where documents come from, and whether that place exists."""
    source = getattr(deps, "source", None)
    root = getattr(source, "root", None)

    if root is None:
        # A remote source. Not contacted: the first real listing is the probe.
        name = getattr(source, "source_id", "configured")
        folder = getattr(source, "folder_id", "")
        if name == "drive" and not folder:
            return Check(
                "document source",
                Level.FAIL,
                "SOURCE_ADAPTER is drive but DRIVE_FOLDER_ID is not set",
                "Set DRIVE_FOLDER_ID to the folder shared with the service account.",
            )
        return Check(
            "document source",
            Level.PASS,
            f"{name}, folder {folder[:8]}… — not contacted; the first listing is the probe",
        )

    path = Path(root)
    if not path.exists():
        return Check(
            "document source",
            Level.WARN,
            f"{path} does not exist yet",
            "Uploading through the interface creates it. Run `make seed` for "
            "the synthetic candidates.",
        )

    return Check("document source", Level.PASS, f"folder at {path}")


def check_destinations(deps: Any) -> Check:
    """Where results and notifications go. Named, not contacted."""
    settings = deps.settings
    if settings.demo_mode:
        return Check("destinations", Level.PASS, "none: demo mode")

    sinks = ", ".join(getattr(sink, "sink_id", "?") for sink in deps.sinks or ())
    notifier = getattr(deps.notifier, "notifier_id", "none")
    summary = f"results to {sinks or 'nowhere'}; notifications to {notifier}"

    if notifier == "slack" and not settings.public_url:
        return Check(
            "destinations",
            Level.WARN,
            f"{summary}; messages will carry no link",
            "Set PUBLIC_URL to where the interface is reachable.",
        )
    return Check("destinations", Level.PASS, summary)


def run_all() -> list[Check]:
    """Every check, in the order somebody needs them."""
    settings = settings_from_env()

    checks = [check_database(settings), check_migrations(settings)]

    try:
        deps = build_deps(settings)
    except DemoModeViolation as refused:
        checks.append(Check("demo mode", Level.FAIL, str(refused), "Unset the path, or DEMO_MODE."))
        return checks
    except Exception as error:
        checks.append(
            Check(
                "startup",
                Level.FAIL,
                str(error)[:150],
                "The system could not be assembled. Everything below is skipped.",
            )
        )
        return checks

    checks += [
        check_prompts(deps),
        check_rubrics(deps),
        # deps.settings is what is in force: the environment with the admin
        # page's saved values laid over it.
        check_provider(deps.settings, deps),
        check_pricing(deps),
        check_ocr(),
        check_source(deps),
        check_destinations(deps),
        check_disk(settings),
        check_reviewer(deps.settings),
        check_demo_mode(settings),
        *check_credentials(deps.settings),
    ]

    return checks
