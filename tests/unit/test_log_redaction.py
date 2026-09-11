"""What must never appear in a log file.

The property is not "we remember to redact". It is "the code path to a sink does
not exist without redaction", and that difference is the whole point of a
processor. A convention is a rule every future log call has to remember, and the
one that forgets is the one that writes a candidate's email into a file somebody
pastes into a ticket.

Both directions are tested. Redacting an address is easy; not mangling
"p95 210ms" and "2018 to 2021" is what keeps the redactor switched on.

check_pii: invented-identifiers. This file holds one example of every telephone
shape the redactor must catch, and some of those shapes (an Indian mobile, a
subdomain address) have no regulator-reserved value to write them with. Every
identifier here is made up.
"""

from __future__ import annotations

import pytest

from infrastructure.observability.redactor import (
    DROP_KEYS,
    MASK,
    SPAN_FIELDS,
    SPAN_MAX_CHARS,
    Redactor,
    pilot_patterns,
    redact_text,
    redact_value,
)

# --- identifiers -----------------------------------------------------------------


@pytest.mark.parametrize(
    "text",
    [
        "ana.ferreira@example.com",
        "Contact: A.Ferreira+jobs@sub.domain.co.uk",
        "send it to first_last@example.org please",
    ],
)
def test_an_email_is_removed(text: str) -> None:
    result = redact_text(text)

    assert "@" not in result or "redacted" in result
    assert "example" not in result or "redacted:email" in result


def test_the_kind_survives_the_value(text: str = "write to ana@example.com") -> None:
    """ "An email was here" is useful. The address is not."""
    assert redact_text(text) == "write to [redacted:email]"


@pytest.mark.parametrize(
    "text",
    [
        "+44 20 7946 0958",
        "+1 (555) 010-9999",
        "+91-98765-43210",
        "(555) 010-9999",
        "555-010-9999",
        "020 7946 0958",
        "07700 900123",
        "123456789012",
    ],
)
def test_a_phone_number_is_removed(text: str) -> None:
    assert redact_text(text) == MASK.format(kind="phone")


def test_a_whole_international_number_goes(text: str = "call +44 20 7946 0958 now") -> None:
    """The failure this replaced: a single loose pattern matched only the last
    two groups and left "+44 20" in the log, which looks like it worked."""
    result = redact_text(text)

    assert "44" not in result
    assert "20" not in result
    assert result == "call [redacted:phone] now"


def test_email_is_redacted_before_phone() -> None:
    """Order matters. Phone-first would half-redact an address containing digits
    and leave the domain behind."""
    result = redact_text("ana2024@example.com")

    assert result == MASK.format(kind="email")
    assert "example.com" not in result


def test_a_configured_name_is_removed() -> None:
    """A pilot with three named candidates is exactly where a log line becomes a
    privacy incident."""
    names = pilot_patterns(["Ana Ferreira"])

    assert redact_text("Ana Ferreira applied", names=names) == "[redacted:name] applied"


def test_a_configured_name_matches_case_insensitively() -> None:
    names = pilot_patterns(["ana ferreira"])

    assert "redacted:name" in redact_text("ANA FERREIRA", names=names)


def test_a_name_matches_on_word_boundaries() -> None:
    """The substring trap, again. "Ana" inside "Anastasia" is a different
    person."""
    names = pilot_patterns(["Ana"])

    assert redact_text("Anastasia Petrova", names=names) == "Anastasia Petrova"


# --- what must survive --------------------------------------------------------------


@pytest.mark.parametrize(
    "text",
    [
        "Reduced p95 latency from 900ms to 210ms",
        "Northwind Logistics, 2018 to 2021",
        "handling time fell from 40 minutes to 6",
        "version 1.2.3 build 4567",
        "2021-2024 at Acme Payments",
        "criterion evaluation-practice resolved as met",
        "120000 tokens against a 120000 ceiling",
        "run 01a08a99 finished in 4210 ms",
    ],
)
def test_ordinary_operational_text_survives(text: str) -> None:
    """A redactor that mangles ordinary numbers is one somebody switches off,
    and a redactor that is switched off redacts nothing at all."""
    assert redact_text(text) == text


# --- spans ----------------------------------------------------------------------------


def test_a_span_is_removed_by_default() -> None:
    """A span is candidate-authored text, so logging one logs part of a CV."""
    assert redact_value("verbatim_span", "Owned the payments backend") == MASK.format(kind="span")


def test_a_span_can_be_switched_on_for_debugging() -> None:
    """Debugging a validation failure without seeing the span is guessing."""
    result = redact_value("verbatim_span", "Owned the payments backend", log_spans=True)

    assert "Owned the payments backend" in result


def test_a_span_is_truncated_when_switched_on() -> None:
    """Enough to know which span is being discussed, not enough to reconstruct a
    CV from a log file."""
    long_span = "x" * (SPAN_MAX_CHARS * 3)

    result = redact_value("verbatim_span", long_span, log_spans=True)

    assert len(result) <= SPAN_MAX_CHARS + 1
    assert result.endswith("…")


def test_a_switched_on_span_is_still_redacted() -> None:
    """Turning spans on is not turning redaction off."""
    result = redact_value(
        "verbatim_span", "Reach me at ana@example.com about the role", log_spans=True
    )

    assert "ana@example.com" not in result


@pytest.mark.parametrize("field", sorted(SPAN_FIELDS))
def test_every_span_field_is_treated_as_candidate_text(field: str) -> None:
    assert redact_value(field, "some candidate text") == MASK.format(kind="span")


# --- secrets ---------------------------------------------------------------------------


@pytest.mark.parametrize("key", sorted(DROP_KEYS))
def test_a_secret_is_never_written(key: str) -> None:
    assert redact_value(key, "sk-live-abcdef") == MASK.format(kind="secret")


# --- structure --------------------------------------------------------------------------


def test_nested_payloads_are_redacted() -> None:
    """A log line's payload is a nested dictionary, and a rule that only looked
    at the top level would miss every interesting field."""
    result = redact_value(
        "payload",
        {"candidate": {"contacts": ["ana@example.com", "+44 20 7946 0958"]}},
    )

    flattened = str(result)
    assert "ana@example.com" not in flattened
    assert "7946" not in flattened


def test_numbers_pass_through_untouched() -> None:
    """Token counts and latencies are the whole point of a log line."""
    assert redact_value("input_tokens", 120_000) == 120_000
    assert redact_value("latency_ms", 4210) == 4210


def test_the_processor_returns_a_whole_record() -> None:
    redactor = Redactor(names=["Ana Ferreira"])

    result = redactor(
        None,
        "info",
        {
            "event": "assess.criterion",
            "candidate": "Ana Ferreira",
            "email": "ana@example.com",
            "input_tokens": 1200,
        },
    )

    assert result["event"] == "assess.criterion"
    assert result["input_tokens"] == 1200
    assert result["candidate"] == MASK.format(kind="name")
    assert result["email"] == MASK.format(kind="email")


def test_the_processor_reads_its_configuration_once() -> None:
    """A run's redaction behaviour cannot change halfway through because
    something else edited an environment variable."""
    redactor = Redactor(names=["Ana"], log_spans=False)

    assert redactor.log_spans is False
    assert len(redactor.names) == 1


def test_an_empty_name_list_does_not_match_everything() -> None:
    """An empty pattern would match at every position in every string."""
    assert pilot_patterns(["", "   ", None]) == []  # type: ignore[list-item]
    assert redact_text("anything", names=pilot_patterns([])) == "anything"
