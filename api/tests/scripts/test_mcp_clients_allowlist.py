"""Tests for `scripts/check_mcp_clients_allowlist.py` (CI guardrail 3, T-081)."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import check_mcp_clients_allowlist as cca
import pytest

from app.auth.mcp_clients import ClientPolicy

API_ROOT = Path(__file__).resolve().parents[2]


def test_canonical_and_delegated_pass() -> None:
    clients: dict[str, ClientPolicy] = {
        "delegated": {"scopes": None},
        "m2m": {"scopes": ["character:write", "task:read"]},
    }
    assert cca.main([], allowed_clients=clients) == 0


def test_non_canonical_scope_fails(capsys: pytest.CaptureFixture[str]) -> None:
    clients: dict[str, ClientPolicy] = {"bad": {"scopes": ["character:write", "bogus:scope"]}}
    rc = cca.main([], allowed_clients=clients)
    assert rc == 1
    err = capsys.readouterr().err
    assert "non-canonical scope" in err
    assert "bogus:scope" in err


def test_real_allowlist_passes_via_subprocess() -> None:
    proc = subprocess.run(
        [sys.executable, "scripts/check_mcp_clients_allowlist.py"],
        cwd=API_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 0, f"stdout={proc.stdout!r} stderr={proc.stderr!r}"
    assert "mcp-client-allowlist OK" in proc.stdout
