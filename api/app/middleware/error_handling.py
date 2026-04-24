"""Request-scoped error context.

Populates a `request_id` contextvar on every request so `AgentError` bodies
carry a correlator back to server logs (per api-shape §4). Accepts an
inbound `X-Request-Id` when present; otherwise mints a UUID4. The value is
echoed back via the `X-Request-Id` response header.

Actual 401 / 403 / 404 formatting lives in `app.core.errors`; this middleware
just makes sure the contextvar is set before routes run.
"""

from __future__ import annotations

import uuid
from collections.abc import Awaitable, Callable

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

from app.core.errors import set_request_id


class RequestIdMiddleware(BaseHTTPMiddleware):
    async def dispatch(
        self,
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        incoming = request.headers.get("x-request-id")
        request_id = incoming or uuid.uuid4().hex
        set_request_id(request_id)
        try:
            response = await call_next(request)
        finally:
            set_request_id(None)
        response.headers["x-request-id"] = request_id
        return response
