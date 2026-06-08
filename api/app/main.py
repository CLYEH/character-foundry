from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.exception_handlers import request_validation_exception_handler
from fastapi.exceptions import RequestValidationError
from starlette.responses import Response as StarletteResponse

from app.api.routes.aliases import router as aliases_router
from app.api.routes.aliases import singular_router as aliases_singular_router
from app.api.routes.auth import router as auth_router
from app.api.routes.characters import router as characters_router
from app.api.routes.checkpoints import router as checkpoints_router
from app.api.routes.creation_sessions import router as creation_sessions_router
from app.api.routes.health import router as health_router
from app.api.routes.meta import router as meta_router
from app.api.routes.motions import router as motions_router
from app.api.routes.prompt import router as prompt_router
from app.api.routes.reference_images import router as reference_images_router
from app.api.routes.storage import router as storage_router
from app.api.routes.tasks import router as tasks_router
from app.core.errors import AgentErrorException, agent_error_handler
from app.mcp.app import (
    MCPPathNormalizationMiddleware,
    get_mcp_dispatcher,
    mcp_lifespan,
)
from app.mcp.discovery import router as mcp_discovery_router
from app.middleware.error_handling import RequestIdMiddleware
from app.prompt.errors import validation_mask_required


# FastAPI lifespan drives the MCP streamable-HTTP session manager (T-080).
# `app.mount("/mcp", ...)` below pulls in a Starlette sub-app whose own
# lifespan does NOT auto-fire under mount; without this, the FastMCP
# session manager never starts and every /mcp/ request errors out at
# request time. Wrap `mcp_lifespan` so future lifespan needs (DB pool
# warmup, etc.) compose cleanly.
@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    async with mcp_lifespan():
        yield


app = FastAPI(title="Character Foundry API", version="0.1.0", lifespan=lifespan)

app.add_middleware(RequestIdMiddleware)
# Rewrite `/mcp` → `/mcp/` BEFORE the router runs so JSON-RPC clients
# that don't follow 307-on-POST hit the MCP mount with a single round-trip
# regardless of which URL they configure. See middleware docstring.
app.add_middleware(MCPPathNormalizationMiddleware)
app.add_exception_handler(AgentErrorException, agent_error_handler)


# `mask: {}` (or any malformed mask shape that Pydantic 422s under the
# prompt-preview body's `mask` field) gets remapped to the structured
# `VALIDATION_MASK_REQUIRED` AgentError so the frontend can render an
# inline mask-required hint. All other Pydantic 422s fall through to
# FastAPI's default validation handler unchanged. T-035 acceptance.
#
# Anchored to the prompt-preview body shape only — the discriminated
# union surfaces errors under `('body', <mode>, 'mask', ...)`. A bare
# `'mask' in loc` would also match e.g. T-031's mask-upload route's own
# validation errors (size, MIME) and collapse them to
# VALIDATION_MASK_REQUIRED, which is wrong. Reviewer P1.
def _is_prompt_mask_error(loc: tuple[object, ...]) -> bool:
    return len(loc) >= 3 and loc[0] == "body" and loc[2] == "mask"


async def _request_validation_handler(
    request: Request,
    exc: Exception,
) -> StarletteResponse:
    assert isinstance(exc, RequestValidationError)
    for err in exc.errors():
        if _is_prompt_mask_error(tuple(err.get("loc", ()))):
            return agent_error_handler(request, validation_mask_required())
    return await request_validation_exception_handler(request, exc)


app.add_exception_handler(RequestValidationError, _request_validation_handler)
app.include_router(storage_router)
# RFC 9728 Protected Resource Metadata for MCP OAuth discovery (T-089). Public,
# top-level `/.well-known/oauth-protected-resource` — not under `/v1` or `/mcp`.
app.include_router(mcp_discovery_router)
app.include_router(auth_router)
app.include_router(health_router)
app.include_router(meta_router)
app.include_router(tasks_router)
app.include_router(characters_router)
app.include_router(creation_sessions_router)
app.include_router(reference_images_router)
app.include_router(checkpoints_router)
app.include_router(aliases_router)
app.include_router(aliases_singular_router)
app.include_router(prompt_router)
app.include_router(motions_router)

# MCP streamable HTTP server (T-080). Mounted as the last surface so
# `/v1/*` route resolution is unambiguous. Per agent-interface Q7 sub-7a,
# same-process FastAPI sub-app — shares DB session factory / AgentError /
# task system with REST. Auth (dual-stack JWT + OAuth) is wrapped at the
# sub-app's ASGI boundary; per-tool scope enforcement lives in each tool
# implementation (T-080 ships only `hello.world`; T-081 introduces the
# registry pattern).
#
# Mounted as a dispatcher — see `app.mcp.app._MCPDispatcher` docstring —
# so the lifespan can rebuild the FastMCP on each startup without
# re-mounting. `StreamableHTTPSessionManager.run()` is single-use; the
# dispatcher decouples request dispatch from the rebuild lifecycle.
app.mount("/mcp", get_mcp_dispatcher())
