"""Three messages, and the test that nothing about a person is in them.

``test_no_payload_contains_pii`` is the reason this module exists. A channel is
read by people who are not the reviewer, often on a phone, often visible over a
shoulder, and archived somewhere nobody has thought about. A candidate's name in
one cannot be taken back.

The defence is an allowlist rather than a redaction list, and the difference is
the whole design. A redaction list enumerates what must not go and is always one
field behind the code. An allowlist enumerates what may go, so a field added to
a notification is absent until somebody adds it and says why it is safe. This
tests that property directly: a notification is built with contraband in every
field, and none of it reaches the channel.
"""

from __future__ import annotations

import pytest

from domain.ports.notifiers import ALLOWED_FIELDS, Notification, NotificationKind
from domain.ports.sinks import AdapterError
from infrastructure.integrations.slack import (
    TEMPLATES,
    ConsoleNotifier,
    SlackNotifier,
    render,
    safe_payload,
)
from tests.fakes.fake_transports import FakeSlack, Script, no_sleep

READY = Notification(
    kind=NotificationKind.BATCH_READY,
    ready_count=12,
    flagged_count=3,
    role_id="ai-engineer",
    link="http://localhost:8501/queue",
)

FAILED = Notification(
    kind=NotificationKind.DELIVERY_FAILED,
    failed_count=2,
    sink_id="sheets",
    error_code="rate_limited",
    link="http://localhost:8501/operations",
)

QUARANTINE = Notification(
    kind=NotificationKind.QUARANTINE_DETECTED,
    count=1,
    link="http://localhost:8501/queue",
)


def notifier(slack: FakeSlack) -> SlackNotifier:
    return SlackNotifier(slack, channel="#hiring", sleep=no_sleep)


# --- nothing about anybody ----------------------------------------------------------


def test_no_payload_contains_pii() -> None:
    """The headline. Contraband in every field a notification has, and none of
    it reaches the channel."""
    slack = FakeSlack()
    contraband = Notification(
        kind=NotificationKind.BATCH_READY,
        ready_count=1,
        role_id="ai-engineer",
        link="http://localhost:8501/queue",
    )
    # Fields the contract does not define cannot be set at all, which is the
    # point: there is no field for a name to go in.
    object.__setattr__(contraband, "candidate_name", "Ana Ferreira")
    object.__setattr__(contraband, "quotation", "Owned the payments backend")
    object.__setattr__(contraband, "score", 0.82)

    notifier(slack).notify(contraband)

    everything = slack.everything_sent
    assert "Ana Ferreira" not in everything
    assert "payments backend" not in everything
    assert "0.82" not in everything


@pytest.mark.parametrize("notification", [READY, FAILED, QUARANTINE])
def test_every_field_sent_is_on_the_allowlist(notification: Notification) -> None:
    """The property, stated directly. Anything not named cannot travel."""
    assert set(safe_payload(notification)) <= ALLOWED_FIELDS


def test_an_unlisted_field_does_not_travel() -> None:
    """A field added to a notification is absent until somebody adds it to the
    allowlist and explains why it is safe."""
    smuggled = Notification(kind=NotificationKind.BATCH_READY, ready_count=1)
    object.__setattr__(smuggled, "candidate_id", "cand-0007")

    assert "candidate_id" not in safe_payload(smuggled)


@pytest.mark.parametrize("notification", [READY, FAILED, QUARANTINE])
def test_no_message_carries_a_score(notification: Notification) -> None:
    text = render(notification)

    assert "score" not in text.lower()
    assert "band" not in text.lower()


def test_the_text_is_built_from_filtered_fields_only() -> None:
    """Formatted from the payload rather than the notification, so a field that
    failed the allowlist cannot reach the text either."""
    smuggled = Notification(kind=NotificationKind.QUARANTINE_DETECTED, count=1)
    object.__setattr__(smuggled, "candidate_name", "Ana Ferreira")

    assert "Ana" not in render(smuggled)


# --- the three events ------------------------------------------------------------------


def test_there_are_exactly_three_events() -> None:
    """Three things are worth interrupting somebody for. A fourth would make the
    channel something people mute."""
    assert len(NotificationKind) == 3
    assert set(TEMPLATES) == set(NotificationKind)


def test_a_ready_batch_says_how_many() -> None:
    text = render(READY)

    assert "12 candidate(s) ready" in text
    assert "3 needing a closer look" in text


def test_a_batch_with_nothing_flagged_says_nothing_about_flags() -> None:
    """A message reading "0 needing a closer look" makes somebody wonder what
    that means."""
    clean = Notification(kind=NotificationKind.BATCH_READY, ready_count=5, role_id="x")

    assert "needing a closer look" not in render(clean)


def test_a_failed_delivery_names_the_destination() -> None:
    text = render(FAILED)

    assert "sheets" in text
    assert "will retry" in text


def test_a_failed_delivery_says_nothing_was_lost() -> None:
    """The message somebody reads at seven in the evening."""
    assert "saved" in render(FAILED)


def test_a_quarantine_says_nothing_was_spent() -> None:
    text = render(QUARANTINE)

    assert "nothing was spent" in text
    assert "automated reader" in text


@pytest.mark.parametrize("notification", [READY, FAILED, QUARANTINE])
def test_every_message_links_somewhere(notification: Notification) -> None:
    """A count with nowhere to go is an interruption without a next step."""
    assert "localhost" in render(notification)


# --- delivery ------------------------------------------------------------------------------


def test_a_message_is_posted() -> None:
    slack = FakeSlack()

    result = notifier(slack).notify(READY)

    assert result.ok
    assert len(slack.posted) == 1


def test_a_rate_limit_is_retried() -> None:
    slack = FakeSlack(script=Script([429, 429]))

    assert notifier(slack).notify(READY).ok


def test_an_auth_failure_is_not_retried() -> None:
    slack = FakeSlack(script=Script([401] * 5))

    result = notifier(slack).notify(READY)

    assert result.error_code is AdapterError.AUTH
    assert result.attempts == 1


def test_an_auth_failure_disables_the_notifier() -> None:
    slack = FakeSlack(script=Script([401]))
    poster = notifier(slack)

    poster.notify(READY)

    assert poster.healthy() is False


def test_a_failure_is_reported_not_raised() -> None:
    """A notifier that raised would fail a delivery that had already succeeded,
    making the least important integration the one that decides whether a
    result is recorded."""
    slack = FakeSlack(script=Script([500] * 5))

    result = notifier(slack).notify(READY)

    assert result.ok is False


def test_an_unconfigured_channel_says_what_to_do() -> None:
    poster = SlackNotifier(FakeSlack(), channel="", sleep=no_sleep)

    result = poster.notify(READY)

    assert result.error_code is AdapterError.CONFIG
    assert "SLACK_CHANNEL" in result.message


# --- the default -----------------------------------------------------------------------------


def test_the_console_notifier_always_works() -> None:
    """Notifications are optional, and this is what makes that true: a
    deployment with no Slack still has a notifier, so no code path branches on
    whether one exists."""
    written: list[str] = []
    console = ConsoleNotifier(write=written.append)

    result = console.notify(READY)

    assert result.ok
    assert console.healthy() is True
    assert "12 candidate(s) ready" in written[0]


def test_the_console_notifier_carries_no_pii_either() -> None:
    written: list[str] = []
    smuggled = Notification(kind=NotificationKind.BATCH_READY, ready_count=1)
    object.__setattr__(smuggled, "candidate_name", "Ana Ferreira")

    ConsoleNotifier(write=written.append).notify(smuggled)

    assert "Ana" not in written[0]
