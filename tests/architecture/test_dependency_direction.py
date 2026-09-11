"""The layer boundary, enforced.

Dependencies point inward only. This test walks the AST of every module in the
project, resolves each import to an absolute module name, and checks it against
the table below. It is written before any feature code because layer discipline
is the thing that erodes under time pressure, and a convention that is not
executable is a preference.

The table is the specification. Read it first; the machinery underneath only
exists to apply it.
"""

from __future__ import annotations

import ast
import sys
from dataclasses import dataclass, field
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]

# The layer packages. A directory not named here is not subject to the rules:
# tests/ imports everything by design, and scripts/ is operational tooling that
# the application never imports.
LAYER_PACKAGES = (
    "domain",
    "application",
    "pipeline",
    "infrastructure",
    "app",
    "eval",
)


@dataclass(frozen=True)
class LayerRule:
    """What one layer may and may not import.

    ``allowed_external`` is the closed set of non-stdlib distributions a layer
    may import. ``None`` means the layer is not restricted to a closed set,
    which is true only of infrastructure, whose entire job is to hold the
    third-party SDKs the rest of the system must not see.

    ``allowed_internal`` is checked as a dotted prefix, so ``domain.contracts``
    permits ``domain.contracts.evidence`` and forbids ``domain.rules``.

    ``forbidden_modules`` overrides the standard library allowance. Most of the
    standard library is fine in any layer, but its network, database, and
    process gateways are exactly the thing the inner layers must not touch, and
    ``sqlite3`` is in the standard library rather than outside it.
    """

    allowed_external: frozenset[str] | None
    allowed_internal: frozenset[str]
    reason: str
    forbidden_modules: frozenset[str] = frozenset()
    #: Modules permitted imports the rest of their layer is denied, keyed by the
    #: exact module name. A composition root crosses layers by definition: its
    #: whole job is to know which concrete thing satisfies which protocol. The
    #: exemption is named rather than pattern-matched, so it cannot widen by
    #: accident, and a test asserts every other module in the layer still fails.
    composition_roots: dict[str, frozenset[str]] = field(default_factory=dict)


# The standard library's doors to the outside world. Named explicitly in
# ARCHITECTURE.md sections 2 and 12 (`sqlite3` in both
# domain and application), generalised here to the rest of the same category.
IO_GATEWAYS = frozenset(
    {
        "sqlite3",
        "socket",
        "ssl",
        "http",
        "urllib",
        "subprocess",
        "smtplib",
        "ftplib",
    }
)


# ---------------------------------------------------------------------------
# The table. ARCHITECTURE.md section 2 and the layer table in
# docs/ENGINEERING_STANDARDS.md section 3 all state the same thing; this is the
# executable copy.
# ---------------------------------------------------------------------------

LAYER_RULES: dict[str, LayerRule] = {
    "domain": LayerRule(
        allowed_external=frozenset({"pydantic"}),
        forbidden_modules=IO_GATEWAYS,
        allowed_internal=frozenset({"domain"}),
        reason=(
            "The domain holds the rules that must still be correct in three years. "
            "It has no network, no database, and no provider SDK, which is what "
            "makes the scoring and fairness logic testable offline in milliseconds."
        ),
    ),
    "application": LayerRule(
        allowed_external=frozenset({"pydantic"}),
        forbidden_modules=IO_GATEWAYS,
        allowed_internal=frozenset({"domain", "application", "pipeline"}),
        reason=(
            "Use cases own when things happen, never how they are done externally. "
            "They reach infrastructure only through the protocols in domain.ports."
        ),
    ),
    "pipeline": LayerRule(
        allowed_external=frozenset({"pydantic"}),
        forbidden_modules=IO_GATEWAYS,
        allowed_internal=frozenset({"domain", "application", "pipeline"}),
        reason=(
            "A node receives its I/O through Deps. A node that imports an adapter "
            "directly cannot be run against a fake, which breaks the offline suite."
        ),
    ),
    "infrastructure": LayerRule(
        # Open: the adapters are where third-party SDKs are allowed to live.
        allowed_external=None,
        allowed_internal=frozenset(
            {
                "domain.ports",
                "domain.contracts",
                # Adapters raise from the shared taxonomy rather than inventing
                # their own exceptions, so the error types are importable here.
                "domain.errors",
                "infrastructure",
            }
        ),
        reason=(
            "Adapters are replaceable and each has a fake twin. An adapter that "
            "imports a use case has inverted the dependency and cannot be swapped."
        ),
        composition_roots={
            # ARCHITECTURE.md section 2 names this the composition root: the
            # one module that constructs concrete adapters and hands back
            # protocol-typed handles. It therefore has to name the container it
            # fills and the policy it installs. Nothing else in infrastructure
            # may import application at all.
            "infrastructure.factory": frozenset({"application.deps", "application.budget"}),
        },
    ),
    "app": LayerRule(
        allowed_external=None,
        allowed_internal=frozenset(
            {
                # Every action goes through a use case. This is the only way the
                # interface causes anything to happen.
                "application.use_cases",
                # The Deps type, for annotating what a page was handed. A type is
                # not a decision.
                "application.deps",
                # Contracts and the value objects a use case takes as input. The
                # upload page has to build a CandidateRef to pass one.
                "domain.contracts",
                "domain.ports",
                # The integrity wording, defined once so a second interface
                # cannot invent a gentler version of the red banner.
                "domain.security",
                "infrastructure.factory",
                # The deployment checks behind `submission-desk doctor`. They
                # read adapters and return pass/warn/fail; the command renders.
                "infrastructure.doctor",
                # Startup reconciliation, one call from the command line.
                "application.recovery",
                "app",
            }
        ),
        reason=(
            "Rendering and command parsing, zero business logic. If a change here "
            "alters an outcome, the change is in the wrong layer. What the "
            "interface may import is types, values, and use cases: nothing that "
            "computes a band, resolves a criterion, or moves a run."
        ),
    ),
    "eval": LayerRule(
        allowed_external=None,
        allowed_internal=frozenset(
            {
                "domain",
                "application",
                # The harness assembles the system it measures: a concrete
                # source for the case documents, the response validator for the
                # stand-in, the factory for everything else. That is what an
                # end-to-end measurement is, and forbidding it would push the
                # harness towards its own copy of the pipeline — which is the
                # one thing an evaluation must never have.
                "infrastructure",
                "pipeline",
                "infrastructure.factory",
                "eval",
            }
        ),
        reason=(
            "The harness drives the same use cases a reviewer drives. It reads "
            "eval/gold/, which nothing else may read, and it never imports the UI."
        ),
    ),
}


