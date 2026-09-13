"""Sealing secrets and hashing passwords, behind a port.

The use cases that manage the admin account and the provider keys need two
things: a way to store a credential so that a copy of the database does not
give it away, and a way to store a password so that nothing gives it away. Both
are cryptography, and cryptography is an adapter concern; a use case that
imported a cipher could not be tested without one.
"""

from __future__ import annotations

from typing import Protocol


class Secrets(Protocol):
    def seal(self, plaintext: str) -> str:
        """Encrypt for storage. The result is opaque text."""
        ...

    def open(self, sealed: str) -> str:
        """Decrypt what ``seal`` produced. Raises on tampering or a wrong key."""
        ...

    def hash_password(self, password: str) -> str:
        """A salted, slow hash. Never reversible."""
        ...

    def verify_password(self, password: str, hashed: str) -> bool: ...

    def sign(self, message: str) -> str:
        """A signature over a message, for session tokens."""
        ...

    def verify_signature(self, message: str, signature: str) -> bool: ...
