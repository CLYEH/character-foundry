from __future__ import annotations

from fastapi.testclient import TestClient


def test_request_id_echoed_when_client_supplies_it(
    client: TestClient, seeded_user: dict[str, str]
) -> None:
    resp = client.get("/health", headers={"X-Request-Id": "req-abc-123"})
    assert resp.headers.get("x-request-id") == "req-abc-123"


def test_request_id_minted_when_missing(client: TestClient, seeded_user: dict[str, str]) -> None:
    resp = client.get("/health")
    assert resp.status_code == 200
    rid = resp.headers.get("x-request-id")
    assert rid and len(rid) >= 16  # UUID4 hex is 32 chars, allow minted forms


def test_agent_error_body_includes_request_id(
    client: TestClient, seeded_user: dict[str, str]
) -> None:
    """401 responses must carry the same request_id as the response header."""
    resp = client.get("/v1/auth/me", headers={"X-Request-Id": "req-correlate-me"})
    assert resp.status_code == 401
    assert resp.headers.get("x-request-id") == "req-correlate-me"
    body = resp.json()
    assert body["error"]["request_id"] == "req-correlate-me"


def test_agent_error_body_has_minted_request_id_when_client_silent(
    client: TestClient, seeded_user: dict[str, str]
) -> None:
    resp = client.get("/v1/auth/me")
    assert resp.status_code == 401
    minted = resp.headers.get("x-request-id")
    assert minted
    body = resp.json()
    assert body["error"]["request_id"] == minted
