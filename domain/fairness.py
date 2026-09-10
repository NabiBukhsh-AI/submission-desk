"""What a claim or a question may not stray onto.

Two places need this rule: assessment drops an evidence claim that mentions a
protected attribute, and composition drops a generated interview question that
does. They were written separately and drifted, which is how one of them ended
up matching substrings.

Matching is on word boundaries, and that is the whole point of the module.
Substring matching looks equivalent and is not: "age" sits inside "language"
and "triage", so a substring rule silently deletes the evidence for every
language-model criterion on an AI rubric. The failure is invisible, because a
dropped claim looks exactly like a model that found nothing.

Two decisions are worth stating, because both narrow the list on purpose.

Terms with a common engineering sense are matched only in their hiring sense.
"race condition", "single sign-on" and "children of a node" are ordinary things
to write about, and a filter that eats them removes real evidence to prevent a
harm that was not occurring.

Citizenship wording is not filtered. The rubric may legitimately ask whether a
candidate stated their eligibility to work somewhere, and candidates state it in
exactly those words. Nationality as an attribute is still caught; "eligible to
work in the United Kingdom" is left alone, because filtering it would break the
criterion that asks for it.
"""

from __future__ import annotations

import re
from collections.abc import Iterable

#: Single words that carry no ordinary engineering sense. Matched whole.
PROTECTED_WORDS: tuple[str, ...] = (
    "age",
    "aged",
    "ages",
    "gender",
    "genders",
    "gendered",
    "male",
    "males",
    "female",
    "females",
    "nationality",
    "nationalities",
    "ethnicity",
    "ethnicities",
    "ethnic",
    "racial",
    "religion",
    "religions",
    "religious",
    "married",
    "marriage",
    "marital",
    "divorced",
    "widowed",
    "pregnant",
    "pregnancy",
    "disability",
    "disabilities",
    "personality",
    "appearance",
    "attractive",
    "unattractive",
    "handsome",
    "childcare",
    "children",
    "child",
)

#: Phrases whose individual words are innocuous and whose combination is not.
PROTECTED_PHRASES: tuple[str, ...] = (
    "years old",
    "year old",
    "date of birth",
    "place of birth",
    "culture fit",
    "cultural fit",
    "culture add",
    "single parent",
    "single mother",
    "single father",
    "marital status",
    "family status",
    "maternity leave",
    "paternity leave",
    "native speaker",
    "mother tongue",
    "sexual orientation",
    "gender identity",
    "racial background",
    "ethnic background",
    "race or",
    "how old",
)


#: Phrases where a protected word is being used in its structural sense. These
#: are blanked before matching, so "children of a node" survives and "children
#: at home" does not. Kept short and specific: a long exception list is a filter
#: with holes in it.
TECHNICAL_SENSES: tuple[str, ...] = (
    "child node",
    "child nodes",
    "children of",
    "child process",
    "child processes",
    "child element",
    "child elements",
    "child component",
    "child components",
    "child record",
    "child records",
    "child table",
    "child span",
    "child spans",
    "parent and child",
    "parent-child",
)


def _pattern(words: Iterable[str], phrases: Iterable[str]) -> re.Pattern[str]:
    """One compiled alternation, longest first so phrases win over their parts."""
    terms = sorted({*words, *phrases}, key=len, reverse=True)
    # Whitespace inside a phrase matches any run of whitespace, so a claim
    # broken across a line is caught the same as one on a single line.
    alternation = "|".join(re.escape(term).replace(r"\ ", r"\s+") for term in terms)
    return re.compile(rf"\b(?:{alternation})\b", re.IGNORECASE)


_DEFAULT = _pattern(PROTECTED_WORDS, PROTECTED_PHRASES)
_TECHNICAL = _pattern([], TECHNICAL_SENSES)


def _without_technical_senses(text: str) -> str:
    """Blank the structural uses so the protected ones still stand out."""
    return _TECHNICAL.sub(" ", text)


def mentions_protected_attribute(text: str, extra: Iterable[str] = ()) -> bool:
    """Whether this text mentions a protected attribute.

    ``extra`` takes a rubric's own ``forbidden_attributes``, so a recruiter
    adding one protects against it without a code change. Underscores in those
    names are read as spaces, because a rubric writes ``marital_status`` and a
    sentence writes "marital status".
    """
    cleaned = _without_technical_senses(text)
    if _DEFAULT.search(cleaned):
        return True

    additions = [str(term).replace("_", " ").strip().lower() for term in extra]
    additions = [term for term in additions if term]
    if not additions:
        return False

    return bool(_pattern([], additions).search(cleaned))


def offending_terms(text: str, extra: Iterable[str] = ()) -> list[str]:
    """Which terms matched, for a log line worth reading.

    "dropped: mentions 'gender'" tells a reviewer why a question vanished.
    A bare count does not.
    """
    cleaned = _without_technical_senses(text)
    found = [match.group(0).lower() for match in _DEFAULT.finditer(cleaned)]

    additions = [str(term).replace("_", " ").strip().lower() for term in extra]
    additions = [term for term in additions if term]
    if additions:
        found += [match.group(0).lower() for match in _pattern([], additions).finditer(cleaned)]

    return sorted(set(found))
