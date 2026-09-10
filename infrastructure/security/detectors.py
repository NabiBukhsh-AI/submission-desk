"""Deterministic detectors. The primary control, and the only one that votes.

Each detector is a pure function over text, spans, or metadata, returning
``IntegrityFinding`` objects with a stable id. Pure because a detector that
reaches a network cannot be run over a corpus, and a control nobody can measure
is a control nobody can trust.

The stable id is what makes precision measurable per detector. When a true
negative fires, the id says which pattern bank to narrow, rather than sending
somebody to read the whole module.

A classifier appears later in the pipeline and may only raise severity. That
asymmetry is the design: a classifier that can clear a finding is a classifier
worth attacking, because clearing one is exactly what an attacker wants.
"""

from __future__ import annotations

import re
import unicodedata
from collections import Counter
from collections.abc import Iterable, Sequence
from dataclasses import dataclass

from domain.contracts.enums import IntegrityFindingKind, Severity
from domain.contracts.integrity import EXCERPT_MAX_CHARS, IntegrityFinding

# --- what counts as an instruction aimed at a machine -------------------------
#
# Two banks, because they carry different weight. A system-addressing pattern is
# only ever written to steer a model; an imperative is something a person might
# write about their own work, and needs corroboration before it counts.

#: Only ever addressed to a model. One of these is enough on its own, so each
#: has to earn that on its own too.
#:
#: A bare noun phrase does not qualify, however suspicious it looks. "the
#: previous instructions" appears in a sentence about an out-of-date runbook,
#: and a corpus of real CVs has such sentences in it. What qualifies is a verb
#: aimed at the reader, or a name for the reader.
SYSTEM_ADDRESSING: tuple[str, ...] = (
    r"you\s+are\s+now",
    r"as\s+an?\s+AI\b",
    r"as\s+a\s+language\s+model",
    r"^\s*system\s*:",
    r"^\s*assistant\s*:",
    # An imperative *and* its object, together. "Ignore the previous
    # instructions" is addressed outward; "the previous instructions" is not.
    r"(?:ignore|disregard|forget|override|discard)\s+(?:the\s+|all\s+|any\s+|your\s+)?"
    r"(?:previous|prior|above|preceding|earlier|system|initial)?\s*"
    r"(?:instructions?|prompts?|rules?|directives?|guidelines?)",
    r"follow\s+(?:these|the\s+following)\s+instructions?\s+instead",
    r"your\s+(?:new\s+)?instructions?\s+(?:are|is)",
    # "automated screener" is what an attacker writes; "automated system" is
    # what an engineer writes about their own work, and the corpus contains a
    # researcher whose CV says "how automated systems should be evaluated".
    r"automated\s+(?:screener|reviewer|recruiter)",
    r"AI\s+(?:screener|reviewer|reader)",
    r"note\s+to\s+the\s+(?:automated\s+)?(?:screener|reviewer|system|reader|AI)",
    r"the\s+(?:screening\s+)?system\s+should\s+(?:mark|rate|score|recommend|assign|skip)",
)

#: Things a person might legitimately write, so these need a second-person or
#: system-addressing token nearby before they mean anything.
IMPERATIVE_PATTERNS: tuple[str, ...] = (
    r"ignore\s+(?:the\s+|all\s+)?(?:previous|prior|above|preceding|earlier)",
    r"disregard\s+(?:the\s+|all\s+)?(?:previous|prior|above|preceding|earlier)",
    r"forget\s+(?:the\s+|all\s+)?(?:previous|prior|above|everything)",
    r"output\s+only",
    r"respond\s+only\s+with",
    r"rate\s+this\s+candidate",
    r"recommend\s+hiring",
    r"assign\s+the\s+highest",
    r"set\s+the\s+score",
    r"give\s+(?:this\s+candidate\s+)?(?:the\s+)?(?:highest|top|maximum)",
    r"mark\s+(?:this\s+candidate\s+)?as\s+(?:qualified|suitable|approved)",
    r"do\s+not\s+(?:mention|report|flag)",
    r"must\s+(?:advance|approve|hire)",
)

