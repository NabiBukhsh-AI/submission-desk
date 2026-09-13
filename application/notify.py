"""Posting a notification, when the deployment has somewhere to post it.

One function, so the three places that have something to say — a batch
finished, a document was quarantined, a delivery failed — do not each decide
whether a notifier exists, whether demo mode forbids it, or what the link is.
The notifier contract says ``notify`` never raises; this relies on that, so a
channel being down cannot fail a run that has already succeeded.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from application.deps import Deps
from domain.ports.notifiers import Notification, NotificationKind


def notify(deps: Deps, kind: NotificationKind, **fields: Any) -> None:
    """Send one message if there is a notifier and the deployment is live."""
    if deps.notifier is None or deps.settings.demo_mode:
        return
    settings = deps.settings
    link = f"{settings.public_url}/#/queue" if settings.public_url else ""
    deps.notifier.notify(
        Notification(
            kind=kind,
            link=link,
            occurred_at=datetime.now(UTC).isoformat(timespec="seconds"),
            **fields,
        )
    )
