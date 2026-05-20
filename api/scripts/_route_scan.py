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


def _scope_tokens_in(region: ast.AST) -> tuple[bool, list[str]]:
    """Find `Depends(require_scope(...))` within one AST region.

    Returns (found, tokens). Requires the `Depends(...)` wrapper so a stray
    `require_scope` reference can't be miscounted; the caller controls WHICH
    region is searched (parameter defaults vs a single decorator) so dead /
    nested body calls never reach here — a silent false-negative both T-081
    pre-push reviews flagged.
    """
    found = False
    tokens: list[str] = []
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
    return found, tokens


def _param_default_scope(
    func_node: ast.FunctionDef | ast.AsyncFunctionDef,
) -> tuple[bool, list[str]]:
    """Scope deps in parameter defaults — these apply to EVERY route on the function.

    A `_: None = Depends(require_scope(...))` default runs for the handler
    regardless of which decorator dispatched the request, so it legitimately
    covers all of a multi-route handler's decorators. Decorator-level
    `dependencies=[...]` are evaluated separately, per decorator (Codex #109 P2).
    """
    args = func_node.args
    found = False
    tokens: list[str] = []
    for default in (*args.defaults, *args.kw_defaults):
        if default is None:
            continue
        f, t = _scope_tokens_in(default)
        found = found or f
        tokens.extend(t)
    return found, tokens


def _decorator_path(deco: ast.Call) -> str | None:
    """Extract the route path from a decorator: positional[0] or keyword `path=`.

    FastAPI accepts both `@router.get("/x")` and `@router.get(path="/x")`
    (Codex #109 P1). Returns None when neither is a string literal (e.g. a
    variable path) so the caller can fail loud rather than silently drop the route.
    """
    if deco.args and isinstance(deco.args[0], ast.Constant) and isinstance(deco.args[0].value, str):
        return deco.args[0].value
    for kw in deco.keywords:
        if (
            kw.arg == "path"
            and isinstance(kw.value, ast.Constant)
            and isinstance(kw.value.value, str)
        ):
            return kw.value.value
    return None


def _route_decorators(
    func_node: ast.FunctionDef | ast.AsyncFunctionDef,
    prefixes: dict[str, str],
    *,
    file_label: str,
) -> list[tuple[str, str, int, bool, tuple[str, ...]]]:
    """Return (METHOD, path, lineno, has_scope, tokens) per `@<router>.<method>(...)`.

    `has_scope` / `tokens` here are the DECORATOR-level dependencies only; the
    caller ORs in the function-wide parameter-default scope. Raises
    RouteScanError when a recognized route decorator has no readable string path
    (Codex #109 P1) — silently skipping it would drop the endpoint from the gate.
    """
    out: list[tuple[str, str, int, bool, tuple[str, ...]]] = []
    for deco in func_node.decorator_list:
        if not isinstance(deco, ast.Call):
            continue
        target = deco.func
        if not isinstance(target, ast.Attribute) or not isinstance(target.value, ast.Name):
            continue
        if target.value.id not in prefixes or target.attr not in HTTP_METHODS:
            continue
        route_path = _decorator_path(deco)
        if route_path is None:
            raise RouteScanError(
                f"{file_label}:{deco.lineno} `@{target.value.id}.{target.attr}(...)` has no "
                "string-literal path (neither positional nor `path=`). The scanner can't read "
                "its route path; use a literal or extend _route_scan.py before merging."
            )
        deco_found, deco_tokens = _scope_tokens_in(deco)
        full_path = prefixes[target.value.id] + route_path
        out.append((target.attr.upper(), full_path, deco.lineno, deco_found, tuple(deco_tokens)))
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
            decos = _route_decorators(node, prefixes, file_label=rel)
            if not decos:
                continue
            # Parameter-default scope applies to every decorator on the
            # function; decorator-level `dependencies=[...]` applies only to
            # that decorator (Codex #109 P2). Combine per route.
            param_found, param_tokens = _param_default_scope(node)
            for method, path, lineno, deco_found, deco_tokens in decos:
                endpoints.append(
                    Endpoint(
                        method=method,
                        path=path,
                        file=rel,
                        lineno=lineno,
                        has_scope=param_found or deco_found,
                        scope_tokens=(*param_tokens, *deco_tokens),
                    )
                )
    return endpoints