#: Proximity tokens. An imperative near one of these is addressed outward; the
#: same imperative in a paragraph about the candidate's own work is not.
ADDRESSING_TOKENS: tuple[str, ...] = (
    r"\byou\b",
    r"\byour\b",
    r"\bAI\b",
    r"\bmodel\b",
    r"\bsystem\b",
    r"\bscreener\b",
    r"\bassistant\b",
    r"\bLLM\b",
    r"\bchatbot\b",
    r"\bprompt\b",
    r"\bparser\b",
    r"\breviewer\b",
    r"\bscreening\b",
)

#: How far either side of an imperative an addressing token still counts.
#: Roughly a sentence. Wider and ordinary prose starts firing; narrower and
#: "you should ignore the previous formatting" slips through.
PROXIMITY_WINDOW = 120

#: Chat and completion delimiters. None of these belong in a CV.
ROLE_TOKENS: tuple[str, ...] = (
    r"<\|",
    r"\|>",
    r"</s>",
    r"<s>",
    r"\[INST\]",
    r"\[/INST\]",
    r"###\s*system",
    r"###\s*instruction",
    r"<\s*system\s*>",
    r"^\s*assistant\s*:",
    r"^\s*system\s*:",
    r"<\|im_start\|>",
    r"<\|endoftext\|>",
)

#: Carry no legitimate content in a CV. The normalization profile strips them;
#: this reports that they were there, because their presence is the signal.
INVISIBLE_CHARS: dict[str, str] = {
    "\u200b": "zero-width space",
    "‌": "zero-width non-joiner",
    "‍": "zero-width joiner",
    "⁠": "word joiner",
    "﻿": "zero-width no-break space",
    "‪": "left-to-right embedding",
    "‫": "right-to-left embedding",
    "‬": "pop directional formatting",
    "‭": "left-to-right override",
    "‮": "right-to-left override",
    "⁦": "left-to-right isolate",
    "⁧": "right-to-left isolate",
    "⁨": "first strong isolate",
    "⁩": "pop directional isolate",
    "­": "soft hyphen",
}

#: Tag characters, a whole block that can encode hidden ASCII.
TAG_RANGE = range(0xE0000, 0xE0080)

#: A run of hidden text shorter than this is noise: a stray white space, a
#: rendering artefact, a superscript. Longer than this and somebody meant it.
HIDDEN_RUN_MIN_CHARS = 40

#: The render diff compares two readings of the same page, so it needs more
#: slack before a difference means anything.
RENDER_DIFF_MIN_CHARS = 60

#: Below this the text is not meant to be read by a person.
MIN_VISIBLE_PT = 3.0

#: How close a span's colour may be to the background before it is hidden.
HIDDEN_COLOUR_DELTA = 0.08

#: A phrase repeated more than this is keyword stuffing.
REPEAT_THRESHOLD = 6

#: Two distinct imperatives corroborate each other. One could be a coincidence
#: of phrasing next to an unrelated "you".
CORROBORATING_IMPERATIVES = 2

#: Alpha below this is text nobody can see, whatever colour it claims to be.
INVISIBLE_ALPHA = 0.05

#: Scripts that mixing inside a single word is a homoglyph attack. Latin plus
#: any of these in one word is not something a keyboard produces by accident.
CONFUSABLE_SCRIPTS = ("CYRILLIC", "GREEK")


@dataclass(frozen=True)
class TextSpan:
    """One run of text with how it was drawn.

    Supplied by the extractor. Everything optional, because a plain-text
    document has no colour and a DOCX has no media box, and a detector that
    required them could not run at all on those.
    """

    text: str
    page_number: int = 1
    norm_start: int = 0
    font_size: float | None = None
    colour: tuple[float, float, float] | None = None
    background: tuple[float, float, float] | None = None
    alpha: float | None = None
    #: (x0, y0, x1, y1) in page coordinates, and the page's media box.
    bbox: tuple[float, float, float, float] | None = None
    media_box: tuple[float, float, float, float] | None = None
    behind_image: bool = False


def _excerpt(text: str, limit: int = EXCERPT_MAX_CHARS) -> str:
    """What a reviewer is shown, and the most that ever reaches a classifier.

    Collapsed to one line: an excerpt containing newlines would break the
    reviewer's table and, in the classifier's prompt, would let flagged content
    lay out its own section.
    """
    collapsed = " ".join(text.split())
    if len(collapsed) <= limit:
        return collapsed
    return collapsed[: limit - 1] + "…"


