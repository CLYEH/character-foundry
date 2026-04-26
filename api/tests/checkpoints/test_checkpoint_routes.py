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


def _seed_committed_checkpoint(database_url: str, session_id: str, sequence: int = 1) -> str:
    """Insert a checkpoint row directly so the GET endpoint has
    something to serve. Returns the checkpoint id as a string.

    Used by initiator-only tests — we need a real row past the
    `output_image_key NOT NULL` schema, so we synthesise one with
    a fake key (the route doesn't fetch storage at GET time except
    for signed URL minting which tolerates missing files).
    """
    import asyncio
    import uuid as _uuid

    from sqlalchemy import text as sql_text
    from sqlalchemy.ext.asyncio import create_async_engine

    cid = _uuid.uuid4()

    async def _insert() -> None:
        engine = create_async_engine(database_url, future=True, isolation_level="AUTOCOMMIT")
        try:
            async with engine.connect() as conn:
                await conn.execute(
                    sql_text(
                        "INSERT INTO checkpoints (id, creation_session_id, sequence, "
                        "prompt, output_image_key) "
                        "VALUES (:cid, :sid, :seq, 'p', :okey)"
                    ),
                    {
                        "cid": str(cid),
                        "sid": session_id,
                        "seq": sequence,
                        "okey": f"checkpoints/{session_id}/{cid}.png",
                    },
                )
        finally:
            await engine.dispose()

    asyncio.run(_insert())
    return str(cid)


def test_get_checkpoint_initiator_only_blocks_same_team_non_initiator(
    client: TestClient,
    access_token: str,
    second_user: dict[str, Any],
    second_access_token: str,
    database_url: str,
) -> None:
    """Round-15: per storage-layout.md §5.1 checkpoint images are
    initiator-only. Same-team Bob must see 404 even though he can
    read the parent character / session shell."""
    body = _create_character(client, access_token)
    session_id = body["creation_session"]["id"]

    cid = _seed_committed_checkpoint(database_url, session_id)

    # Initiator (Alice) gets it.
    alice_resp = client.get(
        f"/v1/checkpoints/{cid}",
        headers=auth_headers(access_token),
    )
    assert alice_resp.status_code == 200, alice_resp.text
    assert alice_resp.json()["checkpoint"]["id"] == cid

    # Same-team non-initiator (Bob) gets 404, not 403 — the policy
    # collapses missing / cross-team / non-initiator to one code.
    bob_resp = client.get(
        f"/v1/checkpoints/{cid}",
        headers=auth_headers(second_access_token),
    )
    assert bob_resp.status_code == 404
    assert bob_resp.json()["error"]["code"] == "NOT_FOUND_CHECKPOINT"


def test_get_session_redacts_checkpoints_for_non_initiator(
    client: TestClient,
    access_token: str,
    second_user: dict[str, Any],
    second_access_token: str,
    database_url: str,
) -> None:
    """Round-16: `GET /v1/creation-sessions/{id}` is team-readable
    for the session shell, but the embedded checkpoints[] must be
    empty (and checkpoint_count zeroed) for non-initiators —
    otherwise the count alone leaks generation cadence."""
    body = _create_character(client, access_token)
    session_id = body["creation_session"]["id"]

    _seed_committed_checkpoint(database_url, session_id, sequence=1)
    _seed_committed_checkpoint(database_url, session_id, sequence=2)

    # Initiator sees both.
    alice_resp = client.get(
        f"/v1/creation-sessions/{session_id}",
        headers=auth_headers(access_token),
    )
    assert alice_resp.status_code == 200
    alice_payload = alice_resp.json()
    assert alice_payload["session"]["checkpoint_count"] == 2
    assert len(alice_payload["checkpoints"]) == 2

    # Same-team non-initiator sees the session shell but no
    # checkpoints and a redacted count.
    bob_resp = client.get(
        f"/v1/creation-sessions/{session_id}",
        headers=auth_headers(second_access_token),
    )
    assert bob_resp.status_code == 200
    bob_payload = bob_resp.json()
    assert bob_payload["session"]["id"] == session_id
    assert bob_payload["session"]["checkpoint_count"] == 0
    assert bob_payload["checkpoints"] == []
