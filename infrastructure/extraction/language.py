"""What language a document is in, and what the system does about it.

Prompts are English; documents are passed in whatever language they arrived in.
There is deliberately no translation step, because a translated document breaks
verbatim span validation against its own source: the quotation a model returned
would exist in the translation and not in the file the reviewer opens.

That is a genuine engineering contradiction rather than a missing feature, and
it belongs in LIMITATIONS.md rather than behind a comfortable abstraction.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass

from langdetect import DetectorFactory
from langdetect import detect as langdetect_detect
from langdetect.lang_detect_exception import LangDetectException

#: No single language above this share means the document is genuinely mixed,
#: which is common for CVs listing publications or employers abroad.
DOMINANCE_THRESHOLD = 0.6

#: Processed normally.
ALLOWED = ("en",)

#: Processed, flagged, and reported as being of unmeasured quality. Korean is
#: here rather than in ALLOWED because nothing has measured how well assessment
#: works in it, and claiming otherwise would be a number nobody ran.
SUPPORTED = ("en", "ko")

#: Characters below which a page is not a language sample. A page of dates and
#: single words defeats every detector, and forcing a guess out of it is how an
#: English CV gets reported as Afrikaans.
MIN_SAMPLE_CHARS = 40


@dataclass(frozen=True)
class LanguageReport:
    """What was detected, and what the policy says about it."""

    languages: tuple[str, ...]
    proportions: dict[str, float]
    dominant: str | None
    mixed: bool

    @property
    def is_allowed(self) -> bool:
        return self.dominant in ALLOWED

    @property
    def is_supported(self) -> bool:
        return self.dominant in SUPPORTED

    @property
    def quality_is_unmeasured(self) -> bool:
        """Proceeding, with no evidence that it works well in this language."""
        return self.is_supported and not self.is_allowed

    @property
    def score(self) -> float:
        """The language component of extraction confidence."""
        if self.dominant is None:
            return 0.0
        return 0.5 if self.mixed else 1.0


def detect(page_samples: list[str]) -> LanguageReport:
    """Detect languages across page samples.

    Sampled per page rather than over the whole document, so a CV that is
    English throughout with one Korean address does not come back as mixed.
    A page the detector cannot read contributes nothing rather than raising.
    """
    counts: Counter[str] = Counter()
    for sample in page_samples:
        code = detect_one(sample)
        if code:
            counts[code] += 1

    if not counts:
        return LanguageReport(languages=(), proportions={}, dominant=None, mixed=False)

    total = sum(counts.values())
    proportions = {code: count / total for code, count in counts.items()}
    ordered = tuple(sorted(proportions, key=lambda code: -proportions[code]))
    dominant = ordered[0]

    return LanguageReport(
        languages=ordered,
        proportions=proportions,
        dominant=dominant,
        mixed=proportions[dominant] < DOMINANCE_THRESHOLD,
    )


def detect_one(sample: str) -> str | None:
    """One page's language, or None when the page is too thin to tell."""
    text = sample.strip()
    if len(text) < MIN_SAMPLE_CHARS:
        return None

    # The detector randomises by default, which would make two runs of the same
    # document disagree and break the reproducibility every experiment here
    # depends on.
    DetectorFactory.seed = 0

    try:
        return str(langdetect_detect(text))
    except LangDetectException:
        return None