def _finding(
    kind: IntegrityFindingKind,
    severity: Severity,
    detector: str,
    confidence: float,
    excerpt: str,
) -> IntegrityFinding:
    return IntegrityFinding(
        kind=kind,
        severity=severity,
        detector=detector,
        detector_confidence=confidence,
        excerpt=_excerpt(excerpt),
    )


# --- D-INSTR-IMPERATIVE -------------------------------------------------------


def _matches(patterns: Iterable[str], text: str) -> list[re.Match[str]]:
    found: list[re.Match[str]] = []
    for pattern in patterns:
        found += list(re.finditer(pattern, text, re.IGNORECASE | re.MULTILINE))
    return found


def _addressed_outward(text: str, at: int) -> bool:
    """Whether something nearby is talking to a machine.

    This is the whole false-positive control. "I disregarded the previous
    approach and rebuilt the pipeline" is a sentence an engineer writes about
    their own work; the same words next to "you" or "the model" are not.
    """
    window = text[max(0, at - PROXIMITY_WINDOW) : at + PROXIMITY_WINDOW]
    return any(re.search(token, window, re.IGNORECASE) for token in ADDRESSING_TOKENS)


def detect_instructions(text: str) -> list[IntegrityFinding]:
    """Instructions in the document aimed at whatever reads it.

    HIGH for a system-addressing pattern, or for two distinct imperatives.
    MEDIUM for a single corroborated imperative, because one could still be a
    coincidence of phrasing and MEDIUM keeps the run going while telling the
    reviewer.
    """
    system_hits = _matches(SYSTEM_ADDRESSING, text)
    imperative_hits = [
        match
        for match in _matches(IMPERATIVE_PATTERNS, text)
        if _addressed_outward(text, match.start())
    ]

    if not system_hits and not imperative_hits:
        return []

    distinct = {match.re.pattern for match in imperative_hits}

    # The earliest hit in the *text*, not the first pattern in the bank that
    # happened to match. The excerpt is what a reviewer reads when deciding
    # whether to override a quarantine, so it has to be the passage they would
    # look for, and it has to contain the matched phrase rather than a window
    # that starts after it.
    first = min(system_hits or imperative_hits, key=lambda match: match.start())
    excerpt = text[max(0, first.start() - 40) : first.start() + 200]

    if system_hits:
        severity, confidence = Severity.HIGH, 0.9
    elif len(distinct) >= CORROBORATING_IMPERATIVES:
        severity, confidence = Severity.HIGH, 0.8
    else:
        # One imperative near a "you". Real often enough to report, thin enough
        # that quarantining on it would stop legitimate CVs.
        severity, confidence = Severity.MEDIUM, 0.55

    return [
        _finding(
            IntegrityFindingKind.INSTRUCTION_PATTERN,
            severity,
            "D-INSTR-IMPERATIVE",
            confidence,
            excerpt,
        )
    ]


# --- D-ROLE-TOKEN -------------------------------------------------------------


def detect_role_tokens(text: str) -> list[IntegrityFinding]:
    """Chat-format delimiters in a CV.

    No word processor emits these. There is no benign explanation, so one is
    enough and the confidence is high.
    """
    hits = _matches(ROLE_TOKENS, text)
    if not hits:
        return []

    first = hits[0]
    return [
        _finding(
            IntegrityFindingKind.INSTRUCTION_PATTERN,
            Severity.HIGH,
            "D-ROLE-TOKEN",
            0.95,
            text[max(0, first.start() - 40) : first.start() + 160],
        )
    ]


# --- D-HIDDEN-COLOUR ----------------------------------------------------------


def _distance(left: tuple[float, float, float], right: tuple[float, float, float]) -> float:
    return max(abs(a - b) for a, b in zip(left, right, strict=True))


