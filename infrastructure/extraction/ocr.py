"""Reading pages that have no text in them.

Tesseract is a system binary, not a Python package, so it may simply not be
installed. That case is reported rather than absorbed: a scan that produced
nothing because nobody installed the reader looks exactly like a blank page, and
confusing those two would tell a recruiter their candidate submitted an empty
document.

Finding it is its own problem. The usual Windows installer does not put
Tesseract on the PATH, so a recruiter who installed it correctly would still be
told it is missing. The engine therefore looks in the standard install
locations as well, and an explicit path can be given when it lives somewhere
else entirely.

OCR is triggered per page, never per document. A CV that is digital on page one
and a photograph on page three is normal, and re-reading the whole file through
OCR would throw away good text to fix a bad page.
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path

from domain.ports.extraction import OcrResult

#: Rendering resolution. 300 is the usual floor for reliable character shapes;
#: the retry goes higher for pages that came back uncertain.
DEFAULT_DPI = 300
RETRY_DPI = 400

#: Below this mean word confidence a page is read again at a higher resolution.
#: One retry, never a loop.
MIN_CONFIDENCE = 0.6

#: Where the common installers put it. Checked after the PATH and after the
#: environment override, so an explicit choice always wins.
KNOWN_LOCATIONS = (
    r"C:\Program Files\Tesseract-OCR\tesseract.exe",
    r"C:\Program Files (x86)\Tesseract-OCR\tesseract.exe",
    "/usr/bin/tesseract",
    "/usr/local/bin/tesseract",
    "/opt/homebrew/bin/tesseract",
)


def find_binary(name: str = "tesseract") -> str | None:
    """Locate the reader, or report that it is not there.

    Order matters: an explicit setting beats the PATH, and the PATH beats a
    guess. Someone who has pointed at a particular build wants that build.
    """
    override = os.environ.get("TESSERACT_BINARY", "").strip()
    if override:
        return override if Path(override).exists() else None

    found = shutil.which(name)
    if found:
        return found

    for candidate in KNOWN_LOCATIONS:
        if Path(candidate).exists():
            return candidate

    return None


class TesseractEngine:
    """Reads a rendered page through Tesseract, if it can be found."""

    def __init__(self, *, binary: str | None = None) -> None:
        self.binary = binary or find_binary()
        # Resolved once at construction. The answer cannot change during a run,
        # and probing the filesystem per page of a sixty-page document is waste.
        # A plain attribute rather than a property, because the port declares it
        # settable so a test can stand in a stub.
        self.available: bool = self.binary is not None and _bridge_available()

    def read(self, image_bytes: bytes, *, dpi: int = DEFAULT_DPI) -> OcrResult:
        """Read one rendered page.

        Returns a result with ``available=False`` rather than raising when the
        engine is missing, so the caller records a page it could not read
        instead of failing a whole document over a missing dependency.
        """
        if not self.available or self.binary is None:
            return OcrResult(
                text="",
                mean_confidence=0.0,
                available=False,
                reason=(
                    "No optical character reader was found, so scanned pages cannot be read. "
                    "Install Tesseract, or set TESSERACT_BINARY to its location."
                ),
            )

        import io

        import pytesseract
        from PIL import Image

        pytesseract.pytesseract.tesseract_cmd = self.binary

        image = Image.open(io.BytesIO(image_bytes))
        data = pytesseract.image_to_data(image, output_type=pytesseract.Output.DICT)

        words = [
            (word, int(score))
            for word, score in zip(data["text"], data["conf"], strict=False)
            if word.strip() and int(score) >= 0
        ]
        if not words:
            return OcrResult(text="", mean_confidence=0.0, reason="no characters were recognised")

        text = " ".join(word for word, _ in words)
        mean = sum(score for _, score in words) / len(words) / 100.0
        return OcrResult(text=text, mean_confidence=mean)


class UnavailableOcrEngine:
    """Stands in when OCR is deliberately switched off.

    Distinct from Tesseract being absent by accident: this one says so, and the
    reviewer sees that scanned pages were not attempted rather than that they
    were attempted and failed.
    """

    available = False

    def read(self, image_bytes: bytes, *, dpi: int = DEFAULT_DPI) -> OcrResult:
        return OcrResult(
            text="",
            mean_confidence=0.0,
            available=False,
            reason="Reading scanned pages is turned off for this run.",
        )


def _bridge_available() -> bool:
    try:
        import pytesseract  # noqa: F401
        from PIL import Image  # noqa: F401
    except ImportError:
        return False
    return True


def read_with_retry(engine: object, image_bytes: bytes) -> OcrResult:
    """Read a page, once more at higher resolution if the first pass was poor.

    One retry, never a loop. A page that is still uncertain at 400 DPI is a page
    the reviewer should look at themselves, and spending more attempts on it
    buys latency rather than accuracy.
    """
    first = engine.read(image_bytes, dpi=DEFAULT_DPI)  # type: ignore[attr-defined]
    if not first.available or first.mean_confidence >= MIN_CONFIDENCE:
        return first

    second = engine.read(image_bytes, dpi=RETRY_DPI)  # type: ignore[attr-defined]
    return second if second.mean_confidence > first.mean_confidence else first
