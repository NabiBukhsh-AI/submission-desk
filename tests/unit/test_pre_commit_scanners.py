"""The two scanners catch what they exist to catch, and nothing else.

Each test plants one violation in a temporary tree and asserts the scanner names
it. The negative tests matter as much: a scanner that flags every content hash
and every usage record is a scanner somebody disables, and then it catches
nothing at all.

check_pii: invented-identifiers. Every address and number below is a planted
sample, chosen to be the kind of thing the scanner must report.
check_secrets: planted-shapes. The key-shaped strings are constructed inside
the tests and are not credentials for anything.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from scripts import check_pii, check_secrets

# --- check_pii: location ---------------------------------------------------------------


def test_a_file_under_data_outside_samples_is_refused() -> None:
    findings = check_pii.check_location("data/inbox/candidate-7/cv.pdf")

    assert len(findings) == 1
    assert findings[0].rule == "location"
    assert "data/samples/" in findings[0].detail


@pytest.mark.parametrize(
    "relative",
    ["data/samples/synthetic/001.txt", "data/samples/adversarial/white_text.pdf"],
)
def test_a_sample_is_committable(relative: str) -> None:
    assert check_pii.check_location(relative) == []


def test_the_location_rule_has_no_content_exemption(tmp_path: Path) -> None:
    """A real document under data/ is refused even when its text is clean and
    even when it declares itself invented. The boundary is the rule."""
    path = tmp_path / "data" / "inbox" / "cv.txt"
    path.parent.mkdir(parents=True)
    path.write_text(f"{check_pii.MARKER}\nNothing to see.", encoding="utf-8")

    findings = check_pii.scan(["data/inbox/cv.txt"], root=tmp_path)

    assert [finding.rule for finding in findings] == ["location"]


def test_windows_separators_are_normalised() -> None:
    assert check_pii.check_location("data\\inbox\\cv.pdf")


# --- check_pii: content --------------------------------------------------------------------


def _plant(tmp_path: Path, relative: str, text: str) -> list[check_pii.Finding]:
    path = tmp_path / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return check_pii.scan([relative], root=tmp_path)


def test_a_real_looking_email_is_caught(tmp_path: Path) -> None:
    findings = _plant(tmp_path, "eval/cases/dev/new.yaml", "contact: r.hale@quantex-labs.io")

    assert len(findings) == 1
    assert findings[0].rule == "email"
    assert findings[0].line == 1


def test_a_reserved_domain_is_not(tmp_path: Path) -> None:
    """RFC 2606 exists so that documentation never has to borrow a real domain.
    Using it is the fix the scanner recommends, so it has to pass."""
    text = "\n".join(
        [
            "a@example.com",
            "b@example.org",
            "c@mail.example.net",
            "d@anything.test",
            "e@nowhere.invalid",
        ]
    )

    assert _plant(tmp_path, "docs/GUIDE.md", text) == []


@pytest.mark.parametrize(
    "number",
    ["+91 98765 43210", "+1 212 736 5000", "(212) 736-5000", "0161 496 0000"],
)
def test_a_real_looking_phone_number_is_caught(tmp_path: Path, number: str) -> None:
    findings = _plant(tmp_path, "docs/notes.md", f"call {number} after six")

    assert [finding.rule for finding in findings] == ["phone"]


@pytest.mark.parametrize(
    "number",
    ["+44 20 7946 0958", "020 7946 0123", "07700 900123", "+44 7700 900456", "555-0142"],
)
def test_a_reserved_range_is_not(tmp_path: Path, number: str) -> None:
    assert _plant(tmp_path, "docs/notes.md", f"call {number} after six") == []


@pytest.mark.parametrize(
    "line",
    [
        "2021-2024 at Acme Payments",
        "p95 latency 210ms, p99 900ms",
        "run_id 01a08ced-108f-7b98-8d1a-453f4a01cfe6",
        "version 3.11.9, pinned",
        "content hash 74c9f1b2e0",
    ],
)
def test_ordinary_numbers_are_left_alone(tmp_path: Path, line: str) -> None:
    """The false positives that would get the scanner disabled."""
    assert _plant(tmp_path, "notes.md", line) == []


def test_binary_files_are_not_read_for_content(tmp_path: Path) -> None:
    """A PDF is bytes. Grepping it for an at-sign finds font tables."""
    findings = _plant(tmp_path, "docs/cv.pdf", "%PDF-1.4 someone@real-company.com")

    assert findings == []


def test_the_sample_tree_is_exempt_from_content_scanning(tmp_path: Path) -> None:
    """Every file there comes out of a generator in this repository, and the
    generator invents addresses because a CV without one is not a CV."""
    findings = _plant(tmp_path, "data/samples/synthetic/007.txt", "mail: x@real-company.com")

    assert findings == []


def test_a_declared_file_is_skipped_for_content(tmp_path: Path) -> None:
    text = f'"""Fixtures.\n\n{check_pii.MARKER}: every value invented.\n"""\nX = "a@real.co"'

    assert _plant(tmp_path, "tests/test_x.py", text) == []


def test_the_declaration_must_be_in_the_header(tmp_path: Path) -> None:
    """A marker buried at line 300 is a marker nobody reviewing the file sees."""
    text = "\n".join(["# fixtures"] * 60 + [check_pii.MARKER, 'X = "a@real.co"'])

    assert _plant(tmp_path, "tests/test_x.py", text) != []


def test_findings_name_the_file_and_line(tmp_path: Path) -> None:
    findings = _plant(tmp_path, "docs/x.md", "line one\nline two r@real-co.io")

    assert findings[0].render().startswith("docs/x.md:2")


# --- check_secrets: the environment file ------------------------------------------------------


@pytest.mark.parametrize("name", [".env", ".env.local", ".env.pilot", "config/.env"])
def test_an_environment_file_is_refused_by_name(name: str) -> None:
    findings = check_secrets.check_name(name)

    assert len(findings) == 1
    assert "never committed" in findings[0].detail


def test_the_example_file_is_allowed_by_name() -> None:
    assert check_secrets.check_name(".env.example") == []


def test_an_environment_file_is_refused_even_when_empty(tmp_path: Path) -> None:
    (tmp_path / ".env").write_text("", encoding="utf-8")

    assert check_secrets.scan([".env"], root=tmp_path)


def test_a_filled_in_example_file_is_caught(tmp_path: Path) -> None:
    """The example documents names. A value in it is a value in history."""
    path = tmp_path / ".env.example"
    path.write_text("MODEL_API_KEY=sk-live-Qm3vX9pL2rT8wY4nB7kD1hJ6fS0aZ5cE\n", encoding="utf-8")

    findings = check_secrets.scan([".env.example"], root=tmp_path)

    assert findings
    assert all(finding.path == ".env.example" for finding in findings)


def test_an_empty_example_file_is_clean(tmp_path: Path) -> None:
    path = tmp_path / ".env.example"
    path.write_text(
        "MODEL_API_KEY=          # never commit a value\nDEMO_MODE=\n", encoding="utf-8"
    )

    assert check_secrets.scan([".env.example"], root=tmp_path) == []


# --- check_secrets: key shapes ----------------------------------------------------------------


def _plant_secret(tmp_path: Path, relative: str, text: str) -> list[check_secrets.Finding]:
    path = tmp_path / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return check_secrets.scan([relative], root=tmp_path)


@pytest.mark.parametrize(
    ("what", "text"),
    [
        ("provider API key", "key = 'sk-proj-Qm3vX9pL2rT8wY4nB7kD1hJ6fS0aZ5cEgH2jK4mN6pQ8'"),
        ("Slack token", "SLACK = 'xoxb-1234567890-ABCDEFGHIJKLMNOP'"),
        ("GitHub token", "gh = 'ghp_Qm3vX9pL2rT8wY4nB7kD1hJ6fS0aZ5cEgH2j'"),
        ("AWS access key", "aws = 'AKIAIOSFODNN7EXAMPLE'"),
        ("Google API key", "g = 'AIzaSyQm3vX9pL2rT8wY4nB7kD1hJ6fS0aZ5cEg'"),
        ("private key block", "-----BEGIN RSA PRIVATE KEY-----"),
        ("service account", '{"type": "service_account", "private_key": "-----BEGIN PRIVATE'),
    ],
)
def test_a_known_key_shape_is_caught(tmp_path: Path, what: str, text: str) -> None:
    findings = _plant_secret(tmp_path, "config/creds.py", text)

    assert len(findings) == 1, what


def test_a_credential_assignment_is_caught(tmp_path: Path) -> None:
    findings = _plant_secret(
        tmp_path, "settings.py", 'API_KEY = "Qm3vX9pL2rT8wY4nB7kD1hJ6fS0aZ5cEgH2jK4mN"'
    )

    assert len(findings) == 1
    assert "API_KEY" in findings[0].detail


def test_a_yaml_credential_is_caught(tmp_path: Path) -> None:
    findings = _plant_secret(tmp_path, "config/x.yaml", "sheets_token: Qm3vX9pL2rT8wY4nB7kD1hJ6")

    assert len(findings) == 1


@pytest.mark.parametrize(
    "line",
    [
        "input_tokens=result.usage.input_tokens,",
        "token_ceiling=settings.token_ceiling_per_run,",
        "MODEL_API_KEY=                    # never commit a value",
        'api_key = "your-api-key-here"',
        'api_key = "${MODEL_API_KEY}"',
        "prompt_hash = 'acad6d434fbb3e1c9d2a'",
        "run_id = '01a08ced-108f-7b98-8d1a-453f4a01cfe6'",
        "password_min_length = 12",
    ],
)
def test_the_things_that_are_not_credentials(tmp_path: Path, line: str) -> None:
    """Counts, attribute chains, placeholders, hashes and ids. Each of these
    appearing as a finding is a reason somebody would disable the check."""
    assert _plant_secret(tmp_path, "module.py", line) == []


def test_a_declared_file_is_skipped(tmp_path: Path) -> None:
    header = f'"""{check_secrets.MARKER}: the strings below are constructed."""'
    text = header + '\nk = "xoxb-1234567890-ABCDEFGHIJKLMNOP"'

    assert _plant_secret(tmp_path, "tests/test_x.py", text) == []


def test_secret_findings_name_the_file_and_line(tmp_path: Path) -> None:
    findings = _plant_secret(tmp_path, "a/b.py", "x = 1\ny = 'AKIAIOSFODNN7EXAMPLE'")

    assert findings[0].render().startswith("a/b.py:2")


# --- entropy ---------------------------------------------------------------------------


def test_entropy_separates_random_from_repeated() -> None:
    assert check_secrets.entropy_bits("aaaaaaaaaaaaaaaaaaaa") == 0.0
    assert (
        check_secrets.entropy_bits("Qm3vX9pL2rT8wY4nB7kD1hJ6fS0aZ5cE")
        > check_secrets.MIN_ENTROPY_BITS
    )


def test_short_values_are_never_credentials() -> None:
    assert check_secrets.looks_like_credential("Qm3vX9pL2rT8") is False


# --- the tree ------------------------------------------------------------------------------------


def test_the_repository_itself_is_clean() -> None:
    """The scanners run in CI over every tracked file. If this fails, so does
    the build, and the fix is in the file named, not here."""
    tracked = check_pii.tracked()

    assert tracked, "git ls-files returned nothing; is this a checkout?"
    assert check_pii.scan(tracked) == []
    assert check_secrets.scan(tracked) == []
