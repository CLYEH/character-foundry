"""Route-level tests for /v1/creation-sessions/* (T-016)."""

from __future__ import annotations

import uuid
from typing import Any

from fastapi.testclient import TestClient

from tests.creation_sessions.conftest import auth_headers


def _create_character(client: TestClient, token: str) -> dict[str, Any]:
    resp = client.post(
        "/v1/characters",
        json={"name": "SessionOwner", "input_mode": "template"},
        headers=auth_headers(token),
    )
    assert resp.status_code == 201
    return resp.json()


def test_get_session_returns_session_with_empty_checkpoints(
    client: TestClient, access_token: str
) -> None:
    body = _create_character(client, access_token)
    session_id = body["creation_session"]["id"]

    resp = client.get(f"/v1/creation-sessions/{session_id}", headers=auth_headers(access_token))
    assert resp.status_code == 200
    payload = resp.json()
    assert payload["session"]["id"] == session_id
    assert payload["session"]["status"] == "in_progress"
    assert payload["session"]["input_mode"] == "template"
    assert payload["session"]["character_id"] == body["character"]["id"]
    assert payload["checkpoints"] == []


def test_get_session_unknown_id_404(client: TestClient, access_token: str) -> None:
    resp = client.get(f"/v1/creation-sessions/{uuid.uuid4()}", headers=auth_headers(access_token))
    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "NOT_FOUND_CREATION_SESSION"


def test_get_session_requires_auth(client: TestClient) -> None:
    resp = client.get(f"/v1/creation-sessions/{uuid.uuid4()}")
    assert resp.status_code == 401


def test_get_session_visible_to_same_team_member(
    client: TestClient,
    access_token: str,
    second_user: dict[str, Any],
    second_access_token: str,
) -> None:
    body = _create_character(client, access_token)
    session_id = body["creation_session"]["id"]

    # Bob is on the same team — Phase 1 grants read by team membership.
    resp = client.get(
        f"/v1/creation-sessions/{session_id}",
        headers=auth_headers(second_access_token),
    )
    assert resp.status_code == 200


def test_get_session_404_after_character_soft_deleted(
    client: TestClient, access_token: str
) -> None:
    """Codex round-2 P2: soft-deleting the character must hide its
    session from the public read surface. Ticket note "若 character
    已刪，session 不對外出" — internal fork paths can still find the
    row through their own repo call, but `GET /v1/creation-sessions/{id}`
    must collapse to 404 once the linked character is soft-deleted."""
    body = _create_character(client, access_token)
    char_id = body["character"]["id"]
    session_id = body["creation_session"]["id"]

    delete = client.delete(f"/v1/characters/{char_id}", headers=auth_headers(access_token))
    assert delete.status_code == 204

    resp = client.get(
        f"/v1/creation-sessions/{session_id}",
        headers=auth_headers(access_token),
    )
    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "NOT_FOUND_CREATION_SESSION"
