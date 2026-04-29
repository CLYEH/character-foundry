from fastapi import FastAPI, Request
from fastapi.exception_handlers import request_validation_exception_handler
from fastapi.exceptions import RequestValidationError
from starlette.responses import Response as StarletteResponse

from app.api.routes.auth import router as auth_router
from app.api.routes.characters import router as characters_router
from app.api.routes.checkpoints import router as checkpoints_router
from app.api.routes.creation_sessions import router as creation_sessions_router
from app.api.routes.health import router as health_router
from app.api.routes.meta import router as meta_router
from app.api.routes.prompt import router as prompt_router
from app.api.routes.reference_images import router as reference_images_router
from app.api.routes.storage import router as storage_router
from app.api.routes.tasks import router as tasks_router
from app.core.errors import AgentErrorException, agent_error_handler
from app.middleware.error_handling import RequestIdMiddleware
from app.prompt.errors import validation_mask_required

app = FastAPI(title="Character Foundry API", version="0.1.0")

app.add_middleware(RequestIdMiddleware)
app.add_exception_handler(AgentErrorException, agent_error_handler)


# `mask: {}` (or any malformed mask shape that Pydantic 422s with a
# `mask`-rooted error path) gets remapped to the structured
# `VALIDATION_MASK_REQUIRED` AgentError so the frontend can render an
# inline mask-required hint. All other Pydantic 422s fall through to
# FastAPI's default validation handler unchanged. T-035 acceptance.
async def _request_validation_handler(
    request: Request,
    exc: Exception,
) -> StarletteResponse:
    assert isinstance(exc, RequestValidationError)
    for err in exc.errors():
        loc = err.get("loc", ())
        # `loc` is `(<source>, <field>, ...)` — for a request body
        # rooted at `body`, any error nested under `mask` is a mask
        # shape problem.
        if "mask" in loc:
            return agent_error_handler(request, validation_mask_required())
    return await request_validation_exception_handler(request, exc)


app.add_exception_handler(RequestValidationError, _request_validation_handler)
app.include_router(storage_router)
app.include_router(auth_router)
app.include_router(health_router)
app.include_router(meta_router)
app.include_router(tasks_router)
app.include_router(characters_router)
app.include_router(creation_sessions_router)
app.include_router(reference_images_router)
app.include_router(checkpoints_router)
app.include_router(prompt_router)
