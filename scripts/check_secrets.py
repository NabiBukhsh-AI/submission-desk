"""Refuse a commit that carries a credential.

A key in a public history is a key somebody else has, and rotating it afterwards
closes the door on a room that has already been walked through. The check runs
before the commit exists, which is the only time it is cheap.

Three rules.

**The environment file.** ``.env`` and anything shaped like it (``.env.local``,
``.env.pilot``) is refused by name, whatever it contains. ``.env.example`` is
the one exception, because it is the documentation of the variable names and
carries no values by design — and that design is checked too: a value in the
example file that looks like a real one is reported.

**Known shapes.** Provider keys, bot tokens, cloud access keys and private-key
blocks have recognisable prefixes. Those are matched exactly, because a prefix
match has no false positives worth arguing about.

**Assignments.** A variable whose name says *key*, *token*, *secret* or
*password* assigned a long, high-entropy literal. The name is what makes this
usable: hashes, fingerprints and ids in this repository are long and random too,
and a check on entropy alone would flag every content hash in the test suite.
The assignment shape says the author thought of the value as a credential, and
that is the case worth stopping.

    python scripts/check_secrets.py              # staged files, or the tree
    python scripts/check_secrets.py path ...     # named files
    python scripts/check_secrets.py --all        # every tracked file
"""

from __future__ import annotations

import argparse
import math
import re
import subprocess
import sys
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]

#: Refused by name. The example file documents the variables and never a value.
ENV_FILE = re.compile(r"(^|/)\.env(\.[A-Za-z0-9_-]+)?$")
ENV_EXAMPLE = ".env.example"

#: Recognisable prefixes. Each is a documented format, not a guess.
KNOWN_SHAPES: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("a provider API key", re.compile(r"\bsk-(?:proj-|ant-)?[A-Za-z0-9_\-]{20,}")),
    ("a Slack token", re.compile(r"\bxox[abprs]-[A-Za-z0-9\-]{10,}")),
    ("a GitHub token", re.compile(r"\b(?:ghp|gho|ghu|ghs|ghr)_[A-Za-z0-9]{30,}")),
    ("an AWS access key id", re.compile(r"\bAKIA[0-9A-Z]{16}\b")),
    ("a Google API key", re.compile(r"\bAIza[0-9A-Za-z_\-]{35}")),
    ("a private key block", re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----")),
    ("a service-account private key", re.compile(r"\"private_key\"\s*:\s*\"-----BEGIN")),
)

#: A name that says what the value is. ``tokens`` is excluded on purpose: in
#: this codebase it is a count, and a scanner that flagged every usage record
#: would be switched off by lunchtime.
SECRET_NAME = re.compile(
    r"(?i)\b[A-Za-z_]*(?:api[_-]?key|secret|token(?!s\b)|password|passwd|credential)[A-Za-z_]*\b"
)
#: name = "value", name: value, name=value. The literal is captured.
ASSIGNMENT = re.compile(
    r"(?P<name>[A-Za-z_][A-Za-z0-9_\-]*)\s*[:=]\s*[\"']?(?P<value>[A-Za-z0-9+/=_\-\.]{20,})[\"']?"
)
#: ``settings.token_ceiling_per_run`` is an attribute chain, not a literal.
#: Code that reads a credential from somewhere is fine; code that contains one
#: is not, and the difference is whether the right-hand side is a value.
IDENTIFIER_CHAIN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)+$")

#: Below this a literal is a word, a path or an id. Above it, with a secret-ish
#: name beside it, it is a credential until somebody says otherwise.
MIN_ENTROPY_BITS = 3.5
MIN_LENGTH = 20

#: Values that are obviously not credentials, whatever they sit beside.
PLACEHOLDERS = re.compile(
    r"(?i)^(?:your[_-]?|my[_-]?|example[_-]?|replace[_-]?|changeme|xxx+|<[^>]+>|\$\{[^}]+\}|"
    r"none|null|unset|redacted|placeholder|not[_-]?configured)"
)

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
        ".js",
        ".jsx",
        ".ts",
        ".tsx",
        ".css",
        ".sh",
        ".example",
        "",
    }
)

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

