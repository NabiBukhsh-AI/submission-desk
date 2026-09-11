"""Refuse a commit that would publish somebody's contact details.

This repository is a portfolio artefact. Candidate documents are among the most
sensitive things a person hands over, and the failure is unrecoverable: once an
address is in a public history it stays there through every clone, fork and
mirror, and a later deletion removes the file, not the fact.

Two rules, because there are two ways it happens.

**Location.** Nothing under ``data/`` may be committed except ``data/samples/``.
That is the strong rule. It has no exemptions and no false positives: real
candidate documents live under ``data/`` at run time, and a scanner that tries to
tell a real CV from a synthetic one by reading it will eventually be wrong. The
directory boundary is decidable; the content is not.

**Content.** An address or a number pasted into a fixture, a case file, or a
document. Here the check is a pattern, so it needs a way to say "this one is
invented" that is not a list of exceptions somebody appends to. That way is
reserved values: RFC 2606 domains and the fictional telephone ranges regulators
publish for exactly this purpose. A synthetic identifier costs nothing to write
as ana@example.com, and one that cannot be written that way gets reported.

``data/samples/`` is exempt from the content rule, not by permission but by
definition: it is the generated corpus, every file in it comes out of a script in
this repository, and the location rule guarantees nothing else under ``data/``
is committed at all.

    python scripts/check_pii.py              # staged files, or the tree
    python scripts/check_pii.py path ...     # named files
    python scripts/check_pii.py --all        # every tracked file
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]

#: Committable data. Everything else under data/ is a run artefact or a real
#: document, and neither belongs in history.
SAMPLE_PREFIX = "data/samples/"
DATA_PREFIX = "data/"

#: Read as text. Anything else is checked for location and left alone: a PDF is
#: bytes, and grepping it for an at-sign finds font tables.
TEXT_SUFFIXES = frozenset(
    {
        ".py",
        ".md",
        ".txt",
        ".yaml",
        ".yml",
        ".json",
        ".toml",
        ".cfg",
        ".ini",
        ".csv",
        ".jsonl",
        ".html",
        ".css",
        ".js",
        ".sh",
        ".example",
        "",
    }
)

#: Never walked. Caches and the virtual environment are full of other people's
#: example addresses, and none of them is ours to fix.
SKIP_DIRS = frozenset(
    {
        ".git",
        ".venv",
        "venv",
        "__pycache__",
        ".mypy_cache",
        ".ruff_cache",
        ".pytest_cache",
        ".hypothesis",
        "node_modules",
        "htmlcov",
        "build",
        "dist",
    }
)

EMAIL = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")

#: The same shapes the log redactor recognises. Kept deliberately narrow: a
#: pattern loose enough to catch every telephone number also catches every
#: version string, and a check that cries wolf is a check somebody turns off.
PHONES = (
    re.compile(r"\+\d{1,3}[\s.\-]?\(?\d{1,4}\)?(?:[\s.\-]?\d{2,4}){2,4}"),
    re.compile(r"\(\d{3}\)\s?\d{3}[\s.\-]?\d{4}"),
    re.compile(r"\b0\d{2,4}[\s.\-]\d{3,4}[\s.\-]?\d{3,4}\b"),
)

#: RFC 2606 and RFC 6761. Reserved by standard so that documentation never has
#: to borrow somebody's real domain.
RESERVED_DOMAINS = (".example.com", ".example.net", ".example.org")
RESERVED_EXACT = frozenset({"example.com", "example.net", "example.org"})
RESERVED_TLDS = (".example", ".invalid", ".test", ".localhost")

#: Ranges the regulators set aside for fiction. Ofcom's drama numbers and the
#: North American 555-01xx block have published guarantees; 555 is also not an
#: assigned area code, so a number that starts with it belongs to nobody.
RESERVED_NUMBERS = (
    re.compile(r"(?:\+?44[\s.\-]?)?\(?0?20\)?[\s.\-]?7946[\s.\-]?0\d{3}"),
    re.compile(r"(?:\+?44[\s.\-]?)?\(?0?7700\)?[\s.\-]?900\d{3}"),
    re.compile(r"555[\s.\-]?01\d{2}"),
    re.compile(r"(?:\+?1[\s.\-]?)?\(?555\)?[\s.\-]?\d{3}[\s.\-]?\d{4}"),
)

#: A file that must exercise a format with no reserved range says so in its
#: header. Declared rather than allowed: every marked file is printed on every
#: run, so the exemption stays visible instead of becoming furniture.
MARKER = "check_pii: invented-identifiers"
MARKER_LINES = 40


@dataclass(frozen=True)
class Finding:
    path: str
    line: int
    rule: str
    detail: str

    def render(self) -> str:
        where = f"{self.path}:{self.line}" if self.line else self.path
        return f"{where}: {self.detail}"


def _reserved_email(address: str) -> bool:
    domain = address.rsplit("@", 1)[-1].lower().rstrip(".")
    if domain in RESERVED_EXACT:
        return True
    return domain.endswith(RESERVED_DOMAINS) or domain.endswith(RESERVED_TLDS)


def _reserved_number(text: str) -> bool:
    return any(pattern.search(text) for pattern in RESERVED_NUMBERS)


def check_location(relative: str) -> list[Finding]:
    """The rule with no exemptions."""
    normalised = relative.replace("\\", "/")
    if not normalised.startswith(DATA_PREFIX) or normalised.startswith(SAMPLE_PREFIX):
        return []

    return [
        Finding(
            normalised,
            0,
            "location",
            "is under data/ but not under data/samples/. Run artefacts and real "
            "candidate documents are never committed. Move it, or add it to "
            ".gitignore.",
        )
    ]


def declared(path: Path) -> bool:
    """Whether a file has said, in its own header, that its identifiers are invented."""
    try:
        head = path.read_text(encoding="utf-8").splitlines()[:MARKER_LINES]
    except (OSError, UnicodeDecodeError):
        return False
    return MARKER in "\n".join(head)


def check_content(path: Path, relative: str) -> list[Finding]:
    """Addresses and numbers that are not reserved for fiction."""
    normalised = relative.replace("\\", "/")
    if normalised.startswith(SAMPLE_PREFIX) or path.suffix.lower() not in TEXT_SUFFIXES:
        return []

    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return []

    if declared(path):
        return []

    findings: list[Finding] = []
    for number, line in enumerate(text.splitlines(), start=1):
        for address in EMAIL.findall(line):
            if not _reserved_email(address):
                findings.append(
                    Finding(
                        normalised,
                        number,
                        "email",
                        f"contains the address {address!r}. Use a reserved domain "
                        "(example.com, .test, .invalid) for an invented address.",
                    )
                )

        if _reserved_number(line):
            continue

        for pattern in PHONES:
            match = pattern.search(line)
            if match:
                findings.append(
                    Finding(
                        normalised,
                        number,
                        "phone",
                        "contains what looks like a telephone number "
                        f"({match.group(0).strip()!r}). Use a reserved range, "
                        "such as +44 20 7946 0000, 07700 900000, or 555-0100.",
                    )
                )
                break

    return findings


# --- what to look at ---------------------------------------------------------


def _git(*arguments: str) -> list[str] | None:
    result = subprocess.run(
        ["git", *arguments], cwd=REPO_ROOT, capture_output=True, text=True, check=False
    )
    if result.returncode != 0:
        return None
    return [line for line in result.stdout.splitlines() if line.strip()]


def staged() -> list[str]:
    return _git("diff", "--cached", "--name-only", "--diff-filter=ACM") or []


def tracked() -> list[str]:
    """Every file git knows about, or the tree when git is not available.

    CI sometimes runs from an export rather than a clone, and a check that
    silently passed there would be a check that only ran on developer machines.
    """
    # Tracked and untracked-but-not-ignored, so a new file is scanned before
    # its first commit rather than after.
    return _git("ls-files", "--cached", "--others", "--exclude-standard") or _walk()


def _walk() -> list[str]:
    found = []
    for path in REPO_ROOT.rglob("*"):
        if not path.is_file() or any(part in SKIP_DIRS for part in path.parts):
            continue
        found.append(path.relative_to(REPO_ROOT).as_posix())
    return sorted(found)


def scan(relatives: list[str], *, root: Path = REPO_ROOT) -> list[Finding]:
    findings: list[Finding] = []
    for relative in relatives:
        path = root / relative
        findings += check_location(relative)
        if path.is_file():
            findings += check_content(path, relative)
    return findings


def _as_relative(argument: str) -> str:
    path = Path(argument)
    if not path.is_absolute():
        return path.as_posix()
    try:
        return path.resolve().relative_to(REPO_ROOT).as_posix()
    except ValueError:
        return path.as_posix()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Refuse candidate PII in a commit.")
    parser.add_argument("paths", nargs="*", help="files to check; default is the staged set")
    parser.add_argument("--all", action="store_true", help="every tracked file")
    args = parser.parse_args(argv)

    if args.paths:
        relatives = [_as_relative(argument) for argument in args.paths]
    elif args.all:
        relatives = tracked()
    else:
        relatives = staged() or tracked()

    findings = scan(relatives)

    marked = [
        relative
        for relative in relatives
        if (REPO_ROOT / relative).is_file() and declared(REPO_ROOT / relative)
    ]
    if marked:
        print(f"check-pii: {len(marked)} file(s) declare invented identifiers:")
        for relative in marked:
            print(f"  {relative}")

    if not findings:
        print(f"check-pii: clean over {len(relatives)} file(s).")
        return 0

    print(f"check-pii: {len(findings)} problem(s).", file=sys.stderr)
    for finding in findings:
        print(f"  {finding.render()}", file=sys.stderr)
    print(
        "\nNothing has been committed. Fix the files above, or, if an identifier is "
        "invented and no reserved value has the shape you need, add the line\n"
        f"    {MARKER}\n"
        "to the file's header together with the reason.",
        file=sys.stderr,
    )
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
