"""Retrying, and knowing when not to.

Shared by the three network adapters, because the policy is a decision about the
system and not about any one provider. Three of them with three slightly
different backoff loops is three places for the auth case to be got wrong.

The rule worth stating: an AUTH or CONFIG failure is never retried. A wrong
credential does not become right by being asked again, and hammering an
authentication endpoint is how an account gets locked out during a demo. Those
two disable the adapter for the session and surface one message saying what to
fix, rather than one message per candidate.

Jitter is not decoration. Without it a batch of twenty candidates that all hit a
rate limit retries in lockstep, arrives together, and gets rate-limited again.
"""

from __future__ import annotations

import random
import time
from collections.abc import Callable
from dataclasses import dataclass

from domain.ports.sinks import AdapterError, AdapterResult

#: Attempts in total, not retries after the first. Three is enough to survive a
#: restart on the far side and few enough that a person is not left waiting.
MAX_ATTEMPTS = 3

#: The first wait. Doubles each time.
BASE_DELAY_S = 0.5

#: The ceiling on a single wait, so a long backoff cannot outlast a reviewer's
#: patience or a request timeout.
MAX_DELAY_S = 8.0

#: How much of the delay is random. Enough to break up a synchronised batch.
JITTER = 0.3


def backoff_delay(
    attempt: int, *, base: float = BASE_DELAY_S, rng: random.Random | None = None
) -> float:
    """How long to wait before attempt ``attempt`` (1-based).

    Exponential with jitter, capped. Separated from the loop so the schedule can
    be tested without waiting for it.
    """
    generator = rng or random
    raw = base * (2 ** max(attempt - 1, 0))
    jittered = raw * (1 - JITTER + generator.random() * 2 * JITTER)

    # Capped after jitter, not before. Capping first and then multiplying by up
    # to 1 + JITTER let the returned delay exceed the ceiling by thirty per
    # cent, which is a cap that does not cap.
    return min(jittered, MAX_DELAY_S)


@dataclass
class TokenBucket:
    """A simple rate limit, so this system is a good client.

    Provider quotas are shared across a whole account. A batch that saturates
    one takes down whatever else that account is doing, which is a way to lose
    access that has nothing to do with the code being wrong.
    """

    rate_per_second: float
    capacity: float = 0.0
    _tokens: float = 0.0
    _last: float = 0.0

    def __post_init__(self) -> None:
        self.capacity = self.capacity or max(self.rate_per_second, 1.0)
        self._tokens = self.capacity
        self._last = time.monotonic()

    def take(self, *, sleep: Callable[[float], None] = time.sleep) -> None:
        """Wait until one call is allowed."""
        if self.rate_per_second <= 0:
            return

        now = time.monotonic()
        self._tokens = min(self.capacity, self._tokens + (now - self._last) * self.rate_per_second)
        self._last = now

        if self._tokens < 1.0:
            sleep((1.0 - self._tokens) / self.rate_per_second)
            self._tokens = 0.0
            self._last = time.monotonic()
            return

        self._tokens -= 1.0


def with_retries(
    call: Callable[[], AdapterResult],
    *,
    max_attempts: int = MAX_ATTEMPTS,
    sleep: Callable[[float], None] = time.sleep,
    rng: random.Random | None = None,
) -> AdapterResult:
    """Run a call, retrying only what is worth retrying.

    ``sleep`` and ``rng`` are injected so a test can exercise three attempts and
    a backoff schedule in no time at all. A test that actually waited would be
    slow enough that somebody would eventually delete it.
    """
    last = AdapterResult.failed(AdapterError.TRANSIENT, "The call was never attempted.")

    for attempt in range(1, max_attempts + 1):
        last = call()

        if last.ok:
            return AdapterResult(
                ok=True,
                external_ref=last.external_ref,
                message=last.message,
                attempts=attempt,
                latency_ms=last.latency_ms,
                detail=last.detail,
            )

        if not last.retryable:
            # AUTH, CONFIG and PERMANENT. Asking again changes nothing, and for
            # the first two it makes things worse.
            break

        if attempt < max_attempts:
            sleep(backoff_delay(attempt, rng=rng))

    return AdapterResult(
        ok=False,
        error_code=last.error_code,
        message=last.message,
        attempts=min(attempt, max_attempts),
        latency_ms=last.latency_ms,
        detail=last.detail,
    )


#: The statuses that mean something specific about what to do next. Named
#: because "429" in a condition is a number a reader has to look up, and the
#: whole point of this function is that a reader can check the policy.
TOO_MANY_REQUESTS = 429
UNAUTHORISED = (401, 403)
NOT_FOUND = 404
SERVER_ERRORS = range(500, 600)


def classify_status(status: int) -> AdapterError:
    """An HTTP status, as a decision about what to do next.

    In one place because all three adapters speak to HTTP services, and three
    copies of this mapping is three chances to retry a 403.
    """
    if status == TOO_MANY_REQUESTS:
        return AdapterError.RATE_LIMITED
    if status in UNAUTHORISED:
        return AdapterError.AUTH
    if status == NOT_FOUND:
        return AdapterError.CONFIG
    if status in SERVER_ERRORS:
        return AdapterError.TRANSIENT
    return AdapterError.PERMANENT