def detect_hidden_colour(spans: Sequence[TextSpan]) -> list[IntegrityFinding]:
    """Text the same colour as the paper, or nearly transparent.

    The classic. White-on-white keyword stuffing predates language models by
    twenty years; what is new is that the hidden text now says "ignore your
    instructions" instead of listing skills.
    """
    findings = []
    for span in spans:
        if len(span.text.strip()) < HIDDEN_RUN_MIN_CHARS:
            continue

        invisible = span.alpha is not None and span.alpha < INVISIBLE_ALPHA
        if not invisible and span.colour is not None and span.background is not None:
            invisible = _distance(span.colour, span.background) <= HIDDEN_COLOUR_DELTA

        if invisible:
            findings.append(
                _finding(
                    IntegrityFindingKind.HIDDEN_TEXT,
                    Severity.HIGH,
                    "D-HIDDEN-COLOUR",
                    0.9,
                    span.text,
                )
            )
    return findings


# --- D-HIDDEN-SIZE ------------------------------------------------------------


def detect_hidden_size(spans: Sequence[TextSpan]) -> list[IntegrityFinding]:
    """Text too small for a person to read.

    Two-point type is not a design choice. Nothing legitimate in a CV is set
    below three points, including the smallest footnote anybody has ever used.
    """
    findings = []
    for span in spans:
        if span.font_size is None or span.font_size >= MIN_VISIBLE_PT:
            continue
        if len(span.text.strip()) < HIDDEN_RUN_MIN_CHARS:
            continue

        findings.append(
            _finding(
                IntegrityFindingKind.HIDDEN_TEXT,
                Severity.HIGH,
                "D-HIDDEN-SIZE",
                0.9,
                span.text,
            )
        )
    return findings


# --- D-OFFPAGE ----------------------------------------------------------------


def detect_offpage(spans: Sequence[TextSpan]) -> list[IntegrityFinding]:
    """Text outside the printable page, or underneath an opaque image.

    Extracted by a parser, invisible to a reader. The same asymmetry the whole
    of this module is about.
    """
    findings = []
    for span in spans:
        if len(span.text.strip()) < HIDDEN_RUN_MIN_CHARS:
            continue

        hidden = span.behind_image
        if not hidden and span.bbox is not None and span.media_box is not None:
            x0, y0, x1, y1 = span.bbox
            mx0, my0, mx1, my1 = span.media_box
            hidden = x1 < mx0 or x0 > mx1 or y1 < my0 or y0 > my1

        if hidden:
            findings.append(
                _finding(
                    IntegrityFindingKind.HIDDEN_TEXT,
                    Severity.HIGH,
                    "D-OFFPAGE",
                    0.85,
                    span.text,
                )
            )
    return findings


# --- D-ZERO-WIDTH -------------------------------------------------------------


def detect_invisible_glyphs(text: str) -> list[IntegrityFinding]:
    """Characters with no width.

    MEDIUM rather than HIGH because a copy-and-paste from a web page picks these
    up honestly. The normalization profile removes them; this records that they
    were present, which is the part that matters when a span later fails to
    match.
    """
    present: Counter[str] = Counter()
    for char in text:
        if char in INVISIBLE_CHARS:
            present[INVISIBLE_CHARS[char]] += 1
        elif ord(char) in TAG_RANGE:
            present["tag character"] += 1

    if not present:
        return []

    described = ", ".join(
        f"{name} ({count})" for name, count in sorted(present.items(), key=lambda row: -row[1])
    )
    return [
        _finding(
            IntegrityFindingKind.INVISIBLE_GLYPHS,
            Severity.MEDIUM,
            "D-ZERO-WIDTH",
            0.7,
            f"invisible characters present: {described}",
        )
    ]


# --- D-HOMOGLYPH --------------------------------------------------------------


def _script_of(char: str) -> str | None:
    try:
        name = unicodedata.name(char)
    except ValueError:
        return None
    return name.split()[0]


def detect_homoglyphs(text: str) -> list[IntegrityFinding]:
    """Words mixing Latin with a confusable script.

    A Cyrillic "а" inside "manager" defeats a keyword match while reading
    identically. MEDIUM: multilingual CVs are ordinary, but mixing scripts
    inside one word is not something a keyboard does.
    """
    offenders = []
    for word in re.findall(r"\w{3,}", text, re.UNICODE):
        scripts = {script for char in word if (script := _script_of(char)) is not None}
        if "LATIN" in scripts and scripts & set(CONFUSABLE_SCRIPTS):
            offenders.append(word)

    if not offenders:
        return []

    return [
        _finding(
            IntegrityFindingKind.ENCODING_OBFUSCATION,
            Severity.MEDIUM,
            "D-HOMOGLYPH",
            0.75,
            "mixed-script words: " + ", ".join(sorted(set(offenders))[:8]),
        )
    ]


