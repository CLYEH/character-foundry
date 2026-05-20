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


def test_nested_route_module_is_scanned(tmp_path: Path) -> None:
    # rglob, not glob: a nested route package (e.g. routes/v2/) must not
    # silently escape the gate (Codex review #109 P2).
    nested = tmp_path / "v2"
    nested.mkdir()
    _write(
        nested,
        "things.py",
        "from fastapi import APIRouter\n"
        "router = APIRouter(prefix='/v2/things')\n"
        "@router.get('')\n"
        "async def list_things(): ...\n",
    )
    (ep,) = scan_routes(tmp_path)
    assert ep.path == "/v2/things"
    assert ep.file == str(Path("v2") / "things.py")


def test_keyword_path_decorator_detected(tmp_path: Path) -> None:
    # `@router.get(path="/x")` is valid FastAPI and must not be dropped
    # (Codex #109 P1).
    _write(
        tmp_path,
        "r.py",
        "from fastapi import APIRouter\n"
        "router = APIRouter(prefix='/v1/things')\n"
        "@router.get(path='/{id}')\n"
        "async def get_one(id: str): ...\n",
    )
    (ep,) = scan_routes(tmp_path)
    assert ep.method == "GET"
    assert ep.path == "/v1/things/{id}"


def test_unreadable_path_decorator_raises(tmp_path: Path) -> None:
    # A recognized route decorator whose path isn't a string literal must fail
    # loud, not silently drop the endpoint (Codex #109 P1).
    _write(
        tmp_path,
        "r.py",
        "from fastapi import APIRouter\n"
        "router = APIRouter(prefix='/v1')\n"
        "DYN = '/dyn'\n"
        "@router.get(DYN)\n"
        "async def h(): ...\n",
    )
    with pytest.raises(RouteScanError, match="path"):
        scan_routes(tmp_path)


def test_stacked_decorators_scope_is_per_decorator(tmp_path: Path) -> None:
    # Decorator-level dependencies bind to that decorator only: the route
    # without the dependency must read has_scope=False (Codex #109 P2).
    _write(
        tmp_path,
        "r.py",
        "from fastapi import APIRouter, Depends\n"
        "router = APIRouter(prefix='/v1')\n"
        "@router.get('/a', dependencies=[Depends(require_scope(SCOPE_CHARACTER_READ))])\n"
        "@router.post('/b')\n"
        "async def multi(): ...\n",
    )
    by_route = {(e.method, e.path): e for e in scan_routes(tmp_path)}
    assert by_route[("GET", "/v1/a")].has_scope is True
    assert by_route[("POST", "/v1/b")].has_scope is False


def test_param_default_scope_covers_all_stacked_decorators(tmp_path: Path) -> None:
    # A parameter-default dependency runs for the handler regardless of which
    # decorator dispatched, so it covers every stacked route.
    _write(
        tmp_path,
        "r.py",
        "from fastapi import APIRouter, Depends\n"
        "router = APIRouter(prefix='/v1')\n"
        "@router.get('/a')\n"
        "@router.post('/b')\n"
        "async def multi(_: None = Depends(require_scope(SCOPE_CHARACTER_WRITE))): ...\n",
    )
    by_route = {(e.method, e.path): e for e in scan_routes(tmp_path)}
    assert by_route[("GET", "/v1/a")].has_scope is True
    assert by_route[("POST", "/v1/b")].has_scope is True


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
