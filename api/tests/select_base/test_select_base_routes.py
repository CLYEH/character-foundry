"""Route-level tests for `POST /v1/creation-sessions/{id}/select-base` (T-018)."""

from __future__ import annotations

import asyncio
import uuid
from typing import Any

from fastapi.testclient import TestClient
from sqlalchemy import text as sql_text
from sqlalchemy.ext.asyncio import create_async_engine

from tests.select_base.conftest import auth_headers, seed_committed_checkpoint


def _create_character(client: TestClient, token: str, *, name: str = "BaseChar") -> dict[str, Any]:
    resp = client.post(
        "/v1/characters",
        json={"name": name, "input_mode": "template"},
        headers=auth_headers(token),
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


def _fetch_session_status(database_url: str, session_id: str) -> dict[str, Any]:
    """Pull session status + completed_at directly so we can assert the
    transactional update landed without going through the read endpoint
    (which has its own auth path)."""

    async def _q() -> dict[str, Any]:
        engine = create_async_engine(database_url, future=True, isolation_level="AUTOCOMMIT")
        try:
            async with engine.connect() as conn:
                row = (
                    await conn.execute(
                        sql_text("SELECT status, completed_at FROM creation_sessions WHERE id=:s"),
                        {"s": session_id},
                    )
                ).first()
                return {"status": row[0], "completed_at": row[1]}
        finally:
            await engine.dispose()

    return asyncio.run(_q())


def _fetch_checkpoint_flag(database_url: str, checkpoint_id: str) -> bool:
    async def _q() -> bool:
        engine = create_async_engine(database_url, future=True, isolation_level="AUTOCOMMIT")
        try:
            async with engine.connect() as conn:
                row = (
                    await conn.execute(
                        sql_text("SELECT selected_as_base FROM checkpoints WHERE id=:c"),
                        {"c": checkpoint_id},
                    )
                ).scalar_one()
                return bool(row)
        finally:
            await engine.dispose()

    return asyncio.run(_q())


def _fetch_character_base_id(database_url: str, character_id: str) -> str | None:
    async def _q() -> str | None:
        engine = create_async_engine(database_url, future=True, isolation_level="AUTOCOMMIT")
        try:
            async with engine.connect() as conn:
                row = (
                    await conn.execute(
                        sql_text("SELECT base_id FROM characters WHERE id=:c"),
                        {"c": character_id},
                    )
                ).scalar_one()
                return str(row) if row is not None else None
        finally:
            await engine.dispose()

    return asyncio.run(_q())


def test_select_base_happy_path_writes_all_four_rows(
    client: TestClient,
    access_token: str,
    database_url: str,
) -> None:
    body = _create_character(client, access_token)
    char_id = body["character"]["id"]
    session_id = body["creation_session"]["id"]

    cid, _ = seed_committed_checkpoint(database_url, session_id)

    resp = client.post(
        f"/v1/creation-sessions/{session_id}/select-base",
        json={"checkpoint_id": cid},
        headers=auth_headers(access_token),
    )
    assert resp.status_code == 200, resp.text
    payload = resp.json()
    assert payload["character"]["id"] == char_id
    assert payload["base"]["character_id"] == char_id
    assert payload["base"]["from_checkpoint_id"] == cid

    # Session flipped to completed with timestamp.
    sess = _fetch_session_status(database_url, session_id)
    assert sess["status"] == "completed"
    assert sess["completed_at"] is not None

    # Checkpoint flagged.
    assert _fetch_checkpoint_flag(database_url, cid) is True

    # Character.base_id is populated.
    assert _fetch_character_base_id(database_url, char_id) is not None


def test_select_base_idempotent_retry_after_completed_returns_409(
    client: TestClient,
    access_token: str,
    database_url: str,
) -> None:
    body = _create_character(client, access_token)
    session_id = body["creation_session"]["id"]
    cid, _ = seed_committed_checkpoint(database_url, session_id)

    first = client.post(
        f"/v1/creation-sessions/{session_id}/select-base",
        json={"checkpoint_id": cid},
        headers=auth_headers(access_token),
    )
    assert first.status_code == 200

    # Even with the same checkpoint id, Phase 1 Base is immutable.
    retry = client.post(
        f"/v1/creation-sessions/{session_id}/select-base",
        json={"checkpoint_id": cid},
        headers=auth_headers(access_token),
    )
    assert retry.status_code == 409
    assert retry.json()["error"]["code"] == "CONFLICT_BASE_LOCKED"


def test_select_base_after_abandon_returns_409(
    client: TestClient,
    access_token: str,
    database_url: str,
) -> None:
    body = _create_character(client, access_token)
    session_id = body["creation_session"]["id"]
    cid, _ = seed_committed_checkpoint(database_url, session_id)

    abandon = client.post(
        f"/v1/creation-sessions/{session_id}/abandon",
        headers=auth_headers(access_token),
    )
    assert abandon.status_code == 204

    resp = client.post(
        f"/v1/creation-sessions/{session_id}/select-base",
        json={"checkpoint_id": cid},
        headers=auth_headers(access_token),
    )
    assert resp.status_code == 409
    assert resp.json()["error"]["code"] == "CONFLICT_BASE_LOCKED"


def test_select_base_unknown_session_returns_404(
    client: TestClient,
    access_token: str,
) -> None:
    resp = client.post(
        f"/v1/creation-sessions/{uuid.uuid4()}/select-base",
        json={"checkpoint_id": str(uuid.uuid4())},
        headers=auth_headers(access_token),
    )
    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "NOT_FOUND_CREATION_SESSION"


def test_select_base_unknown_checkpoint_returns_404(
    client: TestClient,
    access_token: str,
) -> None:
    body = _create_character(client, access_token)
    session_id = body["creation_session"]["id"]

    resp = client.post(
        f"/v1/creation-sessions/{session_id}/select-base",
        json={"checkpoint_id": str(uuid.uuid4())},
        headers=auth_headers(access_token),
    )
    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "NOT_FOUND_CHECKPOINT"


def test_select_base_cross_session_checkpoint_returns_404(
    client: TestClient,
    access_token: str,
    database_url: str,
) -> None:
    """A checkpoint belonging to a sibling session must not be promotable
    via another session's select-base — the response collapses to
    NOT_FOUND_CHECKPOINT to avoid leaking sibling-session ids."""
    a = _create_character(client, access_token, name="A")
    b = _create_character(client, access_token, name="B")

    cid_b, _ = seed_committed_checkpoint(database_url, b["creation_session"]["id"])

    resp = client.post(
        f"/v1/creation-sessions/{a['creation_session']['id']}/select-base",
        json={"checkpoint_id": cid_b},
        headers=auth_headers(access_token),
    )
    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "NOT_FOUND_CHECKPOINT"


def test_select_base_non_initiator_forbidden(
    client: TestClient,
    access_token: str,
    second_user: dict[str, Any],
    second_access_token: str,
    database_url: str,
) -> None:
    body = _create_character(client, access_token)
    session_id = body["creation_session"]["id"]
    cid, _ = seed_committed_checkpoint(database_url, session_id)

    resp = client.post(
        f"/v1/creation-sessions/{session_id}/select-base",
        json={"checkpoint_id": cid},
        headers=auth_headers(second_access_token),
    )
    assert resp.status_code == 403
    assert resp.json()["error"]["code"] == "AUTH_INSUFFICIENT_PERMISSION"


def test_select_base_requires_auth(client: TestClient) -> None:
    resp = client.post(
        f"/v1/creation-sessions/{uuid.uuid4()}/select-base",
        json={"checkpoint_id": str(uuid.uuid4())},
    )
    assert resp.status_code == 401


def test_character_detail_after_select_base_includes_base_payload(
    client: TestClient,
    access_token: str,
    database_url: str,
    storage_root: Any,
) -> None:
    """Character detail's `base` field is serialized from the bases
    table once select-base lands. This checks the round-trip from the
    builder and confirms the list/detail surfaces stay coherent with
    the immediate select-base response."""
    body = _create_character(client, access_token)
    char_id = body["character"]["id"]
    session_id = body["creation_session"]["id"]
    cid, _ = seed_committed_checkpoint(
        database_url, session_id, storage_root=storage_root, write_image=True
    )

    pick = client.post(
        f"/v1/creation-sessions/{session_id}/select-base",
        json={"checkpoint_id": cid},
        headers=auth_headers(access_token),
    )
    assert pick.status_code == 200

    detail = client.get(f"/v1/characters/{char_id}", headers=auth_headers(access_token))
    assert detail.status_code == 200
    char = detail.json()["character"]
    assert char["base"] is not None
    assert char["base"]["from_checkpoint_id"] == cid
    # image_url presence is best-effort (signed URL minting tolerates
    # missing files); thumbnail_url MAY be null because the seeded
    # checkpoint only has the main image.
    assert "image_url" in char["base"]
