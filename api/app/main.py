from fastapi import FastAPI

from app.api.routes.auth import router as auth_router
from app.api.routes.health import router as health_router
from app.api.routes.meta import router as meta_router
from app.api.routes.storage import router as storage_router
from app.api.routes.tasks import router as tasks_router
from app.core.errors import AgentErrorException, agent_error_handler
from app.middleware.error_handling import RequestIdMiddleware

app = FastAPI(title="Character Foundry API", version="0.1.0")

app.add_middleware(RequestIdMiddleware)
app.add_exception_handler(AgentErrorException, agent_error_handler)
app.include_router(storage_router)
app.include_router(auth_router)
app.include_router(health_router)
app.include_router(meta_router)
app.include_router(tasks_router)
