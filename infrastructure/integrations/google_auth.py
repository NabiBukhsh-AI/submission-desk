"""An access token for a Google service account, from its JSON key file.

The whole OAuth flow a service account needs is one signed JWT exchanged for a
bearer token: sign ``{iss, scope, aud, iat, exp}`` with the account's private
key (RS256), post it to the token endpoint, keep the token until it expires.
Standard library HTTP and the ``cryptography`` package this project already
carries for sealing keys; no Google client library to version.

The key is read at the point of use, never at import, and its contents never
appear in a log or an error message. It comes from a file, or — for a host
that offers environment variables but no reliable file mount — from the JSON
itself in ``GOOGLE_APPLICATION_CREDENTIALS_JSON``.
"""

from __future__ import annotations

import base64
import json
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa

TOKEN_URL = "https://oauth2.googleapis.com/token"
#: Google issues tokens for an hour; refresh with a minute in hand.
LIFETIME_SECONDS = 3600
REFRESH_MARGIN_SECONDS = 60


class GoogleAuthError(Exception):
    """The credential could not be turned into a token. Carries an HTTP status
    when the token endpoint answered, so the adapters classify it like any
    other refusal."""

    def __init__(self, message: str, status: int = 401) -> None:
        super().__init__(message)
        self.status = status


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def signed_assertion(key: dict[str, Any], scope: str, *, now: float | None = None) -> str:
    """The JWT the token endpoint accepts, signed with the account's key."""
    issued = int(now if now is not None else time.time())
    header = {"alg": "RS256", "typ": "JWT"}
    claims = {
        "iss": key["client_email"],
        "scope": scope,
        "aud": key.get("token_uri", TOKEN_URL),
        "iat": issued,
        "exp": issued + LIFETIME_SECONDS,
    }
    signing_input = f"{_b64(json.dumps(header).encode())}.{_b64(json.dumps(claims).encode())}"
    private_key = serialization.load_pem_private_key(key["private_key"].encode(), password=None)
    if not isinstance(private_key, rsa.RSAPrivateKey):
        raise GoogleAuthError("The service-account key is not an RSA key.")
    signature = private_key.sign(signing_input.encode(), padding.PKCS1v15(), hashes.SHA256())
    return f"{signing_input}.{_b64(signature)}"


class ServiceAccount:
    """A bearer token for one scope, refreshed when it is about to expire."""

    def __init__(
        self,
        key_path: str | Path,
        scope: str,
        *,
        key_json: str = "",
        urlopen: Any = None,
    ) -> None:
        self.key_path = Path(key_path) if key_path else None
        self.key_json = key_json
        self.scope = scope
        self._urlopen = urlopen or urllib.request.urlopen
        self._token = ""
        self._expires_at = 0.0

    @property
    def email(self) -> str:
        """The address a folder or sheet has to be shared with."""
        return str(self._key().get("client_email", ""))

    def _key(self) -> dict[str, Any]:
        if self.key_json:
            try:
                data = json.loads(self.key_json)
            except ValueError as broken:
                raise GoogleAuthError(
                    "GOOGLE_APPLICATION_CREDENTIALS_JSON is not valid JSON. Paste the "
                    "whole key file, unchanged."
                ) from broken
        elif self.key_path is None:
            raise GoogleAuthError(
                "No service-account key is configured. Set GOOGLE_APPLICATION_CREDENTIALS "
                "to the key file, or GOOGLE_APPLICATION_CREDENTIALS_JSON to its contents."
            )
        else:
            try:
                data = json.loads(self.key_path.read_text(encoding="utf-8"))
            except FileNotFoundError as missing:
                raise GoogleAuthError(
                    "The service-account key file named by GOOGLE_APPLICATION_CREDENTIALS "
                    "does not exist."
                ) from missing
            except (OSError, ValueError) as broken:
                raise GoogleAuthError(
                    "The service-account key file could not be read as JSON."
                ) from broken
        if not isinstance(data, dict) or "private_key" not in data or "client_email" not in data:
            raise GoogleAuthError("The service-account key file is not a Google key file.")
        return data

    def token(self, *, now: float | None = None) -> str:
        current = now if now is not None else time.time()
        if self._token and current < self._expires_at - REFRESH_MARGIN_SECONDS:
            return self._token

        key = self._key()
        body = urllib.parse.urlencode(
            {
                "grant_type": "urn:ietf:params:oauth:grant-type:jwt-bearer",
                "assertion": signed_assertion(key, self.scope, now=current),
            }
        ).encode()
        request = urllib.request.Request(
            str(key.get("token_uri", TOKEN_URL)),
            data=body,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            method="POST",
        )
        try:
            with self._urlopen(request, timeout=30) as response:
                answer = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as error:
            detail = _detail(error)
            raise GoogleAuthError(
                f"Google refused the service-account credential ({error.code}): {detail}",
                status=error.code,
            ) from error
        except urllib.error.URLError as error:
            raise GoogleAuthError(
                "Google's token service could not be reached.", status=503
            ) from error

        self._token = str(answer.get("access_token", ""))
        self._expires_at = current + float(answer.get("expires_in", LIFETIME_SECONDS))
        if not self._token:
            raise GoogleAuthError("Google's token service answered without a token.")
        return self._token


def _detail(error: urllib.error.HTTPError) -> str:
    try:
        body = json.loads(error.read().decode("utf-8"))
        return str(body.get("error_description") or body.get("error") or "")[:200]
    except Exception:
        return str(error.reason)
