"""The benchmark's answers are not reachable from the system under test.

A benchmark whose labels leak into what it measures reports excellent numbers
and measures nothing, and the failure is invisible: the figures look better, not
broken. So this is checked by walking the source tree rather than by trusting a
convention.

Three separations, each enforced separately.

Nothing outside ``eval/`` reads ``eval/gold/``. The pipeline, the prompts, the
rubrics and the interface all have no path to the answers.

Gold labels never reach a prompt. Not the expected band, not the expected
criterion states, not the notes a labeller wrote.

Calibration seeds and benchmark cases are disjoint. A card built from a
benchmark case would let a later run of that same case find its own answer in
the anchors, which is the leak that would be hardest to notice because it
produces a plausible improvement.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]

#: Everything the system actually runs. ``eval/`` is deliberately absent: it is
#: allowed to read its own gold directory, and that is the whole point of the
#: directory being separate.
SYSTEM_DIRS = ("domain", "application", "infrastructure", "pipeline", "app", "scripts")

#: How the gold directory is referred to, in any form somebody might write it.
GOLD_REFERENCES = ("eval/gold", "eval\\\\gold", "manual_baseline")


def system_files() -> list[Path]:
    found: list[Path] = []
    for directory in SYSTEM_DIRS:
        root = REPO_ROOT / directory
        if root.is_dir():
            found += [path for path in root.rglob("*.py") if "__pycache__" not in path.parts]
    return sorted(found)


# --- the directory ------------------------------------------------------------------


def test_there_is_source_to_walk() -> None:
    """A test that silently found no files would pass forever."""
    assert len(system_files()) > 40


def docstrings_of(tree: ast.AST) -> set[int]:
    """Line numbers of every docstring.

    Excluded from the search, because a module explaining *why* it never reads
    the gold directory is doing the opposite of leaking. The first version of
    this test failed on exactly that: a contract whose docstring says "gold
    labels live in eval/gold/, a directory the pipeline never reads".
    """
    lines: set[int] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Module | ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef):
            continue
        body = getattr(node, "body", [])
        if (
            body
            and isinstance(body[0], ast.Expr)
            and isinstance(body[0].value, ast.Constant)
            and isinstance(body[0].value.value, str)
        ):
            first = body[0].value
            lines.update(range(first.lineno, (first.end_lineno or first.lineno) + 1))
    return lines


@pytest.mark.parametrize("path", system_files(), ids=lambda path: path.name)
def test_no_system_module_reads_the_gold_directory(path: Path) -> None:
    """A path to the answers, as code rather than as prose.

    Every string literal that is not a docstring, because that is what a module
    can actually open. A comment about the gold directory is documentation; a
    string containing its path is a read waiting to happen.
    """
    source = path.read_text(encoding="utf-8")
    tree = ast.parse(source)
    skip = docstrings_of(tree)

    for node in ast.walk(tree):
        if not (isinstance(node, ast.Constant) and isinstance(node.value, str)):
            continue
        if node.lineno in skip:
            continue

        for reference in GOLD_REFERENCES:
            assert reference not in node.value, (
                f"{path.relative_to(REPO_ROOT)}:{node.lineno} contains "
                f"{reference!r}. The benchmark's answers must not be reachable "
                "from the system being measured."
            )


@pytest.mark.parametrize("path", system_files(), ids=lambda path: path.name)
def test_no_system_module_imports_the_evaluation(path: Path) -> None:
    """The dependency runs one way. The evaluation may reach into the system;
    the system may not reach into the evaluation."""
    tree = ast.parse(path.read_text(encoding="utf-8"))

    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            assert not node.module.startswith("eval"), (
                f"{path.relative_to(REPO_ROOT)} imports {node.module}"
            )
        elif isinstance(node, ast.Import):
            for alias in node.names:
                assert not alias.name.startswith("eval."), (
                    f"{path.relative_to(REPO_ROOT)} imports {alias.name}"
                )


def test_the_gold_directory_is_not_committed() -> None:
    """Its contents would be real people's judgements about real applications.
    A committed CSV of twelve invented ones labelled "manual baseline" would be
    fabricating the number a reader has least ability to check."""
    gold = REPO_ROOT / "eval" / "gold"

    committed = [
        path.name
        for path in gold.glob("*")
        if path.is_file() and path.name not in (".gitignore", "README.md")
    ]

    assert committed == [], f"gold data is committed: {committed}"


def test_the_gold_directory_explains_itself() -> None:
    """Somebody arriving at an empty directory needs to know why it is empty and
    what to put in it."""
    readme = (REPO_ROOT / "eval" / "gold" / "README.md").read_text(encoding="utf-8")

    assert "manual_baseline.csv" in readme
    assert "not measured" in readme


# --- labels never reach a prompt ---------------------------------------------------------


def test_no_prompt_mentions_an_expected_answer() -> None:
    """A prompt carrying the label would make the benchmark a memory test."""
    for path in (REPO_ROOT / "prompts").rglob("*.md"):
        source = path.read_text(encoding="utf-8").lower()

        assert "expected_band" not in source
        assert "gold" not in source
        assert "must_be_insufficient" not in source


def test_no_rubric_contains_a_case_id() -> None:
    """A rubric example lifted from a benchmark case would teach the system the
    answer through the back door."""
    from eval.runner import load_cases

    ids = {case.case_id for case in load_cases()}

    for path in (REPO_ROOT / "rubrics").glob("*.yaml"):
        source = path.read_text(encoding="utf-8")
        for case_id in ids:
            assert case_id not in source


def test_the_arm_runs_the_real_use_case() -> None:
    """A harness with its own pipeline measures the harness, and it diverges
    exactly when somebody changes the real one."""
    from eval.arms import arm_c

    assert arm_c.calls_the_real_use_case()


# --- calibration and the benchmark are disjoint ---------------------------------------------


def test_no_calibration_seed_shares_an_id_with_a_case() -> None:
    """A card built from a benchmark case would let a later run of that case
    find its own answer among the anchors — a leak that produces a plausible
    improvement and is therefore the hardest kind to notice."""
    from eval.runner import load_cases

    case_ids = {case.case_id for case in load_cases()}
    seeds = REPO_ROOT / "data" / "calibration-seeds"

    if not seeds.is_dir():
        return

    seed_ids = {path.stem for path in seeds.glob("*")}

    assert case_ids.isdisjoint(seed_ids), (
        f"calibration seeds share ids with benchmark cases: {case_ids & seed_ids}"
    )


def test_a_benchmark_run_writes_no_calibration_card() -> None:
    """Cards come from approvals, and nothing in the benchmark approves
    anything. Stated as a test because the alternative — a harness that
    accidentally seeded the index — would improve every subsequent run of the
    same benchmark."""
    import inspect

    from eval.arms import arm_c

    source = inspect.getsource(arm_c)

    assert "submit_review" not in source
    assert "card_from" not in source
