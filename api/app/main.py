from fastapi import FastAPI

from app.api.routes.storage import router as storage_router
from app.core.errors import AgentErrorException, agent_error_handler

app = FastAPI(title="Character Foundry API", version="0.1.0")

app.add_exception_handler(AgentErrorException, agent_error_handler)
app.include_router(storage_router)


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}
