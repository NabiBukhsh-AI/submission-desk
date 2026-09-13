"""One admin, one password, one session cookie, and settings that never leak.

The trust boundary of the HTTP interface. What is asserted: nothing past
health and auth answers without a session; the account can be created once;
a wrong password and a wrong username are refused with the same sentence; a
forged or expired token is a stranger; a saved API key is sealed in the
database and never returned; a saved provider takes effect on the next
request.

check_secrets: planted-shapes. The key-shaped strings below are constructed
in the tests and are credentials for nothing.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import replace
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.api import main as api_module
from application.deps import Deps
from application.use_cases import admin
from infrastructure.factory import build_deps, settings_from_env
from infrastructure.secrets import LocalSecrets
from infrastructure.storage.sqlite.connection import close_thread_connection

PASSWORD = "correct horse battery"


@pytest.fixture
def http(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    settings = settings_from_env(
        db_path=str(tmp_path / "auth.sqlite"), blob_dir=str(tmp_path / "blobs")
    )
    monkeypatch.setenv("DATABASE_PATH", settings.db_path)
    monkeypatch.setenv("BLOB_DIR", settings.blob_dir)
    monkeypatch.setattr(api_module, "_deps", build_deps(settings))
    yield TestClient(api_module.app)
    close_thread_connection(settings.db_path)


def setup(http: TestClient) -> None:
    assert (
        http.post("/api/auth/setup", json={"username": "nabi", "password": PASSWORD}).status_code
        == 201
    )


# --- the guard -------------------------------------------------------------------------


@pytest.mark.parametrize(
    "path",
    ["/api/vocabulary", "/api/runs", "/api/admin/settings"],
)
def test_nothing_answers_without_a_session(http: TestClient, path: str) -> None:
    setup(http)
    http.post("/api/auth/logout")

    assert http.get(path).status_code == 401


def test_health_and_auth_status_are_open(http: TestClient) -> None:
    """A page has to be able to ask whether to show the login form."""
    assert http.get("/api/health").status_code == 200
    body = http.get("/api/auth/status").json()
    assert body == {"setup_required": True, "user": None}


# --- the account ----------------------------------------------------------------------


def test_the_first_visitor_creates_the_account_and_is_signed_in(http: TestClient) -> None:
    setup(http)

    assert http.get("/api/auth/status").json() == {"setup_required": False, "user": "nabi"}
    assert http.get("/api/runs").status_code == 200


def test_the_account_can_be_created_once(http: TestClient) -> None:
    setup(http)

    response = http.post("/api/auth/setup", json={"username": "other", "password": PASSWORD})

    assert response.status_code == 409


def test_a_short_password_is_refused(http: TestClient) -> None:
    response = http.post("/api/auth/setup", json={"username": "nabi", "password": "short"})

    assert response.status_code == 409
    assert "characters" in response.json()["detail"]


def test_login_and_logout(http: TestClient) -> None:
    setup(http)
    http.post("/api/auth/logout")
    assert http.get("/api/runs").status_code == 401

    assert (
        http.post("/api/auth/login", json={"username": "nabi", "password": PASSWORD}).status_code
        == 200
    )
    assert http.get("/api/runs").status_code == 200


@pytest.mark.parametrize(
    ("username", "password"),
    [("nabi", "wrong password here"), ("somebody", PASSWORD), ("", "")],
)
def test_a_wrong_credential_is_refused_without_saying_which(
    http: TestClient, username: str, password: str
) -> None:
    setup(http)
    http.post("/api/auth/logout")

    response = http.post("/api/auth/login", json={"username": username, "password": password})

    assert response.status_code == 401
    assert response.json()["detail"] == "That username and password do not match."


def test_a_forged_cookie_is_a_stranger(http: TestClient) -> None:
    setup(http)
    http.cookies.set(api_module.SESSION_COOKIE, "nabi:9999999999:not-a-real-signature")

    assert http.get("/api/runs").status_code == 401


def test_an_expired_token_is_a_stranger(tmp_path: Path) -> None:
    settings = settings_from_env(db_path=str(tmp_path / "t.sqlite"), blob_dir=str(tmp_path / "b"))
    deps = build_deps(settings)
    try:
        admin.create_admin(deps, "nabi", PASSWORD)
        token = admin.login(deps, "nabi", PASSWORD, now=1_000_000.0)

        assert admin.session_user(deps, token, now=1_000_100.0) == "nabi"
        assert admin.session_user(deps, token, now=1_000_000.0 + admin.SESSION_SECONDS + 1) is None
    finally:
        close_thread_connection(settings.db_path)


def test_the_password_is_stored_as_a_hash(http: TestClient) -> None:
    setup(http)
    stored = api_module.deps().settings_store.get(admin.ADMIN_PASSWORD_HASH)

    assert stored is not None
    assert PASSWORD not in stored
    assert stored.startswith("scrypt$")


# --- settings --------------------------------------------------------------------------


def test_settings_show_keys_as_set_or_not_and_never_the_value(http: TestClient) -> None:
    setup(http)
    key = "sk-ant-api03-Qm3vX9pL2rT8wY4nB7kD1hJ6fS0aZ5cE"

    saved = http.put(
        "/api/admin/settings",
        json={
            "changes": {
                "model_provider": "anthropic",
                "model_cheap_id": "claude-haiku-4-5",
                "model_strong_id": "claude-sonnet-5",
                "price_cheap_input": "1",
                "price_cheap_output": "5",
            },
            "keys": {"anthropic": key},
        },
    )

    assert saved.status_code == 200, saved.text
    body = saved.json()
    assert body["keys_set"] == {"anthropic": True}
    assert key not in saved.text
    assert body["effective_provider"] == "anthropic"
    assert body["values"]["model_cheap_id"] == "claude-haiku-4-5"


def test_a_saved_key_is_sealed_in_the_database(http: TestClient) -> None:
    setup(http)
    key = "sk-ant-api03-Qm3vX9pL2rT8wY4nB7kD1hJ6fS0aZ5cE"
    http.put("/api/admin/settings", json={"changes": {}, "keys": {"anthropic": key}})

    rows = api_module.deps().settings_store.all()
    sealed, secret = rows["anthropic_api_key"]

    assert secret is True
    assert key not in sealed
    assert api_module.deps().secrets.open(sealed) == key


def test_a_saved_provider_takes_effect_on_the_next_request(http: TestClient) -> None:
    """The container is rebuilt after a save, so the running system reads the
    new settings rather than the ones it started with."""
    setup(http)
    http.put(
        "/api/admin/settings",
        json={
            "changes": {"model_provider": "anthropic", "reviewer_id": "rec-42"},
            "keys": {"anthropic": "sk-ant-Qm3vX9pL2rT8wY4nB7kD1hJ6fS0aZ5cEgH2jK4mN"},
        },
    )

    wired = api_module.deps()
    assert wired.settings.model_provider == "anthropic"
    assert wired.settings.reviewer_id == "rec-42"
    assert wired.settings.model_api_key.startswith("sk-ant-")
    assert http.get("/api/health").json()["provider"] == "anthropic"


def test_an_empty_key_field_leaves_the_stored_key_alone(http: TestClient) -> None:
    setup(http)
    http.put(
        "/api/admin/settings",
        json={"changes": {}, "keys": {"anthropic": "sk-ant-Qm3vX9pL2rT8wY4nB7kD1hJ6fS0aZ5cE"}},
    )

    http.put(
        "/api/admin/settings", json={"changes": {"reviewer_id": "x"}, "keys": {"anthropic": ""}}
    )

    assert http.get("/api/admin/settings").json()["keys_set"]["anthropic"] is True


def test_a_key_can_be_cleared(http: TestClient) -> None:
    setup(http)
    http.put(
        "/api/admin/settings",
        json={"changes": {}, "keys": {"anthropic": "sk-ant-Qm3vX9pL2rT8wY4nB7kD1hJ6fS0aZ5cE"}},
    )

    assert http.delete("/api/admin/keys/anthropic").status_code == 204
    assert http.get("/api/admin/settings").json()["keys_set"]["anthropic"] is False


@pytest.mark.parametrize(
    ("changes", "fragment"),
    [
        ({"model_provider": "openrouter"}, "Unknown provider"),
        ({"model_strong_id": "dots-studio/dots-3-note-preview:free"}, "not a model the provider"),
        ({"price_cheap_input": "lots"}, "number"),
        ({"price_cheap_input": "-1"}, "number"),
        ({"retention_days": "0"}, "at least 1"),
        ({"db_path": "/etc/passwd"}, "not a setting"),
    ],
)
def test_a_bad_setting_is_refused_before_anything_is_written(
    http: TestClient, changes: dict[str, str], fragment: str
) -> None:
    setup(http)

    response = http.put("/api/admin/settings", json={"changes": changes, "keys": {}})

    assert response.status_code == 422
    assert fragment in response.json()["detail"]


def test_admin_pricing_reaches_the_cost_table(http: TestClient) -> None:
    """A free tier typed as zero is a price of zero, not "not configured"."""
    setup(http)
    http.put(
        "/api/admin/settings",
        json={"changes": {"price_cheap_input": "0", "price_cheap_output": "0"}, "keys": {}},
    )

    pricing = api_module.deps().pricing
    assert pricing.configured is True
    assert pricing.source.startswith("admin@")


def test_the_probe_reports_what_the_offline_client_answered(http: TestClient) -> None:
    """With the fake provider the probe goes to the stand-in, which answers
    something — so the button is testable without a key."""
    setup(http)

    body = http.post("/api/admin/probe").json()

    assert [probe["tier"] for probe in body] == ["tier_cheap", "tier_strong"]
    assert all(probe["ok"] and "stand-in" in probe["message"] for probe in body)


# --- the secrets adapter -----------------------------------------------------------------


def test_seal_and_open_round_trip() -> None:
    secrets = LocalSecrets("a secret of any shape")

    assert (
        secrets.open(secrets.seal("sk-ant-Qm3vX9pL2rT8wY4nB7kD1hJ6"))
        == "sk-ant-Qm3vX9pL2rT8wY4nB7kD1hJ6"
    )


def test_a_different_secret_cannot_open_it() -> None:
    sealed = LocalSecrets("one").seal("value")

    with pytest.raises(ValueError, match="application secret has changed"):
        LocalSecrets("two").open(sealed)


def test_passwords_hash_with_a_fresh_salt_each_time() -> None:
    secrets = LocalSecrets("s")

    first, second = secrets.hash_password(PASSWORD), secrets.hash_password(PASSWORD)

    assert first != second
    assert secrets.verify_password(PASSWORD, first) and secrets.verify_password(PASSWORD, second)
    assert not secrets.verify_password("not it at all", first)
    assert not secrets.verify_password(PASSWORD, "garbage")


def test_app_secret_is_generated_once_and_reused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from infrastructure.secrets import app_secret

    monkeypatch.delenv("APP_SECRET", raising=False)
    db = tmp_path / "db" / "x.sqlite"

    first = app_secret(db)

    assert (tmp_path / "db" / "app_secret").is_file()
    assert app_secret(db) == first
    monkeypatch.setenv("APP_SECRET", "from-the-environment")
    assert app_secret(db) == "from-the-environment"


# --- a fixed account from the environment -------------------------------------------------


def _with_admin(tmp_path: Path, username: str, password: str) -> Deps:
    settings = settings_from_env(
        db_path=str(tmp_path / "fixed.sqlite"),
        blob_dir=str(tmp_path / "b"),
        admin_username=username,
        admin_password=password,
    )
    return build_deps(settings)


def test_the_environment_can_fix_the_account(tmp_path: Path) -> None:
    """A deployment whose disk is wiped on deploy needs no setup visit."""
    deps = _with_admin(tmp_path, "admin", "a fixed password")
    try:
        assert admin.ensure_admin(deps) is None
        assert admin.setup_required(deps) is False
        assert admin.login(deps, "admin", "a fixed password")
        with pytest.raises(admin.AdminRefused):
            admin.create_admin(deps, "other", "somebody else's")
    finally:
        close_thread_connection(deps.settings.db_path)


def test_a_rotated_environment_password_wins(tmp_path: Path) -> None:
    deps = _with_admin(tmp_path, "admin", "the first password")
    try:
        admin.ensure_admin(deps)
        rotated = Deps(
            **{**deps.__dict__, "settings": replace(deps.settings, admin_password="the second one")}
        )
        assert admin.ensure_admin(rotated) is None
        assert admin.login(rotated, "admin", "the second one")
        with pytest.raises(admin.AdminRefused):
            admin.login(rotated, "admin", "the first password")
    finally:
        close_thread_connection(deps.settings.db_path)


def test_a_short_environment_password_is_refused_with_a_sentence(tmp_path: Path) -> None:
    deps = _with_admin(tmp_path, "admin", "short")
    try:
        problem = admin.ensure_admin(deps)
        assert problem is not None and "10 characters" in problem
        assert admin.setup_required(deps) is True
    finally:
        close_thread_connection(deps.settings.db_path)


def test_the_api_signs_in_the_fixed_account_without_setup(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings = settings_from_env(db_path=str(tmp_path / "f.sqlite"), blob_dir=str(tmp_path / "b"))
    monkeypatch.setenv("DATABASE_PATH", settings.db_path)
    monkeypatch.setenv("BLOB_DIR", settings.blob_dir)
    monkeypatch.setenv("ADMIN_USERNAME", "admin")
    monkeypatch.setenv("ADMIN_PASSWORD", "correct horse battery")
    monkeypatch.setattr(api_module, "_deps", None)
    http = TestClient(api_module.app)
    try:
        assert http.get("/api/auth/status").json() == {"setup_required": False, "user": None}
        login = http.post(
            "/api/auth/login", json={"username": "admin", "password": "correct horse battery"}
        )
        assert login.status_code == 200
        assert http.get("/api/runs").status_code == 200
    finally:
        close_thread_connection(settings.db_path)
