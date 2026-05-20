"""Static AST scan of FastAPI route modules (T-081 shared helper).

Both `check_scope_coverage.py` and `check_mcp_tool_scopes.py` need to know,
for every REST endpoint, its `(METHOD, path)` and whether/which `require_scope`
it declares. This module produces that view by parsing source — NO importing
of the app.

Why static AST rather than importing `app.main` and reading the route table:
per `planning/backend/oauth-mcp-integration.md` §5.1 and the T-081 ticket Note,
importing the whole app to introspect routes is slow and fragile (import-time
side effects from pgvector / numpy / DB engine — see STATUS.md S3.5-2 where
mutmut hit exactly this). It also wouldn't help: `require_scope(...)` returns a
closure, so the required scopes aren't recoverable from the live route object
anyway. Parsing the `require_scope(...)` call arguments out of the source is
the only reliable way to read them.

`path` is the *logical* path = the router's `prefix=` plus the route
decorator's path argument. It is NOT necessarily the externally-served URL
(whatever mount prefix `app.main` adds is irrelevant here): the coverage check
only compares scan output against scan-derived whitelists, and the tool-scope
check compares against `bundles` strings written in the same logical form.

`scope_tokens` are the raw argument tokens passed to `require_scope(...)` —
identifiers (e.g. `SCOPE_CHARACTER_WRITE`) because the architecture-fitness
test forbids bare scope-string literals outside `app/auth/scopes.py`. The
consumer resolves identifiers to values; literals (defensive) pass through.
"""

from __future__ import annotations

import ast
from dataclasses import dataclass
from pathlib import Path

HTTP_METHODS = frozenset({"get", "post", "put", "patch", "delete", "head", "options", "trace"})


@dataclass(frozen=True)
class Endpoint:
    method: str  # upper-case HTTP method
    path: str  # router prefix + route decorator path (logical)
    file: str  # path relative to the scanned routes dir
    lineno: int  # line of the route decorator
    has_scope: bool  # True iff a require_scope(...) call appears in the handler
    scope_tokens: tuple[str, ...]  # raw arg tokens to require_scope(...)


def _router_prefixes(tree: ast.Module) -> dict[str, str]:
    """Map each `<var> = APIRouter(prefix=...)` to its prefix (or "")."""
    prefixes: dict[str, str] = {}
    for node in ast.walk(tree):
        if not isinstance(node, ast.Assign) or not isinstance(node.value, ast.Call):
            continue
        func = node.value.func
        name = func.id if isinstance(func, ast.Name) else getattr(func, "attr", None)
        if name != "APIRouter":
            continue
        prefix = ""
        for kw in node.value.keywords:
            if kw.arg == "prefix" and isinstance(kw.value, ast.Constant):
                if isinstance(kw.value.value, str):
                    prefix = kw.value.value
        for target in node.targets:
            if isinstance(target, ast.Name):
                prefixes[target.id] = prefix
    return prefixes


def _require_scope_tokens(func_node: ast.AST) -> tuple[bool, tuple[str, ...]]:
    """Find any `require_scope(...)` call within `func_node`.

    Walks the whole function (decorators + signature defaults + body), so it
    catches both the `dependencies=[Depends(require_scope(...))]` decorator
    form and the `_: None = Depends(require_scope(...))` parameter-default
    form. Returns (found, tokens) where tokens are the string values of
    string-literal args plus the identifier names of Name args.
    """
    found = False
    tokens: list[str] = []
    for node in ast.walk(func_node):
        if not isinstance(node, ast.Call):
            continue
        callee = node.func
        callee_name = callee.id if isinstance(callee, ast.Name) else getattr(callee, "attr", None)
        if callee_name != "require_scope":
            continue
        found = True
        for arg in node.args:
            if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                tokens.append(arg.value)
            elif isinstance(arg, ast.Name):
                tokens.append(arg.id)
    return found, tuple(tokens)


def _route_decorators(
    func_node: ast.FunctionDef | ast.AsyncFunctionDef,
    prefixes: dict[str, str],
) -> list[tuple[str, str, int]]:
    """Return (METHOD, path, lineno) for each `@<router>.<method>(...)` deco."""
    out: list[tuple[str, str, int]] = []
    for deco in func_node.decorator_list:
        if not isinstance(deco, ast.Call):
            continue
        target = deco.func
        if not isinstance(target, ast.Attribute) or not isinstance(target.value, ast.Name):
            continue
        if target.value.id not in prefixes or target.attr not in HTTP_METHODS:
            continue
        if not deco.args or not isinstance(deco.args[0], ast.Constant):
            continue
        route_path = deco.args[0].value
        if not isinstance(route_path, str):
            continue
        full_path = prefixes[target.value.id] + route_path
        out.append((target.attr.upper(), full_path, deco.lineno))
    return out


def scan_routes(routes_dir: Path) -> list[Endpoint]:
    """Parse every `*.py` under `routes_dir` and return the endpoints found."""
    endpoints: list[Endpoint] = []
    for py in sorted(routes_dir.glob("*.py")):
        source = py.read_text(encoding="utf-8")
        tree = ast.parse(source, filename=str(py))
        prefixes = _router_prefixes(tree)
        if not prefixes:
            continue
        for node in tree.body:
            if not isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
                continue
            decos = _route_decorators(node, prefixes)
            if not decos:
                continue
            has_scope, tokens = _require_scope_tokens(node)
            for method, path, lineno in decos:
                endpoints.append(
                    Endpoint(
                        method=method,
                        path=path,
                        file=py.name,
                        lineno=lineno,
                        has_scope=has_scope,
                        scope_tokens=tokens,
                    )
                )
    return endpoints
