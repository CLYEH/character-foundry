"""Tests for the static route scanner `scripts/_route_scan.py` (T-081).

These guard the scanner's correctness directly — especially the
false-negative direction (an unscoped endpoint counted as scoped), which is
what makes the coverage gate trustworthy. Fixtures are written as source
strings into a temp dir; the scanner is pure static AST so the referenced
names (`Depends`, `require_scope`, `SCOPE_*`) need not be importable.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from _route_scan import RouteScanError, scan_routes


def _write(routes_dir: Path, name: str, source: str) -> None:
    (routes_dir / name).write_text(source, encoding="utf-8")


def test_param_default_require_scope_detected(tmp_path: Path) -> None:
    _write(
        tmp_path,
        "r.py",
        "from fastapi import APIRouter, Depends\n"
        "router = APIRouter(prefix='/v1/things')\n"
        "@router.post('')\n"
        "async def create(_: None = Depends(require_scope(SCOPE_CHARACTER_WRITE))):\n"
        "    ...\n",
    )
    (ep,) = scan_routes(tmp_path)
    assert ep.method == "POST"
    assert ep.path == "/v1/things"
    assert ep.has_scope is True
    assert ep.scope_tokens == ("SCOPE_CHARACTER_WRITE",)


def test_decorator_dependencies_require_scope_detected(tmp_path: Path) -> None:
    _write(
        tmp_path,
        "r.py",
        "from fastapi import APIRouter, Depends\n"
        "router = APIRouter(prefix='/v1/things')\n"
        "@router.get('/{id}', dependencies=[Depends(require_scope(SCOPE_CHARACTER_READ))])\n"
        "async def get_one(id: str):\n"
        "    ...\n",
    )
    (ep,) = scan_routes(tmp_path)
    assert ep.path == "/v1/things/{id}"
    assert ep.has_scope is True
    assert ep.scope_tokens == ("SCOPE_CHARACTER_READ",)


def test_body_only_require_scope_is_not_counted(tmp_path: Path) -> None:
    # A `require_scope(...)` reference in the BODY is not a real dependency —
    # the scanner must report has_scope=False so this endpoint still trips the
    # coverage gate (the dangerous false-negative both reviews flagged).
    _write(
        tmp_path,
        "r.py",
        "from fastapi import APIRouter, Depends\n"
        "router = APIRouter(prefix='/v1/things')\n"
        "@router.post('')\n"
        "async def create():\n"
        "    def _unused():\n"
        "        return Depends(require_scope(SCOPE_CHARACTER_WRITE))\n"
        "    return None\n",
    )
    (ep,) = scan_routes(tmp_path)
    assert ep.has_scope is False
    assert ep.scope_tokens == ()


def test_no_prefix_router(tmp_path: Path) -> None:
    _write(
        tmp_path,
        "h.py",
        "from fastapi import APIRouter\nrouter = APIRouter()\n@router.get('/health')\nasync def h(): ...\n",
    )
    (ep,) = scan_routes(tmp_path)
    assert ep.path == "/health"
    assert ep.has_scope is False


def test_unmodeled_registration_form_raises(tmp_path: Path) -> None:
    _write(
        tmp_path,
        "r.py",
        "from fastapi import APIRouter\n"
        "router = APIRouter(prefix='/v1/things')\n"
        "router.add_api_route('/x', handler, methods=['GET'])\n",
    )
    with pytest.raises(RouteScanError, match="add_api_route"):
        scan_routes(tmp_path)
