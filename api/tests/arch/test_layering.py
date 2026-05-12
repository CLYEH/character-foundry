"""Architecture fitness tests — layering / import direction (T-059).

Enforces structural invariants between Python packages so coding agents can
extend the codebase without silently breaking layer boundaries. Contracts
live in `api/pyproject.toml` under `[tool.importlinter]`; this module wraps
`lint-imports` so PR CI fails with an actionable error when a new import
crosses a forbidden line.

Rules currently wired:

1. `app.api.*` must not import `app.models.*` — routes/deps go through
   `app.repositories.*` (returning Pydantic schemas) instead of touching
   ORM rows directly.
2. `app.ai.*` must not import `app.api.*` — single direction: the HTTP
   layer uses AI clients, AI clients never reach up into the HTTP layer.
3. (placeholder, T-054) Once `app/auth/scopes.py` exists, OAuth scope
   literals must live only in that one module. `test_oauth_scope_source_is_centralized`
   scans for hard-coded scope strings outside the canonical source and
   auto-enables the moment T-054 creates the file.

Both `app.api -> app.models` and `app.api.routes.characters -> app.models.character`
style violations are already present in the codebase; they're listed in
`pyproject.toml`'s `ignore_imports` with inline comments separating
sanctioned auth-context exceptions from real leaks, and STATUS.md backlog
row S3.5-1 tracks the cleanup.
"""

from __future__ import annotations

import ast
import shutil
import subprocess
from pathlib import Path

import pytest

# `tests/arch/test_layering.py` lives two levels under `api/`; pin the
# index in case anyone moves the file so the resolution failure surfaces
# loudly instead of silently scanning the wrong tree.
API_ROOT = Path(__file__).resolve().parents[2]
APP_ROOT = API_ROOT / "app"
PYPROJECT = API_ROOT / "pyproject.toml"

# OAuth scope literals to be centralized in `app.auth.scopes` once T-054
# lands. Stored as a tuple so the test fails loudly if anyone edits the
# constants without also editing this list.
OAUTH_SCOPE_LITERALS: tuple[str, ...] = (
    "character:read",
    "character:write",
    "task:read",
    "task:cancel",
    "usage:read",
)
CANONICAL_SCOPE_MODULE = APP_ROOT / "auth" / "scopes.py"


def test_layering_contracts_hold() -> None:
    """All `[tool.importlinter]` contracts in pyproject.toml must pass."""
    script = shutil.which("lint-imports")
    if script is None:
        pytest.fail(
            "`lint-imports` console script not found on PATH. "
            'Install api dev deps first: `pip install -e ".[dev]"`. '
            "CI's `Install api (dev extras)` step already does this; if you "
            "see this failure in CI, something stripped the entry-point "
            "scripts from the install."
        )

    proc = subprocess.run(
        [script, "--config", str(PYPROJECT), "--no-cache"],
        cwd=API_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode == 0:
        return

    pytest.fail(
        "Architecture fitness contracts failed — a new import crossed a\n"
        "forbidden layer boundary. Read the broken contract above to see\n"
        "which source file imports which forbidden module, then apply the\n"
        "matching fix:\n"
        "\n"
        '  • Broken contract "Routes don\'t import ORM models directly":\n'
        "      Replace `from app.models.<entity> import ...` in the route\n"
        "      with the matching `app.repositories.<entity>_repo` helper,\n"
        "      which returns Pydantic schemas instead of ORM rows. If you\n"
        "      genuinely need ORM access from a route, add the offending\n"
        "      edge to `ignore_imports` in pyproject.toml *and* open a\n"
        "      follow-up ticket — silently widening the ignore list defeats\n"
        "      the test.\n"
        "\n"
        '  • Broken contract "AI clients don\'t depend on the HTTP layer":\n'
        "      `app.ai.*` must stay self-contained. Move whatever you need\n"
        "      out of `app.api.*` into `app.core/`, `app.schemas/`, or pass\n"
        "      it in as a constructor argument from the route side.\n"
        "\n"
        "--- lint-imports stdout ---\n"
        f"{proc.stdout}"
        "\n--- lint-imports stderr ---\n"
        f"{proc.stderr}",
        pytrace=False,
    )


def _docstring_node_ids(tree: ast.Module) -> set[int]:
    """Return the `id()` of every string-Constant node that occupies the
    docstring slot of a module, function, async function, or class body.

    A naive substring scan over source text trips on docstrings that
    legitimately mention scope names (e.g. a function docstring saying
    "requires the character:read scope"). Filtering by ast position keeps
    the check precise: only string literals used as real code values
    surface as offenders.
    """
    ids: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Module | ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
            body = getattr(node, "body", None)
            if not body:
                continue
            first = body[0]
            if (
                isinstance(first, ast.Expr)
                and isinstance(first.value, ast.Constant)
                and isinstance(first.value.value, str)
            ):
                ids.add(id(first.value))
    return ids


def test_oauth_scope_source_is_centralized() -> None:
    """Once `app/auth/scopes.py` exists (T-054), every other `.py` under
    `app/` must reference OAuth scope literals via import rather than
    redefining the strings inline.

    Skipped today: `app.auth.scopes` doesn't exist, so there is nothing to
    enforce. T-054 creates the module; the moment it does, this test starts
    guarding drift in `app.mcp` (T-055) and the rest of `app.auth`.
    """
    if not CANONICAL_SCOPE_MODULE.exists():
        pytest.skip(
            "T-054 placeholder: activates when `app/auth/scopes.py` ships "
            "as the canonical OAuth scope source. See "
            "planning/harness/roadmap.md §1 A2 and planning/auth/."
        )

    offenders: list[tuple[Path, str, int]] = []
    canonical = CANONICAL_SCOPE_MODULE.resolve()
    scope_set = set(OAUTH_SCOPE_LITERALS)
    for py in APP_ROOT.rglob("*.py"):
        if py.resolve() == canonical:
            continue
        source = py.read_text(encoding="utf-8")
        try:
            tree = ast.parse(source, filename=str(py))
        except SyntaxError:
            # A syntactically broken file is someone else's problem; the
            # rest of the test suite will surface it. Don't mask it as a
            # scope-source violation.
            continue
        docstring_ids = _docstring_node_ids(tree)
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Constant)
                and isinstance(node.value, str)
                and node.value in scope_set
                and id(node) not in docstring_ids
            ):
                offenders.append((py.relative_to(API_ROOT), node.value, node.lineno))

    assert not offenders, (
        "Hard-coded OAuth scope literals found outside `app.auth.scopes`:\n"
        + "\n".join(f"  • {path}:{lineno}: literal {scope!r}" for path, scope, lineno in offenders)
        + "\n\nFix: import the constant from `app.auth.scopes` instead of "
        "redefining the string. Centralizing scopes is the whole point of the "
        "shared scope source rule — drift here means `app.mcp` and `app.auth` "
        "will disagree on what `character:read` actually grants."
    )
