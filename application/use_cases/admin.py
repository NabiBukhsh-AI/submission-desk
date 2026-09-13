"""The admin account, sessions, and the settings the admin page writes.

One admin. The first person to reach a fresh deployment creates the account;
after that, the account exists and nobody creates another. Passwords are
hashed through the ``Secrets`` port and never stored; provider keys are sealed
through the same port and never read back to a browser — an admin sees that a
key is set, not what it is.

Sessions are signed tokens, not rows: ``user:expiry:signature``. The server
keeps nothing and a token cannot be forged without the application secret.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any

from application.deps import Deps

ADMIN_USERNAME = "admin_username"
ADMIN_PASSWORD_HASH = "admin_password_hash"

#: A session lasts a working day. A reviewer who leaves the page open overnight
#: logs in again in the morning, which is the right amount of friction.
SESSION_SECONDS = 12 * 60 * 60
MIN_PASSWORD_CHARS = 10

#: What the admin page may set. Names outside this list are refused, so a
#: request cannot write an arbitrary setting.
EDITABLE = (
    "model_provider",
    "model_cheap_id",
    "model_strong_id",
    "model_api_base_url",
    "price_cheap_input",
    "price_cheap_output",
    "price_strong_input",
    "price_strong_output",
    "reviewer_id",
    "blind_mode",
    "retention_days",
)
PROVIDERS = ("fake", "anthropic")
KEY_SETTINGS = {"anthropic": "anthropic_api_key"}
#: Every model the provider serves is named this way. A tier bound to
#: anything else fails every call with "not found", so it is refused here.
MODEL_PREFIX = "claude-"


class AdminRefused(Exception):
    """Something the admin asked for cannot be done, with the reason."""


# --- the account -----------------------------------------------------------------


def setup_required(deps: Deps) -> bool:
    return deps.settings_store.get(ADMIN_USERNAME) is None


def create_admin(deps: Deps, username: str, password: str) -> None:
    """Create the one admin account. Refused once it exists."""
    if not setup_required(deps):
        raise AdminRefused("An admin account already exists.")
    username = username.strip()
    if not username:
        raise AdminRefused("A username is needed.")
    if len(password) < MIN_PASSWORD_CHARS:
        raise AdminRefused(f"The password needs at least {MIN_PASSWORD_CHARS} characters.")
    deps.settings_store.set(ADMIN_USERNAME, username)
    deps.settings_store.set(ADMIN_PASSWORD_HASH, deps.secrets.hash_password(password))


def ensure_admin(deps: Deps) -> str | None:
    """Make the environment's admin account the account, if one is configured.

    ``ADMIN_USERNAME`` and ``ADMIN_PASSWORD`` are authoritative when both are
    set: a missing account is created, and a stored one that differs is
    brought into line, so a redeploy that wiped the database or a rotated
    password both end in the same place. Returns a sentence when the
    configuration cannot be honoured, for the log; never raises, because a
    bad password in the environment should not take the API down.
    """
    username = deps.settings.admin_username.strip()
    password = deps.settings.admin_password
    if not username or not password:
        return None
    if len(password) < MIN_PASSWORD_CHARS:
        return (
            f"ADMIN_PASSWORD is shorter than {MIN_PASSWORD_CHARS} characters; the account was "
            "not created from the environment."
        )
    stored_name = deps.settings_store.get(ADMIN_USERNAME)
    stored_hash = deps.settings_store.get(ADMIN_PASSWORD_HASH) or ""
    if stored_name == username and deps.secrets.verify_password(password, stored_hash):
        return None
    deps.settings_store.set(ADMIN_USERNAME, username)
    deps.settings_store.set(ADMIN_PASSWORD_HASH, deps.secrets.hash_password(password))
    return None


def login(deps: Deps, username: str, password: str, *, now: float | None = None) -> str:
    """A session token, or a refusal that does not say which half was wrong."""
    stored_name = deps.settings_store.get(ADMIN_USERNAME)
    stored_hash = deps.settings_store.get(ADMIN_PASSWORD_HASH) or ""
    # Always run the hash, so a wrong username costs the same time as a wrong
    # password and the difference cannot be measured.
    matches = deps.secrets.verify_password(password, stored_hash)
    if stored_name is None or username.strip() != stored_name or not matches:
        raise AdminRefused("That username and password do not match.")
    expiry = int((now or time.time()) + SESSION_SECONDS)
    message = f"{stored_name}:{expiry}"
    return f"{message}:{deps.secrets.sign(message)}"


def session_user(deps: Deps, token: str | None, *, now: float | None = None) -> str | None:
    """Who a token belongs to, or None if it is missing, expired, or forged."""
    if not token:
        return None
    try:
        username, expiry_text, signature = token.rsplit(":", 2)
        expiry = int(expiry_text)
    except ValueError:
        return None
    if expiry < (now or time.time()):
        return None
    if not deps.secrets.verify_signature(f"{username}:{expiry}", signature):
        return None
    if username != deps.settings_store.get(ADMIN_USERNAME):
        return None
    return username


# --- the settings ---------------------------------------------------------------


@dataclass(frozen=True)
class SettingsView:
    """What the admin page shows. Keys appear as set or not, never as values."""

    values: dict[str, str]
    keys_set: dict[str, bool]
    demo_mode: bool
    effective_provider: str


def describe_settings(deps: Deps) -> SettingsView:
    rows = deps.settings_store.all()
    values = {name: value for name, (value, secret) in rows.items() if name in EDITABLE}
    # The environment's values fill the gaps so the page shows what is in
    # force, not only what was typed here.
    settings = deps.settings
    defaults = {
        "model_provider": settings.model_provider,
        "model_cheap_id": settings.model_cheap_id,
        "model_strong_id": settings.model_strong_id,
        "model_api_base_url": settings.model_api_base_url,
        "price_cheap_input": settings.price_cheap_input,
        "price_cheap_output": settings.price_cheap_output,
        "price_strong_input": settings.price_strong_input,
        "price_strong_output": settings.price_strong_output,
        "reviewer_id": settings.reviewer_id,
        "blind_mode": "true" if settings.blind_mode else "false",
        "retention_days": str(settings.retention_days),
    }
    return SettingsView(
        values={**defaults, **values},
        keys_set={
            provider: bool(rows.get(name, ("", False))[0])
            for provider, name in KEY_SETTINGS.items()
        },
        demo_mode=settings.demo_mode,
        effective_provider=settings.model_provider,
    )


PRICE_SETTINGS = (
    "price_cheap_input",
    "price_cheap_output",
    "price_strong_input",
    "price_strong_output",
)


def _check(changes: dict[str, str], keys: dict[str, str]) -> None:
    """Every refusal, before anything is written."""
    unknown = [name for name in changes if name not in EDITABLE]
    if unknown:
        raise AdminRefused(f"{unknown[0]!r} is not a setting the admin page can change.")

    provider = changes.get("model_provider")
    if provider is not None and provider not in PROVIDERS:
        raise AdminRefused(f"Unknown provider {provider!r}. Choose one of: {', '.join(PROVIDERS)}.")

    for name in ("model_cheap_id", "model_strong_id"):
        model = changes.get(name, "").strip()
        if model and not model.startswith(MODEL_PREFIX):
            raise AdminRefused(
                f"{name}: {model!r} is not a model the provider serves. "
                f"Model identifiers start with {MODEL_PREFIX!r}."
            )

    for name in PRICE_SETTINGS:
        value = changes.get(name, "").strip()
        if value and _price(value) is None:
            raise AdminRefused(f"{name} must be a number per million tokens, zero or more.")

    retention = changes.get("retention_days", "").strip()
    if retention and (not retention.isdigit() or int(retention) < 1):
        raise AdminRefused("retention_days must be a whole number of days, at least 1.")

    unknown_providers = [name for name in keys if name not in KEY_SETTINGS]
    if unknown_providers:
        raise AdminRefused(f"No provider called {unknown_providers[0]!r}.")


def _price(text: str) -> Decimal | None:
    try:
        value = Decimal(text)
    except InvalidOperation:
        return None
    return value if value >= 0 else None


def save_settings(deps: Deps, changes: dict[str, str], keys: dict[str, str]) -> None:
    """Write what the page sent, after every value has been checked.

    ``keys`` maps a provider to a new API key; an empty value leaves the
    stored key alone, so a form that did not touch the key field cannot erase
    it.
    """
    _check(changes, keys)
    for name, value in changes.items():
        deps.settings_store.set(name, value.strip())
    for provider_name, key in keys.items():
        if key.strip():
            deps.settings_store.set(
                KEY_SETTINGS[provider_name], deps.secrets.seal(key.strip()), secret=True
            )


def clear_key(deps: Deps, provider: str) -> None:
    if provider not in KEY_SETTINGS:
        raise AdminRefused(f"No provider called {provider!r}.")
    deps.settings_store.delete(KEY_SETTINGS[provider])


# --- the connection test ------------------------------------------------------------


@dataclass(frozen=True)
class ProbeResult:
    ok: bool
    message: str
    input_tokens: int = 0
    output_tokens: int = 0
    tier: str = ""


def probe_provider(deps: Deps) -> list[ProbeResult]:
    """One tiny call per tier through the configured client, and what each cost.

    Every tier, because each is bound to its own model and a typo in the
    strong one only surfaces on the first escalation otherwise.
    """
    from domain.contracts.enums import ModelTier  # noqa: PLC0415 - a value, not a decision

    return [_probe_tier(deps, tier) for tier in (ModelTier.CHEAP, ModelTier.STRONG)]


def _probe_tier(deps: Deps, tier: Any) -> ProbeResult:
    """Not a health check the doctor runs: this spends money on purpose,
    once, when an admin presses the button, so that "the key works" is a
    measured fact rather than the absence of a typo."""
    from pydantic import BaseModel  # noqa: PLC0415

    from domain.ports.models import (  # noqa: PLC0415
        BlockKind,
        GenerationRequest,
        ModelUnavailable,
        PromptBlock,
    )

    class Probe(BaseModel):
        ok: bool

    if deps.models is None:
        return ProbeResult(False, "No model client is configured.", tier=tier.value)
    if deps.settings.model_provider in ("", "fake"):
        return ProbeResult(
            True,
            "The offline stand-in is in use: it answers every call without a provider, so "
            "there is no connection to test. Choose Anthropic and save a key to test one.",
            tier=tier.value,
        )

    request = GenerationRequest(
        call_site="assess.criterion",
        tier=tier,
        system_prompt='Reply with the JSON object {"ok": true} and nothing else.',
        user_blocks=(PromptBlock(kind=BlockKind.DOCUMENT, content="ping"),),
        response_schema=Probe,
        max_output_tokens=64,
        timeout_s=30.0,
        nonce="probe",
    )
    try:
        result: Any = deps.models.structured_generate(request)
    except ModelUnavailable as refused:
        return ProbeResult(False, str(refused), tier=tier.value)
    except TimeoutError:
        return ProbeResult(
            False, "The provider did not respond within thirty seconds.", tier=tier.value
        )

    if not result.ok:
        return ProbeResult(
            False,
            "The provider answered, but not with the JSON asked for: "
            f"{(result.validation_error or 'unknown')[:160]}",
            result.usage.input_tokens,
            result.usage.output_tokens,
            request.tier.value,
        )
    return ProbeResult(
        True,
        "The provider answered correctly.",
        result.usage.input_tokens,
        result.usage.output_tokens,
        request.tier.value,
    )
