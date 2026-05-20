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

# Route-registration forms this static scanner does NOT model. If a scanned
# file uses any of them the scanner would silently undercount endpoints (the
# dangerous false-negative direction for the scope-coverage gate), so we raise
# instead — forcing whoever introduces the form to extend the scanner. Per
# both pre-push reviews (T-081). The modeled form is `@<module_router>.<method>("/path")`.
_UNMODELED_ROUTE_CALLS = frozenset(
    {"add_api_route", "add_websocket_route", "include_router", "api_route", "websocket"}
)


class RouteScanError(RuntimeError):
    """Raised when a route module uses a registration form the scanner can't read."""


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
        # Only a string-literal `prefix=` is resolved; a non-literal
        # (`prefix=SOME_CONST`) falls back to "" and would surface the route
        # under a wrong logical path — a false-positive (the safe direction).
        # Current routes all use literals; revisit if that changes.
        prefix = ""
        for kw in node.value.keywords:
            if kw.arg == "prefix" and isinstance(kw.value, ast.Constant):
                if isinstance(kw.value.value, str):
                    prefix = kw.value.value
        for target in node.targets:
            if isinstance(target, ast.Name):
                prefixes[target.id] = prefix
    return prefixes


def _named_call(call: ast.Call, name: str) -> bool:
    """True if `call` invokes a function named `name` (bare or attribute)."""
    func = call.func
    return (isinstance(func, ast.Name) and func.id == name) or (
        isinstance(func, ast.Attribute) and func.attr == name
    )


def _require_scope_tokens(
    func_node: ast.FunctionDef | ast.AsyncFunctionDef,
) -> tuple[bool, tuple[str, ...]]:
    """Detect `Depends(require_scope(...))` in the legitimate dependency slots.

    Only the route decorator(s) (`dependencies=[Depends(require_scope(...))]`)
    and parameter defaults (`_: None = Depends(require_scope(...))`) count. The
    function BODY is deliberately EXCLUDED: a dead or nested `require_scope(...)`
    call there is not real enforcement, and counting it would let an endpoint
    pass the coverage gate without an actual dependency — a silent false-negative
    flagged by both T-081 pre-push reviews. We further require the call to be
    wrapped in `Depends(...)` so a stray reference can't be miscounted.

    Returns (found, tokens) where tokens are the string values of string-literal
    args plus the identifier names of Name args to each matched require_scope.
    """
    args = func_node.args
    regions: list[ast.AST] = [*func_node.decorator_list]
    regions.extend(d for d in (*args.defaults, *args.kw_defaults) if d is not None)

    found = False
    tokens: list[str] = []
    for region in regions:
        for node in ast.walk(region):
            if not (isinstance(node, ast.Call) and _named_call(node, "Depends")):
                continue
            for dep_arg in node.args:
                if isinstance(dep_arg, ast.Call) and _named_call(dep_arg, "require_scope"):
                    found = True
                    for arg in dep_arg.args:
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
    """Parse every `*.py` recursively under `routes_dir` and return endpoints.

    `rglob` (not `glob`) so a nested route package — e.g. `routes/v2/*.py` for
    API versioning — can't silently escape the coverage gate (Codex review #109
    P2). The `file` field carries the path relative to `routes_dir` so two
    same-named modules in different subdirs stay distinguishable in errors.
    """
    endpoints: list[Endpoint] = []
    for py in sorted(routes_dir.rglob("*.py")):
        rel = str(py.relative_to(routes_dir))
        source = py.read_text(encoding="utf-8")
        tree = ast.parse(source, filename=str(py))
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr in _UNMODELED_ROUTE_CALLS
            ):
                raise RouteScanError(
                    f"{rel}:{node.lineno} uses `.{node.func.attr}(...)`, a route-registration "
                    "form the static scanner does not model. Extend _route_scan.py to cover it "
                    "before merging — otherwise the scope-coverage gate would silently undercount."
                )
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
                        file=rel,
                        lineno=lineno,
                        has_scope=has_scope,
                        scope_tokens=tokens,
                    )
                )
    return endpoints
