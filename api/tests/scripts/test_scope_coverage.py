"""Tests for `scripts/check_scope_coverage.py` (CI guardrail 1, T-081)."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import check_scope_coverage as csc
import pytest
from _route_scan import Endpoint

API_ROOT = Path(__file__).resolve().parents[2]


def _ep(method: str, path: str, *, has_scope: bool) -> Endpoint:
    return Endpoint(
        method=method,
        path=path,
        file="fixture.py",
        lineno=1,
        has_scope=has_scope,
        scope_tokens=("SCOPE_CHARACTER_READ",) if has_scope else (),
    )


def test_all_scoped_passes() -> None:
    endpoints = [_ep("POST", "/v1/x", has_scope=True)]
    assert csc.main([], endpoints=endpoints, known_missing=frozenset()) == 0


def test_public_endpoint_is_exempt() -> None:
    # /health, /v1/meta, /v1/auth/*, /storage/* never need scope.
    endpoints = [
        _ep("GET", "/health", has_scope=False),
        _ep("GET", "/v1/meta", has_scope=False),
        _ep("POST", "/v1/auth/login", has_scope=False),
        _ep("GET", "/storage/{key:path}", has_scope=False),
    ]
    assert csc.main([], endpoints=endpoints, known_missing=frozenset()) == 0


def test_new_unscoped_endpoint_fails(capsys: pytest.CaptureFixture[str]) -> None:
    endpoints = [_ep("POST", "/v1/widgets", has_scope=False)]
    rc = csc.main([], endpoints=endpoints, known_missing=frozenset())
    assert rc == 1
    err = capsys.readouterr().err
    assert "missing Depends(require_scope" in err
    assert "POST /v1/widgets" in err


def test_known_missing_endpoint_is_baselined() -> None:
    endpoints = [_ep("POST", "/v1/widgets", has_scope=False)]
    rc = csc.main(
        [],
        endpoints=endpoints,
        known_missing=frozenset({("POST", "/v1/widgets")}),
    )
    assert rc == 0


def test_stale_known_missing_entry_fails(capsys: pytest.CaptureFixture[str]) -> None:
    # The baseline lists an endpoint that is now scoped (or gone) — the entry
    # must be removed, so the gate fails to force cleanup.
    endpoints = [_ep("POST", "/v1/widgets", has_scope=True)]
    rc = csc.main(
        [],
        endpoints=endpoints,
        known_missing=frozenset({("POST", "/v1/widgets")}),
    )
    assert rc == 1
    err = capsys.readouterr().err
    assert "now covered or removed" in err
    assert "POST /v1/widgets" in err


def test_real_repo_passes_via_subprocess() -> None:
    # End-to-end: running the script as CI does (resolves `import _route_scan`
    # off sys.path[0], scans the live routes, uses the real baseline).
    proc = subprocess.run(
        [sys.executable, "scripts/check_scope_coverage.py"],
        cwd=API_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 0, f"stdout={proc.stdout!r} stderr={proc.stderr!r}"
    assert "scope-coverage OK" in proc.stdout
