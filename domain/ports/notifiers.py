"""Telling a person something happened, without telling them who.

Three events, and the payload is an allowlist rather than a redaction. That
distinction is the design: redacting means listing what must not go, and the
list is always missing something. An allowlist means listing what may go, and
everything else is absent by construction.

A notification says a batch is ready, a delivery failed, or a document was
quarantined. It carries counts and a link. It does not carry a name, a
quotation, or a score — a channel is read by people who are not the reviewer,
often on a phone, often visible over a shoulder, and often archived somewhere
nobody has thought about.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Protocol

from domain.ports.sinks import AdapterResult


class NotificationKind(str, Enum):
    """The only three things worth interrupting somebody for."""

    BATCH_READY = "batch_ready"
    DELIVERY_FAILED = "delivery_failed"
    QUARANTINE_DETECTED = "quarantine_detected"


#: Exactly what a payload may contain. Anything not here does not go.
#:
#: An allowlist rather than a redaction list, because a redaction list is always
#: one field behind the code. A new field added to a notification is absent
#: until somebody adds it here and explains why it is safe.
ALLOWED_FIELDS = frozenset(
    {
        "kind",
        "count",
        "ready_count",
        "flagged_count",
        "failed_count",
        "role_id",
        "link",
        "sink_id",
        "error_code",
        "occurred_at",
    }
)


@dataclass(frozen=True)
class Notification:
    """One message, built only from allowed fields."""

    kind: NotificationKind
    #: How many candidates this is about. A count is not a person.
    count: int = 0
    ready_count: int = 0
    flagged_count: int = 0
    failed_count: int = 0
    #: The role, which is a job description rather than an identity.
    role_id: str = ""
    #: Where to go and look. The interface is behind whatever authentication the
    #: deployment has; the link is not a way around it.
    link: str = ""
    sink_id: str = ""
    error_code: str = ""
    occurred_at: str = ""

    def as_payload(self) -> dict[str, object]:
        """The message as fields, filtered through the allowlist.

        Filtered here rather than trusted, so a field added to this dataclass
        without being added to the allowlist is dropped rather than sent.
        """
        return {
            "kind": self.kind.value,
            **{
                name: value
                for name, value in self.__dict__.items()
                if name in ALLOWED_FIELDS and name != "kind" and value not in ("", 0)
            },
        }


class Notifier(Protocol):
    """Somewhere a short message is posted."""

    notifier_id: str

    def notify(self, notification: Notification) -> AdapterResult:
        """Post one message. Never raises.

        A notifier that raised would fail a delivery that had already succeeded,
        which would make the least important integration the one that decides
        whether a candidate's result is recorded.
        """
        ...

    def healthy(self) -> bool: ...
