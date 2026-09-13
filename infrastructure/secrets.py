"""Sealing secrets at rest, hashing the admin password, signing sessions.

One application secret drives all three. It comes from ``APP_SECRET`` in the
environment, or from a file beside the database that is created on first use
with a random value and owner-only permissions. The environment variable is
the deployment's choice; the file is what lets ``make api`` work on a fresh
clone without a setup step, and it is never committed (``data/`` is ignored
except for samples).

What each primitive is for, and why these particular ones:

- Provider keys are sealed with Fernet (AES-128-CBC with an HMAC), keyed from
  the application secret through scrypt. A copy of the database without the
  secret yields nothing.
- The password is stored as a salted scrypt hash. scrypt is in the standard
  library, is deliberately slow, and its parameters are the ones the library
  documents for interactive logins.
- Session tokens are HMAC-signed, so a token cannot be forged without the
  secret and carries nothing the server has to remember.
"""

from __future__ import annotations

import base64
import contextlib
import hashlib
import hmac
import os
import secrets
from pathlib import Path

from cryptography.fernet import Fernet, InvalidToken

#: scrypt parameters: 2**14 rounds, r=8, p=1 — the standard library's own
#: recommendation for interactive use. Roughly 50 ms per check.
SCRYPT_N, SCRYPT_R, SCRYPT_P = 2**14, 8, 1
SALT_BYTES = 16
SECRET_FILE = "app_secret"


def app_secret(db_path: str | os.PathLike[str]) -> str:
    """The application secret: the environment, else a file beside the database."""
    from_env = os.environ.get("APP_SECRET", "").strip()
    if from_env:
        return from_env

    path = Path(db_path).parent / SECRET_FILE
    if path.is_file():
        return path.read_text(encoding="utf-8").strip()

    path.parent.mkdir(parents=True, exist_ok=True)
    generated = secrets.token_urlsafe(48)
    path.write_text(generated, encoding="utf-8")
    with contextlib.suppress(OSError):
        path.chmod(0o600)
    return generated


class LocalSecrets:
    """The ``Secrets`` port, keyed from one application secret."""

    def __init__(self, secret: str) -> None:
        if not secret:
            raise ValueError("an application secret is required")
        self._secret = secret.encode("utf-8")
        # Fernet wants 32 url-safe base64 bytes; derive them so a secret of any
        # shape works and a leaked derived key does not reveal the secret.
        key = hashlib.scrypt(
            self._secret, salt=b"submission-desk-seal", n=2**14, r=8, p=1, dklen=32
        )
        self._fernet = Fernet(base64.urlsafe_b64encode(key))

    # --- secrets at rest ---------------------------------------------------

    def seal(self, plaintext: str) -> str:
        return self._fernet.encrypt(plaintext.encode("utf-8")).decode("ascii")

    def open(self, sealed: str) -> str:
        try:
            return self._fernet.decrypt(sealed.encode("ascii")).decode("utf-8")
        except (InvalidToken, ValueError) as error:
            raise ValueError(
                "A stored secret could not be opened. The application secret has "
                "changed since it was saved; enter the key again on the admin page."
            ) from error

    # --- passwords ----------------------------------------------------------

    def hash_password(self, password: str) -> str:
        salt = os.urandom(SALT_BYTES)
        digest = hashlib.scrypt(
            password.encode("utf-8"), salt=salt, n=SCRYPT_N, r=SCRYPT_R, p=SCRYPT_P
        )
        return f"scrypt${salt.hex()}${digest.hex()}"

    def verify_password(self, password: str, hashed: str) -> bool:
        try:
            scheme, salt_hex, digest_hex = hashed.split("$")
            if scheme != "scrypt":
                return False
            expected = bytes.fromhex(digest_hex)
            actual = hashlib.scrypt(
                password.encode("utf-8"),
                salt=bytes.fromhex(salt_hex),
                n=SCRYPT_N,
                r=SCRYPT_R,
                p=SCRYPT_P,
            )
        except (ValueError, TypeError):
            return False
        return hmac.compare_digest(expected, actual)

    # --- sessions -----------------------------------------------------------

    def sign(self, message: str) -> str:
        return hmac.new(self._secret, message.encode("utf-8"), hashlib.sha256).hexdigest()

    def verify_signature(self, message: str, signature: str) -> bool:
        return hmac.compare_digest(self.sign(message), signature)