@dataclass(frozen=True)
class Violation:
    module: str
    imported: str
    layer: str

    def __str__(self) -> str:
        return f"{self.module} imports {self.imported!r}, forbidden in layer {self.layer!r}"


def _is_stdlib(top_level: str) -> bool:
    return top_level in sys.stdlib_module_names


def _module_name(path: Path, root: Path) -> str:
    """Dotted module name for a file, relative to the repository root."""
    relative = path.relative_to(root).with_suffix("")
    parts = list(relative.parts)
    if parts[-1] == "__init__":
        parts.pop()
    return ".".join(parts)


def _imports_of(path: Path, root: Path) -> list[str]:
    """Every module a file imports, as an absolute dotted name.

    Relative imports are resolved against the importing module's own package so
    that ``from ..ports import ModelClient`` is checked as ``domain.ports``.
    """
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    package_parts = _module_name(path, root).split(".")
    if path.name != "__init__.py":
        package_parts = package_parts[:-1]

    found: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                base = package_parts[: len(package_parts) - node.level + 1]
                found.append(".".join([*base, node.module] if node.module else base))
            elif node.module:
                found.append(node.module)
    return found


def _layer_of(module: str) -> str | None:
    top_level = module.split(".", 1)[0]
    return top_level if top_level in LAYER_RULES else None


def _permits(rule: LayerRule, imported: str, module: str = "") -> bool:
    top_level = imported.split(".", 1)[0]

    exempt = rule.composition_roots.get(module, frozenset())
    if any(imported == allowed or imported.startswith(f"{allowed}.") for allowed in exempt):
        return True

    if top_level in rule.forbidden_modules:
        return False

    if top_level in LAYER_PACKAGES:
        return any(
            imported == allowed or imported.startswith(f"{allowed}.")
            for allowed in rule.allowed_internal
        )

    if _is_stdlib(top_level):
        return True

    if rule.allowed_external is None:
        return True

    return top_level in rule.allowed_external


def python_files(root: Path) -> list[Path]:
    files: list[Path] = []
    for package in LAYER_PACKAGES:
        directory = root / package
        if directory.is_dir():
            files.extend(sorted(directory.rglob("*.py")))
    return files


def check_tree(root: Path) -> list[Violation]:
    """Every layer violation in the tree, in a stable order."""
    violations: list[Violation] = []
    for path in python_files(root):
        module = _module_name(path, root)
        layer = _layer_of(module)
        if layer is None:
            continue
        rule = LAYER_RULES[layer]
        for imported in _imports_of(path, root):
            if not _permits(rule, imported, module):
                violations.append(Violation(module=module, imported=imported, layer=layer))
    return violations


# ---------------------------------------------------------------------------
# The assertion this whole file exists to make
# ---------------------------------------------------------------------------


def test_dependencies_point_inward() -> None:
    violations = check_tree(REPO_ROOT)
    assert violations == [], "\n".join(str(v) for v in violations)


def test_every_layer_package_is_covered_by_a_rule() -> None:
    """A new layer directory must be given a rule, not silently unchecked."""
    assert set(LAYER_PACKAGES) == set(LAYER_RULES)


