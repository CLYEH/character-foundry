"""Route-level tests for `POST /v1/creation-sessions/{id}/checkpoints`
and `GET /v1/checkpoints/{id}` (T-017)."""

from __future__ import annotations

import uuid
from typing import Any

from fastapi.testclient import TestClient

from tests.checkpoints.conftest import auth_headers


def _create_character(
    client: TestClient,
    token: str,
    *,
    name: str = "ChkSession",
    input_mode: str = "template",
) -> dict[str, Any]:
    resp = client.post(
        "/v1/characters",
        json={"name": name, "input_mode": input_mode},
        headers=auth_headers(token),
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


def test_post_checkpoint_fresh_template_enqueues(client: TestClient, access_token: str) -> None:
    body = _create_character(client, access_token)
    session_id = body["creation_session"]["id"]

    resp = client.post(
        f"/v1/creation-sessions/{session_id}/checkpoints",
        json={
            "mode": "fresh",
            "menu_selections": {"gender": "female", "style": "ink_wash"},
            "freeform_note": "古風感覺",
        },
        headers=auth_headers(access_token),
    )
    assert resp.status_code == 202, resp.text
    payload = resp.json()
    assert "task_id" in payload
    assert "checkpoint_id" in payload
    uuid.UUID(payload["task_id"])
    uuid.UUID(payload["checkpoint_id"])


def test_post_checkpoint_fresh_with_base_id_rejected(client: TestClient, access_token: str) -> None:
    body = _create_character(client, access_token)
    session_id = body["creation_session"]["id"]

    resp = client.post(
        f"/v1/creation-sessions/{session_id}/checkpoints",
        json={
            "mode": "fresh",
            "base_checkpoint_id": str(uuid.uuid4()),
        },
        headers=auth_headers(access_token),
    )
    assert resp.status_code == 400
    assert resp.json()["error"]["code"] == "VALIDATION_CHECKPOINT_MODE"


def test_post_checkpoint_remix_without_base_id_rejected(
    client: TestClient, access_token: str
) -> None:
    body = _create_character(client, access_token)
    session_id = body["creation_session"]["id"]

    resp = client.post(
        f"/v1/creation-sessions/{session_id}/checkpoints",
        json={"mode": "remix"},
        headers=auth_headers(access_token),
    )
    assert resp.status_code == 400
    assert resp.json()["error"]["code"] == "VALIDATION_CHECKPOINT_MODE"


def test_post_checkpoint_reference_mode_requires_reference_image(
    client: TestClient, access_token: str
) -> None:
    body = _create_character(client, access_token, input_mode="reference")
    session_id = body["creation_session"]["id"]

    resp = client.post(
        f"/v1/creation-sessions/{session_id}/checkpoints",
        json={"mode": "fresh"},
        headers=auth_headers(access_token),
    )
    assert resp.status_code == 400
    assert resp.json()["error"]["code"] == "VALIDATION_REFERENCE_IMAGE_REQUIRED"


def test_post_checkpoint_non_initiator_forbidden(
    client: TestClient,
    access_token: str,
    second_user: dict[str, Any],
    second_access_token: str,
) -> None:
    body = _create_character(client, access_token)
    session_id = body["creation_session"]["id"]

    resp = client.post(
        f"/v1/creation-sessions/{session_id}/checkpoints",
        json={"mode": "fresh"},
        headers=auth_headers(second_access_token),
    )
    assert resp.status_code == 403
    assert resp.json()["error"]["code"] == "AUTH_INSUFFICIENT_PERMISSION"


def test_get_checkpoint_returns_404_when_worker_has_not_committed(
    client: TestClient, access_token: str
) -> None:
    body = _create_character(client, access_token)
    session_id = body["creation_session"]["id"]

    resp = client.post(
        f"/v1/creation-sessions/{session_id}/checkpoints",
        json={"mode": "fresh"},
        headers=auth_headers(access_token),
    )
    assert resp.status_code == 202
    checkpoint_id = resp.json()["checkpoint_id"]

    # Worker hasn't run — row doesn't exist yet.
    get_resp = client.get(
        f"/v1/checkpoints/{checkpoint_id}",
        headers=auth_headers(access_token),
    )
    assert get_resp.status_code == 404
    assert get_resp.json()["error"]["code"] == "NOT_FOUND_CHECKPOINT"
