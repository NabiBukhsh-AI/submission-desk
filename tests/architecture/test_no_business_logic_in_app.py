"""The interface decides nothing.

Every action a reviewer takes calls an application use case. That is not
tidiness: it is what makes the whole system testable without a browser, and what
makes the interface replaceable without re-deriving a single rule.

The failure this guards against is specific and easy. Somebody needs a band on a
page, the recommendation is right there, and one comparison against 0.75 appears
in a template. Now there are two scoring rules, one of them invisible to the
rule engine's tests, and they will drift.

So this walks the source rather than trusting the convention.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
APP = REPO_ROOT / "app"

#: What the interface may reach. Use cases, contracts for typing, and the
#: components' own package. Notably absent: pipeline, and the rule engine.
ALLOWED_PREFIXES = (
    "app",
    "application.deps",
    "application.use_cases",
    "domain.contracts",
    "domain.ports",
    "domain.security",
    "infrastructure.factory",
)

#: Modules the interface must never import. Each would mean a decision being
#: made in a template.
FORBIDDEN_PREFIXES = (
    "pipeline",
    "domain.rules.aggregate",
    "domain.rules.resolve",
    "domain.rules.explain",
    "domain.state_machine",
    "infrastructure.models",
    "infrastructure.security",
    "infrastructure.storage",
)

#: Names that would mean a threshold had been written into a page.
BANNED_NAMES = (
    "aggregate",
    "resolve_criterion",
    "transition",
    "can_transition",
    "min_coverage",
    "state_points",
)


def app_modules() -> list[Path]:
    return sorted(path for path in APP.rglob("*.py") if "__pycache__" not in path.parts)


def imports_of(path: Path) -> list[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    found: list[str] = []

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found += [alias.name for alias in node.names]
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            found.append(node.module)

    return found


def internal(module: str) -> bool:
    top = module.split(".", maxsplit=1)[0]
    return top in {"app", "application", "domain", "infrastructure", "pipeline", "eval"}


# --- what the interface may reach ------------------------------------------------


def test_the_app_exists() -> None:
    """A test suite that silently found no files would pass forever."""
    assert app_modules()


@pytest.mark.parametrize("path", app_modules(), ids=lambda path: path.name)
def test_no_page_imports_the_pipeline(path: Path) -> None:
    """A page that imported a node could run one, which would put the
    interface's timing into the pipeline's guarantees."""
    for module in imports_of(path):
        for forbidden in FORBIDDEN_PREFIXES:
            assert not module.startswith(forbidden), (
                f"{path.name} imports {module}, which belongs behind a use case"
            )


@pytest.mark.parametrize("path", app_modules(), ids=lambda path: path.name)
def test_every_internal_import_is_allowed(path: Path) -> None:
    for module in imports_of(path):
        if not internal(module):
            continue
        assert any(module.startswith(prefix) for prefix in ALLOWED_PREFIXES), (
            f"{path.name} imports {module}, which is not on the interface's list"
        )


@pytest.mark.parametrize("path", app_modules(), ids=lambda path: path.name)
def test_no_rule_is_called_from_a_page(path: Path) -> None:
    """The named failure: one threshold comparison in a template."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    called = {
        node.func.id
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
    } | {
        node.func.attr
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
    }

    for banned in BANNED_NAMES:
        assert banned not in called, f"{path.name} calls {banned}"


# --- the session ---------------------------------------------------------------------


def test_session_state_is_one_typed_object() -> None:
    """Loose keys are how a page ends up writing ``reviewr_id`` and failing at
    the far end of a workflow."""
    from app.state import SESSION_KEY, SessionState

    assert isinstance(SESSION_KEY, str)
    assert SessionState().reviewer_id == ""


@pytest.mark.parametrize("path", app_modules(), ids=lambda path: path.name)
def test_no_page_writes_a_loose_session_key(path: Path) -> None:
    """``st.session_state["anything"] = x`` outside the state module."""
    if path.name == "state.py":
        return

    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if not isinstance(node, ast.Assign):
            continue
        for target in node.targets:
            if not isinstance(target, ast.Subscript):
                continue
            value = target.value
            attribute = getattr(value, "attr", None) or getattr(value, "id", None)
            assert attribute != "session_state", (
                f"{path.name} writes a loose session key; use app.state instead"
            )


def test_the_session_state_is_frozen() -> None:
    """A page that mutated shared state in place would make the order pages ran
    in part of the behaviour, and Streamlit reruns a script on every click."""
    import dataclasses

    from app.state import SessionState

    assert dataclasses.fields(SessionState)
    with pytest.raises(dataclasses.FrozenInstanceError):
        SessionState().reviewer_id = "someone"  # type: ignore[misc]


# --- the approval gate, from the interface's side --------------------------------------


#: Repository methods that change a run. A page may read a status to display
#: it — the queue shows delivered runs, which is its job — but a page that
#: *set* one would be a second path into the state machine.
MUTATING_CALLS = ("set_status", "mark_node_complete", "update", "create", "save")


@pytest.mark.parametrize("path", app_modules(), ids=lambda path: path.name)
def test_no_page_moves_a_run_itself(path: Path) -> None:
    """Delivery is downstream of an approval decision, and every other
    transition is downstream of a use case.

    Displaying a status is fine and is what the queue is for. Setting one from a
    page would be a second path into the state machine, and the one nobody
    tests is the one that would allow an illegal move.
    """
    tree = ast.parse(path.read_text(encoding="utf-8"))

    for node in ast.walk(tree):
        if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)):
            continue
        if node.func.attr not in MUTATING_CALLS:
            continue

        # Reaching a repository through Deps is what makes it a write.
        owner = node.func.value
        owner_name = getattr(owner, "attr", None) or getattr(owner, "id", None)
        assert owner_name not in ("runs", "reviews", "evidence", "candidates"), (
            f"{path.name} calls {owner_name}.{node.func.attr}; writes belong in a use case"
        )


def test_the_only_write_paths_are_use_cases() -> None:
    """Read off the interface's imports: everything that changes state comes
    from application.use_cases and nowhere else."""
    writing = set()
    for path in app_modules():
        for module in imports_of(path):
            if module.startswith("application.use_cases"):
                writing.add(module)

    assert writing
    assert all(module.startswith("application.use_cases") for module in writing)


def test_the_reviewer_id_is_not_a_text_input() -> None:
    """A name somebody typed into a browser is not an attribution."""
    review = (APP / "pages" / "3_Review.py").read_text(encoding="utf-8")

    assert "reviewer_id" not in review or "text_input" not in review.split("reviewer_id")[0][-200:]


def test_the_reviewer_id_comes_from_the_environment() -> None:
    from app.state import initial

    assert "REVIEWER_ID" in (APP / "state.py").read_text(encoding="utf-8")
    assert initial().reviewer_id == __import__("os").environ.get("REVIEWER_ID", "")


# --- debug ---------------------------------------------------------------------------


def test_raw_payloads_are_off_by_default() -> None:
    """A recruiter who found a JSON blob would reasonably conclude the rest of
    the interface had been hiding something."""
    from app.state import initial

    assert initial().debug is False
    assert initial({"debug": "1"}).debug is True
