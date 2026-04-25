"""Provider error → AgentError mapping (T-014)."""

from __future__ import annotations

import httpx

from app.ai.errors import (
    map_exception_to_agent_error,
    map_response_to_agent_error,
    parse_retry_after_seconds,
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


def test_parse_retry_after_seconds_handles_delta_seconds() -> None:
    assert parse_retry_after_seconds("12") == 12.0
    assert parse_retry_after_seconds("0") == 0.0
    # Negative deltas — clamp to 0 rather than sleep into the past.
    assert parse_retry_after_seconds("-3") == 0.0


def test_parse_retry_after_seconds_handles_http_date_future() -> None:
    """Codex P2 round-3: RFC 9110 §10.2.3 also allows HTTP-date format."""
    # Far-future date — must yield a large positive number, not None.
    seconds = parse_retry_after_seconds("Fri, 31 Dec 2099 23:59:59 GMT")
    assert seconds is not None
    assert seconds > 1_000_000  # ~70 years away in 2026 → many seconds


def test_parse_retry_after_seconds_clamps_past_dates_to_zero() -> None:
    """A date in the past means "you can retry now" — return 0, not negative."""
    seconds = parse_retry_after_seconds("Mon, 01 Jan 2000 00:00:00 GMT")
    assert seconds == 0.0


def test_parse_retry_after_seconds_returns_none_for_garbage() -> None:
    assert parse_retry_after_seconds(None) is None
    assert parse_retry_after_seconds("") is None
    assert parse_retry_after_seconds("   ") is None
    assert parse_retry_after_seconds("not a date or number") is None
