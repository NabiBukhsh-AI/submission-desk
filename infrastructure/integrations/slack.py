"""Three messages, and nothing about anybody.

A channel is read by people who are not the reviewer, often on a phone, often
visible over a shoulder, and archived somewhere nobody has thought about. So a
notification carries counts and a link and nothing else.

The payload is built from an allowlist, not scrubbed with a redaction list. A
redaction list is a list of what must not go, and it is always one field behind
the code; an allowlist is a list of what may go, and a new field is absent until
somebody adds it and says why it is safe. The allowlist lives in the port, and
this adapter filters through it again on the way out — twice, because the cost of
being wrong once is a candidate's name in a company Slack.

Three events, because three things are worth interrupting somebody for: a batch
is ready, a delivery failed, a document was quarantined.
"""

from __future__ import annotations

import os
import time
from typing import Any, Protocol

from domain.ports.notifiers import ALLOWED_FIELDS, Notification, NotificationKind
from domain.ports.sinks import AdapterError, AdapterResult
from infrastructure.integrations.retrying import classify_status, with_retries
from infrastructure.observability.logging import get_logger

#: How each event reads in the channel. Written here so the wording is
#: reviewable as a set, and so nothing can be assembled from candidate data at
#: the call site.
TEMPLATES: dict[NotificationKind, str] = {
    NotificationKind.BATCH_READY: (
        "{ready_count} candidate(s) ready to review for {role_id}{flagged}."
    ),
    NotificationKind.DELIVERY_FAILED: (
        "{failed_count} result(s) could not be sent to {sink_id} ({error_code}). "
        "They are saved and will retry."
    ),
    NotificationKind.QUARANTINE_DETECTED: (
        "{count} document(s) were flagged as containing content aimed at an "
        "automated reader. Nothing was assessed and nothing was spent."
    ),
}

#: Appended when the notification carries a link. A deployment that has not
#: said where its interface lives sends the sentence alone.
LINK_LABEL = "Open the queue"


class SlackTransportError(Exception):
    def __init__(self, status: int, message: str = "") -> None:
        super().__init__(message or f"HTTP {status}")
        self.status = status


class SlackTransport(Protocol):
    """One call. Posting a message is the entire integration."""

    def post(self, channel: str, text: str, payload: dict[str, Any]) -> str:
        """Post, returning whatever the far side calls the message."""
        ...


class SlackNotifier:
    """Posts one of three messages to one channel."""

    notifier_id = "slack"

    def __init__(
        self,
        transport: SlackTransport,
        *,
        channel: str = "",
        sleep: Any = time.sleep,
    ) -> None:
        self.transport = transport
        self.channel = channel or os.environ.get("SLACK_CHANNEL", "")
        self._sleep = sleep
        self._disabled_reason: str | None = None

    def healthy(self) -> bool:
        return self._disabled_reason is None and bool(self.channel)

    def notify(self, notification: Notification) -> AdapterResult:
        if not self.channel:
            return AdapterResult.failed(
                AdapterError.CONFIG,
                "No Slack channel is configured. Set SLACK_CHANNEL, or remove "
                "slack from NOTIFIER_ADAPTER.",
            )

        text = render(notification)
        payload = safe_payload(notification)

        result = with_retries(lambda: self._post(text, payload), sleep=self._sleep)
        if not result.ok and result.error_code and result.error_code.disables_adapter:
            self._disabled_reason = result.message
        # The outcome, in the log the operator watches: which message, whether
        # Slack took it, and if not, what it said. Counts only, like the message.
        get_logger().info(
            "notification.sent" if result.ok else "notification.failed",
            notifier=self.notifier_id,
            kind=notification.kind.value,
            **{k: v for k, v in payload.items() if k not in ("kind", "link", "occurred_at")},
            error_code=result.error_code.value if result.error_code else None,
            message=None if result.ok else result.message,
        )
        return result

    def _post(self, text: str, payload: dict[str, object]) -> AdapterResult:
        try:
            reference = self.transport.post(self.channel, text, dict(payload))
        except Exception as error:
            return _failure(error)
        return AdapterResult.succeeded(reference)


def safe_payload(notification: Notification) -> dict[str, object]:
    """The payload, filtered through the allowlist a second time.

    The port already filters. This filters again, because the two lines of code
    cost nothing and the failure they prevent is a candidate's name in a company
    channel, which cannot be taken back.
    """
    return {key: value for key, value in notification.as_payload().items() if key in ALLOWED_FIELDS}


def render(notification: Notification) -> str:
    """The message text, from a template and allowed fields only.

    Formatted from the filtered payload rather than from the notification, so a
    field that did not survive the allowlist cannot reach the text either.
    """
    fields = safe_payload(notification)
    flagged = fields.get("flagged_count", 0)

    text = TEMPLATES[notification.kind].format(
        ready_count=fields.get("ready_count", 0),
        failed_count=fields.get("failed_count", 0),
        count=fields.get("count", 0),
        role_id=fields.get("role_id", "this role"),
        sink_id=fields.get("sink_id", "the destination"),
        error_code=fields.get("error_code", "unknown"),
        flagged=f", {flagged} needing a closer look" if flagged else "",
    )
    link = fields.get("link", "")
    return f"{text} <{link}|{LINK_LABEL}>" if link else text


class ConsoleNotifier:
    """The default. Prints, and cannot fail.

    Notifications are optional and this is what makes that true: a deployment
    with no Slack still has a notifier, so no code path has to branch on whether
    one exists.
    """

    notifier_id = "console"

    def __init__(self, write: Any = print) -> None:
        self._write = write

    def healthy(self) -> bool:
        return True

    def notify(self, notification: Notification) -> AdapterResult:
        self._write(render(notification))
        return AdapterResult.succeeded("console")


def _failure(error: Exception) -> AdapterResult:
    status = getattr(error, "status", None)
    if status is None:
        return AdapterResult.failed(
            AdapterError.TRANSIENT,
            "Slack could not be reached, so the notification was not posted.",
        )

    code = classify_status(int(status))
    return AdapterResult.failed(
        code,
        {
            AdapterError.AUTH: (
                "Slack refused the token, so the notification was not posted. "
                "The token needs renewing."
            ),
            AdapterError.CONFIG: (
                "The configured Slack channel was not found. Check SLACK_CHANNEL."
            ),
            AdapterError.RATE_LIMITED: (
                "Slack is asking for fewer requests. This will retry on its own."
            ),
            AdapterError.TRANSIENT: ("Slack had a problem. This will retry on its own."),
            AdapterError.PERMANENT: "Slack refused the message.",
        }[code],
    )
