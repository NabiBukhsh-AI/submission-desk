"""Removing what must not be written down, before anything writes it.

A processor, not a convention. That distinction is the entire security property
of this module: a call-site convention is a rule every future log line has to
remember, and the one that forgets is the one that writes a candidate's email
address into a file that gets copied into a ticket.

Placed first in the chain, so no sink can be reached without passing through it.
A test injects a record carrying a synthetic email and inspects both files, which
is the only way to know the ordering is what it is claimed to be.

What is redacted, and why each one:

Email and phone are the two identifiers a CV always carries and a log never
needs. Pilot names are configurable, because a pilot with three named candidates
is exactly the case where a log line becomes a privacy incident.

Quoted spans are the interesting case. A span is candidate-authored text, so
logging one logs part of their CV — but a span is also the thing the whole
system is about, and debugging a validation failure without seeing the span is
guessing. So spans are off by default, and truncated when on.
"""

from __future__ import annotations

import os
import re
from collections.abc import Iterable, MutableMapping
from typing import Any

#: How a redaction reads in the log. Kind is kept because "an email was here" is
#: useful and the address is not.
MASK = "[redacted:{kind}]"

#: Deliberately broad. A pattern that misses an unusual address is worse than one
#: that redacts a version string that looked like an email.
EMAIL = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+", re.IGNORECASE)

#: Phone numbers, as four specific shapes rather than one loose pattern.
#:
#: A single permissive pattern gets this wrong in both directions. It matched
#: only "7946 0958" out of "+44 20 7946 0958", leaving the country and area code
#: in the log — a partial redaction, which is worse than none because it looks
#: like it worked. Widened naively, the same pattern starts eating "2021-2024"
#: and "p95 210 / 900", and a redactor that mangles ordinary numbers is one
#: somebody switches off.
#:
#: So each shape is written out. Anything a person would recognise as a phone
#: number is here; a year range is not.
PHONE_PATTERNS: tuple[re.Pattern[str], ...] = (
    # International: +44 20 7946 0958, +1 (555) 010-9999, +91-98765-43210
    re.compile(r"(?<![\w./])\+\d{1,3}(?:[\s.-]?\(?\d{1,4}\)?){2,5}(?![\w./])"),
    # North American: (555) 010-9999, 555-010-9999, 555.010.9999
    re.compile(r"(?<![\w./])\(?\d{3}\)?[\s.-]\d{3}[\s.-]\d{4}(?![\w./])"),
    # National trunk form: 020 7946 0958, 07700 900123
    re.compile(r"(?<![\w./])0\d{2,4}[\s.-]?\d{3,4}[\s.-]?\d{3,4}(?![\w./])"),
    # Unbroken runs long enough that nothing else is plausible.
    re.compile(r"(?<![\w./])\d{10,15}(?![\w./])"),
)

#: Fields whose whole value is candidate text. Redacting inside them is not
#: enough: the field itself is removed unless spans are switched on.
SPAN_FIELDS = frozenset({"verbatim_span", "span", "quotation", "excerpt", "raw_text"})

#: When spans are on, this is as much as is written. Enough to identify which
#: span is being discussed, not enough to reconstruct a CV from a log file.
SPAN_MAX_CHARS = 120

#: Keys that are never written whatever they contain.
DROP_KEYS = frozenset({"api_key", "authorization", "token", "secret", "password"})


def pilot_patterns(names: Iterable[str] | None = None) -> list[re.Pattern[str]]:
    """Names to redact, from configuration.

    A pilot runs with a handful of real people in it, and their names appear in
    filenames, in profile fields, and in error messages. Configuring them is the
    difference between a log a team can share and one they cannot.
    """
    raw = names if names is not None else os.environ.get("REDACT_NAMES", "").split(",")
    return [
        re.compile(rf"\b{re.escape(name.strip())}\b", re.IGNORECASE)
        for name in raw
        if name and name.strip()
    ]


def redact_text(text: str, *, names: Iterable[re.Pattern[str]] = ()) -> str:
    """One string, with identifiers replaced.

    Order matters. Email runs before phone, because an address containing digits
    would otherwise be half-redacted by the phone pattern and become
    ``[redacted:phone]@example.com``, which leaks the domain.
    """
    result = EMAIL.sub(MASK.format(kind="email"), text)

    for pattern in PHONE_PATTERNS:
        result = pattern.sub(MASK.format(kind="phone"), result)

    for pattern in names:
        result = pattern.sub(MASK.format(kind="name"), result)

    return result


def redact_value(
    key: str, value: Any, *, names: Iterable[re.Pattern[str]] = (), log_spans: bool = False
) -> Any:
    """One field, redacted according to what it is.

    Recursive, because a log line's payload is a nested dictionary and a rule
    that only looked at the top level would miss every interesting field.
    """
    if key in DROP_KEYS:
        return MASK.format(kind="secret")

    if key in SPAN_FIELDS and isinstance(value, str):
        if not log_spans:
            return MASK.format(kind="span")
        truncated = value[:SPAN_MAX_CHARS]
        suffix = "…" if len(value) > SPAN_MAX_CHARS else ""
        return redact_text(truncated, names=names) + suffix

    if isinstance(value, str):
        return redact_text(value, names=names)

    if isinstance(value, dict):
        return {
            inner_key: redact_value(inner_key, inner_value, names=names, log_spans=log_spans)
            for inner_key, inner_value in value.items()
        }

    if isinstance(value, list | tuple):
        return type(value)(
            redact_value(key, item, names=names, log_spans=log_spans) for item in value
        )

    return value


class Redactor:
    """The processor. First in the chain, before any sink.

    Holds its configuration rather than reading the environment per call, so a
    run's redaction behaviour cannot change halfway through because something
    else edited a variable.
    """

    def __init__(
        self, *, names: Iterable[str] | None = None, log_spans: bool | None = None
    ) -> None:
        self.names = pilot_patterns(names)
        self.log_spans = (
            log_spans
            if log_spans is not None
            else os.environ.get("LOG_SPANS", "").strip().lower() in ("1", "true", "yes", "on")
        )

    def __call__(
        self, _logger: Any, _method: str, event: MutableMapping[str, Any]
    ) -> MutableMapping[str, Any]:
        """Redact a whole log record.

        Signature is structlog's processor protocol. The two ignored arguments
        are the logger and the method name, neither of which affects what must
        be removed.
        """
        return {
            key: redact_value(key, value, names=self.names, log_spans=self.log_spans)
            for key, value in event.items()
        }
