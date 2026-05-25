"""Tests for `scripts/check_baseline_resync.py` (PR CI guard, T-065)."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import check_baseline_resync as guard
import pytest

API_ROOT = Path(__file__).resolve().parents[2]


def _pyproject(
    *,
    paths: list[str] | None = None,
    mutmut_spec: str = "mutmut>=3.0",
    covered: bool = True,
) -> str:
    paths = paths or ["app/core/errors.py", "app/ai/circuit.py"]
    paths_toml = "\n".join(f'    "{p}",' for p in paths)
    return f"""
[project]
name = "x"

[project.optional-dependencies]
dev = [
    "pytest>=8.0",
    "{mutmut_spec}",
    "ruff>=0.7",
]

[tool.mutmut]
paths_to_mutate = [
{paths_toml}
]
tests_dir = [
    "tests/ai/test_circuit.py",
]
mutate_only_covered_lines = {str(covered).lower()}
also_copy = [
    "app/",
]
"""


def test_unchanged_config_passes() -> None:
    text = _pyproject()
    rc = guard.main(
        [],
        base_pyproject=text,
        head_pyproject=text,
        baseline_changed=False,
        commit_messages="chore: unrelated change",
    )
    assert rc == 0


def test_config_change_with_baseline_update_passes() -> None:
    base = _pyproject()
    head = _pyproject(paths=["app/core/errors.py", "app/ai/circuit.py", "app/auth/scopes.py"])
    rc = guard.main(
        [],
        base_pyproject=base,
        head_pyproject=head,
        baseline_changed=True,  # baseline JSON also touched in the diff
        commit_messages="feat: widen mutation scope + re-baseline",
    )
    assert rc == 0


def test_config_change_without_baseline_fails(capsys: pytest.CaptureFixture[str]) -> None:
    base = _pyproject()
    head = _pyproject(paths=["app/core/errors.py", "app/ai/circuit.py", "app/auth/scopes.py"])
    rc = guard.main(
        [],
        base_pyproject=base,
        head_pyproject=head,
        baseline_changed=False,
        commit_messages="feat: widen mutation scope but forgot the baseline",
    )
    assert rc == 1
    err = capsys.readouterr().err
    assert "mutmut run" in err
    assert "mutation-baseline.json" in err


def test_mutate_only_covered_lines_flip_trips(capsys: pytest.CaptureFixture[str]) -> None:
    base = _pyproject(covered=True)
    head = _pyproject(covered=False)
    rc = guard.main(
        [],
        base_pyproject=base,
        head_pyproject=head,
        baseline_changed=False,
        commit_messages="chore: flip the speed knob",
    )
    assert rc == 1
    assert "mutmut run" in capsys.readouterr().err


def test_escape_hatch_passes() -> None:
    base = _pyproject()
    head = _pyproject(paths=["app/core/errors.py", "app/ai/circuit.py", "app/auth/scopes.py"])
    rc = guard.main(
        [],
        base_pyproject=base,
        head_pyproject=head,
        baseline_changed=False,
        commit_messages="chore: reorder for readability\n\nbaseline-irrelevant: no surface change",
    )
    assert rc == 0


def test_reordering_paths_is_not_a_change() -> None:
    base = _pyproject(paths=["app/core/errors.py", "app/ai/circuit.py"])
    head = _pyproject(paths=["app/ai/circuit.py", "app/core/errors.py"])  # same set, reordered
    rc = guard.main(
        [],
        base_pyproject=base,
        head_pyproject=head,
        baseline_changed=False,
        commit_messages="chore: alphabetize paths_to_mutate",
    )
    assert rc == 0


def test_mutmut_version_bump_trips(capsys: pytest.CaptureFixture[str]) -> None:
    base = _pyproject(mutmut_spec="mutmut>=3.0")
    head = _pyproject(mutmut_spec="mutmut>=3.5")
    rc = guard.main(
        [],
        base_pyproject=base,
        head_pyproject=head,
        baseline_changed=False,
        commit_messages="chore: bump mutmut",
    )
    assert rc == 1
    assert "mutmut run" in capsys.readouterr().err


def test_unrelated_dev_dep_bump_does_not_trip() -> None:
    # Bumping a non-mutmut dev dep must not be treated as a baseline-sensitive
    # change — the fingerprint only tracks the mutmut pin + [tool.mutmut].
    base = _pyproject().replace("ruff>=0.7", "ruff>=0.7")
    head = _pyproject().replace("ruff>=0.7", "ruff>=0.9")
    rc = guard.main(
        [],
        base_pyproject=base,
        head_pyproject=head,
        baseline_changed=False,
        commit_messages="chore: bump ruff",
    )
    assert rc == 0


def test_pkg_name_handles_extras_and_specifiers() -> None:
    assert guard._pkg_name("mutmut[fast]>=3.0") == "mutmut"
    assert guard._pkg_name("  mutmut >= 3.0 ") == "mutmut"
    assert guard._pkg_name("pytest-cov>=5.0") == "pytest-cov"


def test_real_repo_smoke_via_subprocess() -> None:
    # `--base-ref HEAD` → merge-base(HEAD, HEAD) == HEAD → empty diff → pass.
    # Exercises the real git plumbing (merge-base / show / log) end-to-end
    # without depending on a fetched remote ref.
    proc = subprocess.run(
        [sys.executable, "scripts/check_baseline_resync.py", "--base-ref", "HEAD"],
        cwd=API_ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    assert proc.returncode == 0, f"stdout={proc.stdout!r} stderr={proc.stderr!r}"
    assert "baseline-resync OK" in proc.stdout