# --- D-METADATA ---------------------------------------------------------------

#: The fields worth reading. Named explicitly rather than scanning everything,
#: so a producer string mentioning "Word" cannot fire an instruction detector.
METADATA_FIELDS: tuple[str, ...] = (
    "title",
    "subject",
    "keywords",
    "author",
    "creator",
    "comments",
    "description",
    "category",
    "content_status",
)


def detect_metadata_instructions(metadata: dict[str, str]) -> list[IntegrityFinding]:
    """Instructions hidden in document properties.

    Nobody reads these, which is the appeal. Note what happens to the value
    afterwards: it is reported to the reviewer and never placed in a prompt.
    Metadata is the one part of a document with no reason to reach a model at
    all, so it does not.
    """
    findings = []
    for field in METADATA_FIELDS:
        value = (metadata.get(field) or "").strip()
        if not value:
            continue

        hits = _matches(SYSTEM_ADDRESSING, value) + _matches(IMPERATIVE_PATTERNS, value)
        if hits:
            findings.append(
                _finding(
                    IntegrityFindingKind.METADATA_INSTRUCTION,
                    Severity.HIGH,
                    "D-METADATA",
                    0.9,
                    f"{field}: {value}",
                )
            )
    return findings


# --- D-REPETITION -------------------------------------------------------------


def detect_repetition(text: str, threshold: int = REPEAT_THRESHOLD) -> list[IntegrityFinding]:
    """A phrase repeated far past the point of meaning it.

    LOW on purpose. Keyword stuffing is a nuisance rather than an attack: it
    does not change what a criterion resolves to, because a criterion counts
    distinct supporting quotations and the rule engine reads states, not word
    frequencies. Worth telling a reviewer, not worth stopping a run.
    """
    phrases = Counter(
        " ".join(words) for words in _windows(re.findall(r"[a-z]{3,}", text.lower()), size=3)
    )
    if not phrases:
        return []

    phrase, count = phrases.most_common(1)[0]
    if count <= threshold:
        return []

    return [
        _finding(
            IntegrityFindingKind.EXCESSIVE_REPETITION,
            Severity.LOW,
            "D-REPETITION",
            0.6,
            f'"{phrase}" appears {count} times',
        )
    ]


def _windows(items: Sequence[str], size: int) -> Iterable[Sequence[str]]:
    for index in range(len(items) - size + 1):
        yield items[index : index + size]


# --- the set -------------------------------------------------------------------

#: Every detector id, for the observability page and for the corpus report.
DETECTOR_IDS: tuple[str, ...] = (
    "D-INSTR-IMPERATIVE",
    "D-ROLE-TOKEN",
    "D-HIDDEN-COLOUR",
    "D-HIDDEN-SIZE",
    "D-OFFPAGE",
    "D-RENDER-DIFF",
    "D-ZERO-WIDTH",
    "D-HOMOGLYPH",
    "D-METADATA",
    "D-REPETITION",
)


def run_text_detectors(text: str, *, raw_text: str | None = None) -> list[IntegrityFinding]:
    """Everything that only needs the text.

    ``raw_text`` is the string as it was in the file, before normalization.
    Only the invisible-glyph detector reads it, and it has to: the profile
    strips zero-width and bidi characters, so looking for them in normalized
    text would find none in any document ever.
    """
    return [
        *detect_instructions(text),
        *detect_role_tokens(text),
        *detect_invisible_glyphs(raw_text if raw_text is not None else text),
        *detect_homoglyphs(text),
        *detect_repetition(text),
    ]


def run_span_detectors(spans: Sequence[TextSpan]) -> list[IntegrityFinding]:
    """Everything that needs to know how the text was drawn."""
    return [
        *detect_hidden_colour(spans),
        *detect_hidden_size(spans),
        *detect_offpage(spans),
    ]