# ---------------------------------------------------------------------------
# The checker's own tests. A checker that cannot fail is not a check, so each
# forbidden edge in the table gets a deliberate violation on a temporary tree.
# ---------------------------------------------------------------------------


def _write(root: Path, relative: str, source: str) -> None:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(source, encoding="utf-8")


def test_checker_catches_database_import_in_domain(tmp_path: Path) -> None:
    _write(tmp_path, "domain/rules/aggregate.py", "import sqlite3\n")

    violations = check_tree(tmp_path)

    assert [(v.module, v.imported) for v in violations] == [("domain.rules.aggregate", "sqlite3")]


def test_checker_catches_database_import_in_application(tmp_path: Path) -> None:
    """ARCHITECTURE.md section 12: no SQL and no sqlite3 escapes infrastructure,
    in either of the two inner layers."""
    _write(tmp_path, "application/runner.py", "import sqlite3\n")

    violations = check_tree(tmp_path)

    assert [(v.module, v.imported) for v in violations] == [("application.runner", "sqlite3")]


def test_checker_catches_infrastructure_import_in_domain(tmp_path: Path) -> None:
    _write(tmp_path, "domain/rules/resolve.py", "from infrastructure.storage import runs\n")

    violations = check_tree(tmp_path)

    assert [v.imported for v in violations] == ["infrastructure.storage"]


def test_checker_catches_concrete_adapter_import_in_application(tmp_path: Path) -> None:
    _write(tmp_path, "application/runner.py", "from infrastructure.models.provider import Client\n")

    violations = check_tree(tmp_path)

    assert [v.imported for v in violations] == ["infrastructure.models.provider"]


def test_checker_catches_use_case_import_in_infrastructure(tmp_path: Path) -> None:
    _write(tmp_path, "infrastructure/storage/sqlite/runs.py", "from application import runner\n")

    violations = check_tree(tmp_path)

    assert [v.imported for v in violations] == ["application"]


def test_checker_catches_pipeline_import_in_app(tmp_path: Path) -> None:
    _write(tmp_path, "app/pages/3_Review.py", "from pipeline import assess\n")

    violations = check_tree(tmp_path)

    assert [v.imported for v in violations] == ["pipeline"]


def test_checker_catches_ui_import_in_eval(tmp_path: Path) -> None:
    _write(tmp_path, "eval/runner.py", "import app.state\n")

    violations = check_tree(tmp_path)

    assert [v.imported for v in violations] == ["app.state"]


def test_checker_catches_third_party_import_in_domain(tmp_path: Path) -> None:
    """The domain's allowed set is closed, so an unlisted distribution fails
    without the test needing to know that distribution's name in advance."""
    _write(tmp_path, "domain/contracts/evidence.py", "import httpx\n")

    violations = check_tree(tmp_path)

    assert [v.imported for v in violations] == ["httpx"]


def test_checker_allows_the_legitimate_imports(tmp_path: Path) -> None:
    _write(
        tmp_path,
        "domain/rules/aggregate.py",
        "from __future__ import annotations\n"
        "import math\n"
        "from dataclasses import dataclass, field\n"
        "from pydantic import BaseModel\n"
        "from domain.contracts import Evidence\n",
    )
    _write(
        tmp_path,
        "infrastructure/models/provider.py",
        "import httpx\nfrom domain.ports import ModelClient\n",
    )
    _write(tmp_path, "app/main.py", "from application.use_cases import process_candidate\n")

    assert check_tree(tmp_path) == []


def test_checker_resolves_relative_imports(tmp_path: Path) -> None:
    """A relative import is checked as the absolute module it resolves to,
    otherwise the rules are bypassed by writing ``from ..`` instead."""
    _write(tmp_path, "domain/rules/resolve.py", "from ..contracts import Evidence\n")
    assert check_tree(tmp_path) == []

    _write(tmp_path, "app/pages/queue.py", "from ...pipeline import assess\n")
    violations = check_tree(tmp_path)
    assert [v.imported for v in violations] == ["pipeline"]


def test_the_composition_root_exemption_is_one_module_wide(tmp_path: Path) -> None:
    """Only infrastructure.factory may reach into application. Any other adapter
    doing the same is still a violation, so the exemption cannot widen by
    someone copying the import."""
    _write(tmp_path, "infrastructure/factory.py", "from application.deps import Deps\n")
    _write(tmp_path, "infrastructure/storage/sqlite/runs.py", "from application.deps import Deps\n")

    violations = check_tree(tmp_path)

    assert [v.module for v in violations] == ["infrastructure.storage.sqlite.runs"]


def test_the_composition_root_may_not_import_the_pipeline(tmp_path: Path) -> None:
    """The exemption names two modules, not the application package. A factory
    reaching into pipeline internals would be wiring by reaching through."""
    _write(tmp_path, "infrastructure/factory.py", "from pipeline import assess\n")

    assert [v.imported for v in check_tree(tmp_path)] == ["pipeline"]
