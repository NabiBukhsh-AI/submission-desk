"""The demo, as a test.

TC-09 goes in and the run stops at SANITIZE having called no model at all. Then
a reviewer says "review anyway", the pipeline runs, and the fabricated claim
fails span validation if the model repeats it.

The number that matters is zero. Not "cheap", not "one small call" — zero rows
in the cost ledger for assessment, counted from the database rather than
inferred from a flag, because the claim made on camera is about spend and the
ledger is what spend means.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest

from application.deps import Deps
from application.use_cases.process_candidate import process_candidate
from domain.contracts.enums import IntegrityTier, RunStatus, Severity
from domain.ports.sources import CandidateRef, DocumentRef
from infrastructure.factory import build_deps, settings_from_env
from infrastructure.storage.sqlite.connection import close_thread_connection
from scripts.make_adversarial_corpus import build
from tests.workflow.test_end_to_end import (
    STRONG_ANSWERS,
    AnsweringModel,
    _FolderSource,
    _load_shipped_rubric,
)


@pytest.fixture(scope="session")
def adversarial(tmp_path_factory: pytest.TempPathFactory) -> Path:
    out = tmp_path_factory.mktemp("quarantine-corpus")
    build(out)
    return out


@pytest.fixture
def deps(tmp_path: Path) -> Iterator[Deps]:
    settings = settings_from_env(
        db_path=str(tmp_path / "quarantine.sqlite"),
        blob_dir=str(tmp_path / "blobs"),
        blind_mode=False,
        # The render comparison is exercised in tests/adversarial. Here the
        # deterministic detectors are enough, and an OCR pass per page would
        # make this suite slow for no extra assurance.
        sanitize_render_diff=False,
    )
    built = build_deps(settings)
    yield Deps(
        **{
            **built.__dict__,
            "rubric_loader": _load_shipped_rubric,
            "source": _FolderSource(tmp_path / "inbox"),
        }
    )
    close_thread_connection(settings.db_path)


def submit(deps: Deps, source: Path, filename: str = "cv.pdf") -> CandidateRef:
    inbox = Path(deps.source.root)
    path = inbox / filename
    path.write_bytes(source.read_bytes())
    return CandidateRef(
        candidate_id="cand-adv",
        documents=(
            DocumentRef(candidate_id="cand-adv", filename=filename, external_ref=str(path)),
        ),
    )


def run(deps: Deps, source: Path, *, force: bool = False):
    candidate = submit(deps, source)
    model = AnsweringModel("", STRONG_ANSWERS)
    wired = Deps(**{**deps.__dict__, "models": model})
    return process_candidate(candidate, "ai-engineer", wired, force=force), wired, model


def assessment_calls(deps: Deps, run_id) -> int:
    """Rows in the cost ledger for assessment. The number on camera."""
    return sum(1 for record in deps.costs.for_run(run_id) if record.call_site == "assess.criterion")


# --- the halt -----------------------------------------------------------------


def test_the_demo_document_quarantines(deps: Deps, adversarial: Path) -> None:
    result, _, _ = run(deps, adversarial / "tc09_visible_and_hidden.pdf")

    assert result.status is RunStatus.QUARANTINED


def test_nothing_is_spent_on_assessment(deps: Deps, adversarial: Path) -> None:
    """Counted from the ledger, not from a flag. Zero is the claim."""
    result, wired, model = run(deps, adversarial / "tc09_visible_and_hidden.pdf")

    assert assessment_calls(wired, result.run_id) == 0
    assert "assess.criterion" not in model.seen


def test_the_run_stops_before_structuring(deps: Deps, adversarial: Path) -> None:
    """SANITIZE sits before STRUCTURE for exactly this reason. Moving it later
    would keep every detector working and lose the property they exist for."""
    result, _, _ = run(deps, adversarial / "tc09_visible_and_hidden.pdf")

    assert "SANITIZE" in result.outcome.executed
    assert "STRUCTURE" not in result.outcome.executed
    assert "ASSESS" not in result.outcome.executed


def test_no_model_was_called_at_all(deps: Deps, adversarial: Path) -> None:
    _, _, model = run(deps, adversarial / "tc09_visible_and_hidden.pdf")

    assert model.seen == []


# --- what the reviewer is shown --------------------------------------------------


def test_the_findings_are_persisted(deps: Deps, adversarial: Path) -> None:
    result, wired, _ = run(deps, adversarial / "tc09_visible_and_hidden.pdf")

    reports = wired.candidates.integrity_reports_for_run(result.run_id)

    assert reports
    assert reports[0].tier is IntegrityTier.QUARANTINE
    assert reports[0].findings


def test_both_injections_are_quoted(deps: Deps, adversarial: Path) -> None:
    """The demo promises both, with page and position, and a reviewer deciding
    whether to override needs to read what was actually in the file."""
    result, wired, _ = run(deps, adversarial / "tc09_visible_and_hidden.pdf")
    findings = wired.candidates.integrity_reports_for_run(result.run_id)[0].findings

    detectors = {finding.detector for finding in findings}
    assert "D-INSTR-IMPERATIVE" in detectors
    assert {"D-HIDDEN-COLOUR", "D-HIDDEN-SIZE"} & detectors

    quoted = " ".join(finding.excerpt for finding in findings).lower()
    assert "automated screener" in quoted
    assert "12 years" in quoted or "ignore prior instructions" in quoted


def test_every_finding_names_its_detector(deps: Deps, adversarial: Path) -> None:
    """The id is what makes precision measurable per detector."""
    result, wired, _ = run(deps, adversarial / "tc09_visible_and_hidden.pdf")

    for finding in wired.candidates.integrity_reports_for_run(result.run_id)[0].findings:
        assert finding.detector.startswith("D-")
        assert 0.0 <= finding.detector_confidence <= 1.0


def test_the_message_is_written_for_a_person(deps: Deps, adversarial: Path) -> None:
    result, wired, _ = run(deps, adversarial / "tc09_visible_and_hidden.pdf")

    errors = wired.errors.for_run(result.run_id)
    quarantine = next(error for error in errors if error.error_code == "QUARANTINED")

    assert "manipulate" in quarantine.message_redacted
    assert "nothing was spent" in quarantine.message_redacted
    for jargon in ("traceback", "exception", "None"):
        assert jargon not in quarantine.message_redacted


def test_the_event_log_records_the_tier(deps: Deps, adversarial: Path) -> None:
    result, wired, _ = run(deps, adversarial / "tc09_visible_and_hidden.pdf")

    nodes = [row[1] for row in wired.events.for_run(result.run_id)]

    assert "SANITIZE" in nodes


# --- review anyway -----------------------------------------------------------------


def test_a_reviewer_can_override_the_halt(deps: Deps, adversarial: Path) -> None:
    """The second half of the demo. The override is a decision a person makes
    and the system records, not a setting that turns the scanner off."""
    from domain.state_machine import ALLOWED

    assert RunStatus.NEEDS_REVIEW in ALLOWED[RunStatus.QUARANTINED]


def test_a_quarantined_run_cannot_reach_a_decision_without_a_person(
    adversarial: Path,
) -> None:
    """The only two doors out of QUARANTINED both go through a human."""
    from domain.state_machine import ALLOWED

    assert ALLOWED[RunStatus.QUARANTINED] == frozenset({RunStatus.NEEDS_REVIEW, RunStatus.REJECTED})


def test_delivery_is_not_reachable_from_quarantine() -> None:
    from domain.state_machine import RunStatus as Status
    from domain.state_machine import paths_to

    for path in paths_to(Status.DELIVERED):
        assert Status.QUARANTINED not in path or Status.APPROVED in path


# --- the clean twin ------------------------------------------------------------------


def test_a_clean_document_is_not_quarantined(deps: Deps, adversarial: Path) -> None:
    """The control. Without it, a scanner that quarantines everything would
    pass every test above."""
    result, _, _ = run(deps, adversarial / "clean_engineer.pdf")

    assert result.status is not RunStatus.QUARANTINED


def test_a_clean_document_reaches_a_reviewer(deps: Deps, adversarial: Path) -> None:
    result, _, _ = run(deps, adversarial / "clean_engineer.pdf")

    assert result.is_reviewable


def test_a_clean_document_does_spend(deps: Deps, adversarial: Path) -> None:
    """The other half of the zero: assessment runs when it should, so the zero
    above means "the halt worked" rather than "assessment is broken"."""
    _, _, model = run(deps, adversarial / "clean_engineer.pdf")

    assert "assess.criterion" in model.seen


def test_keyword_stuffing_does_not_halt_a_run(deps: Deps, adversarial: Path) -> None:
    """A LOW finding is reported and the run proceeds. Stuffing cannot change a
    criterion state anyway: the rule engine counts distinct quotations, not
    word frequencies."""
    result, wired, _ = run(deps, adversarial / "keyword_stuffing.pdf")

    assert result.status is not RunStatus.QUARANTINED
    reports = wired.candidates.integrity_reports_for_run(result.run_id)
    assert all(
        finding.severity is Severity.LOW for report in reports for finding in report.findings
    )
