"""Provider error → AgentError mapping (T-014)."""

from __future__ import annotations

import httpx

from app.ai.errors import (
    map_exception_to_agent_error,
    map_response_to_agent_error,
)


def _resp(
    status: int, body: object | None = None, headers: dict[str, str] | None = None
) -> httpx.Response:
    return httpx.Response(
        status_code=status,
        json=body,
        headers=headers or {},
        request=httpx.Request("POST", "https://example.test/v1/x"),
    )


def test_429_maps_to_rate_limit_retryable() -> None:
    err = map_response_to_agent_error("gpt-image-2", _resp(429, headers={"Retry-After": "12"}))
    assert err.error.code == "MODEL_RATE_LIMIT"
    assert err.error.retryable is True
    assert err.status_code == 429


def test_content_policy_400_maps_to_prompt_content_policy() -> None:
    body = {
        "error": {
            "code": "content_policy_violation",
            "message": "Your request was rejected by the safety system.",
        }
    }
    err = map_response_to_agent_error("gpt-image-2", _resp(400, body))
    assert err.error.code == "PROMPT_CONTENT_POLICY"
    assert err.error.retryable is False
    # The provider's verbatim rejection message must NOT leak into the user-facing copy.
    assert "rejected by the safety system" not in err.error.message
    assert "rejected by the safety system" not in err.error.problem


def test_400_other_maps_to_invalid_request_not_retryable() -> None:
    body = {"error": {"code": "invalid_param", "message": "size must be one of ..."}}
    err = map_response_to_agent_error("gpt-image-2", _resp(400, body))
    assert err.error.code == "MODEL_INVALID_REQUEST"
    assert err.error.retryable is False


def test_401_maps_to_internal_auth_failed() -> None:
    err = map_response_to_agent_error("gpt-image-2", _resp(401))
    assert err.error.code == "INTERNAL_AUTH_FAILED"
    assert err.error.retryable is False


def test_403_maps_to_internal_auth_failed() -> None:
    err = map_response_to_agent_error("gpt-image-2", _resp(403))
    assert err.error.code == "INTERNAL_AUTH_FAILED"


def test_5xx_maps_to_unavailable_retryable() -> None:
    err = map_response_to_agent_error("gpt-image-2", _resp(503))
    assert err.error.code == "MODEL_UNAVAILABLE"
    assert err.error.retryable is True


def test_timeout_exception_maps_to_model_timeout() -> None:
    request = httpx.Request("POST", "https://example.test/v1/x")
    err = map_exception_to_agent_error(
        "gpt-image-2", httpx.ReadTimeout("timed out", request=request)
    )
    assert err.error.code == "MODEL_TIMEOUT"
    assert err.error.retryable is True


def test_transport_error_maps_to_unavailable() -> None:
    request = httpx.Request("POST", "https://example.test/v1/x")
    err = map_exception_to_agent_error(
        "gpt-image-2", httpx.ConnectError("conn refused", request=request)
    )
    assert err.error.code == "MODEL_UNAVAILABLE"
