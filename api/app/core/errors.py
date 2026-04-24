"""AgentError envelope for structured API error responses.

The API returns errors in the shape `{"error": {<AgentError fields>}}` so
both UI and agent callers get a stable, machine-readable surface. See
planning/backend/api-shape.md §4 for field semantics and category prefixes.
"""

from __future__ import annotations

from fastapi import Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel


class AgentError(BaseModel):
    code: str
    message: str
    problem: str
    cause: str
    fix: str
    docs_url: str | None = None
    retryable: bool = False
    request_id: str | None = None


class AgentErrorException(Exception):
    """Raise inside a route to short-circuit with an AgentError JSON body."""

    def __init__(self, error: AgentError, status_code: int = 400) -> None:
        super().__init__(error.message)
        self.error = error
        self.status_code = status_code


def agent_error_handler(request: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, AgentErrorException)
    return JSONResponse(
        status_code=exc.status_code,
        content={"error": exc.error.model_dump()},
    )