#: A test that plants a key-shaped string to prove the scanner catches it has
#: to say so, or it would be the first thing the scanner catches.
MARKER = "check_secrets: planted-shapes"
MARKER_LINES = 40


@dataclass(frozen=True)
class Finding:
    path: str
    line: int
    detail: str

    def render(self) -> str:
        where = f"{self.path}:{self.line}" if self.line else self.path
        return f"{where}: {self.detail}"


def entropy_bits(value: str) -> float:
    """Shannon entropy per character. Random base64 sits near 5.5; English
    prose near 4.0; a repeated character at 0."""
    if not value:
        return 0.0
    counts = Counter(value)
    length = len(value)
    return -sum((count / length) * math.log2(count / length) for count in counts.values())


def looks_like_credential(value: str) -> bool:
    if len(value) < MIN_LENGTH or PLACEHOLDERS.match(value):
        return False
    if IDENTIFIER_CHAIN.match(value):
        return False
    # A hex digest has at most sixteen symbols; base64 or a real key has more.
    # Both can be secrets, so the threshold is on entropy rather than alphabet.
    return entropy_bits(value) >= MIN_ENTROPY_BITS


def check_name(relative: str) -> list[Finding]:
    normalised = relative.replace("\\", "/")
    if normalised.endswith(ENV_EXAMPLE):
        return []
    if ENV_FILE.search(normalised):
        return [
            Finding(
                normalised,
                0,
                "is an environment file. It is never committed, whatever it holds. "
                "The variable names belong in .env.example, with no values.",
            )
        ]
    return []


def _declared(lines: list[str]) -> bool:
    return MARKER in "\n".join(lines[:MARKER_LINES])


def check_content(path: Path, relative: str) -> list[Finding]:
    normalised = relative.replace("\\", "/")
    if path.suffix.lower() not in TEXT_SUFFIXES and path.name != ".env.example":
        return []

    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeDecodeError):
        return []

    if _declared(lines):
        return []

    findings: list[Finding] = []
    for number, line in enumerate(lines, start=1):
        for what, pattern in KNOWN_SHAPES:
            if pattern.search(line):
                findings.append(Finding(normalised, number, f"contains {what}."))
                break
        else:
            findings += _check_assignment(line, normalised, number)

    return findings


def _check_assignment(line: str, normalised: str, number: int) -> list[Finding]:
    for match in ASSIGNMENT.finditer(line):
        name, value = match.group("name"), match.group("value")
        if SECRET_NAME.search(name) and looks_like_credential(value):
            return [
                Finding(
                    normalised,
                    number,
                    f"assigns a credential-shaped value to {name!r}. Values belong in "
                    ".env, which is not committed; names belong in .env.example.",
                )
            ]
    return []


def check_example_file(path: Path) -> list[Finding]:
    """The example file may name variables and must not fill them in."""
    if not path.is_file():
        return []
    findings: list[Finding] = []
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        stripped = line.split("#", 1)[0].strip()
        if not stripped or "=" not in stripped:
            continue
        _, value = stripped.split("=", 1)
        if looks_like_credential(value.strip().strip("\"'")):
            findings.append(
                Finding(
                    ENV_EXAMPLE,
                    number,
                    "carries a value that looks real. The example file documents "
                    "names and defaults, never a credential.",
                )
            )
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
        findings += check_name(relative)
        if path.is_file():
            findings += check_content(path, relative)
        if relative.replace("\\", "/").endswith(ENV_EXAMPLE):
            findings += check_example_file(path)
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
    parser = argparse.ArgumentParser(description="Refuse credentials in a commit.")
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

    if not findings:
        print(f"check-secrets: clean over {len(relatives)} file(s).")
        return 0

    print(f"check-secrets: {len(findings)} problem(s).", file=sys.stderr)
    for finding in findings:
        print(f"  {finding.render()}", file=sys.stderr)
    print(
        "\nNothing has been committed. If a value above is a real credential, "
        "treat it as exposed and rotate it now, before fixing the file.",
        file=sys.stderr,
    )
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
