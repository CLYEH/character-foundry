"""Route-level tests for `POST /v1/creation-sessions/{id}/abandon` (T-018)."""

from __future__ import annotations

import asyncio
import uuid
from typing import Any

from fastapi.testclient import TestClient
from sqlalchemy import text as sql_text
from sqlalchemy.ext.asyncio import create_async_engine

from tests.select_base.conftest import auth_headers, seed_committed_checkpoint


def _create_character(
    client: TestClient, token: str, *, name: str = "AbandonChar"
) -> dict[str, Any]:
    resp = client.post(
        "/v1/characters",
        json={"name": name, "input_mode": "template"},
        headers=auth_headers(token),
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


def _fetch_session_status(database_url: str, session_id: str) -> str:
    async def _q() -> str:
        engine = create_async_engine(database_url, future=True, isolation_level="AUTOCOMMIT")
        try:
            async with engine.connect() as conn:
                row = (
                    await conn.execute(
                        sql_text("SELECT status FROM creation_sessions WHERE id=:s"),
                        {"s": session_id},
                    )
                ).scalar_one()
                return str(row)
        finally:
            await engine.dispose()

    return asyncio.run(_q())


def test_abandon_marks_session_abandoned(
    client: TestClient,
    access_token: str,
    database_url: str,
) -> None:
    body = _create_character(client, access_token)
    session_id = body["creation_session"]["id"]

    resp = client.post(
        f"/v1/creation-sessions/{session_id}/abandon",
        headers=auth_headers(access_token),
    )
    assert resp.status_code == 204
    assert resp.text == ""

    assert _fetch_session_status(database_url, session_id) == "abandoned"


def test_abandon_is_idempotent(
    client: TestClient,
    access_token: str,
    database_url: str,
) -> None:
    body = _create_character(client, access_token)
    session_id = body["creation_session"]["id"]

    first = client.post(
        f"/v1/creation-sessions/{session_id}/abandon",
        headers=auth_headers(access_token),
    )
    assert first.status_code == 204

    second = client.post(
        f"/v1/creation-sessions/{session_id}/abandon",
        headers=auth_headers(access_token),
    )
    assert second.status_code == 204
    assert _fetch_session_status(database_url, session_id) == "abandoned"


def test_abandon_after_select_base_returns_409(
    client: TestClient,
    access_token: str,
    database_url: str,
) -> None:
    body = _create_character(client, access_token)
    session_id = body["creation_session"]["id"]
    cid, _ = seed_committed_checkpoint(database_url, session_id)

    pick = client.post(
        f"/v1/creation-sessions/{session_id}/select-base",
        json={"checkpoint_id": cid},
        headers=auth_headers(access_token),
    )
    assert pick.status_code == 200

    resp = client.post(
        f"/v1/creation-sessions/{session_id}/abandon",
        headers=auth_headers(access_token),
    )
    assert resp.status_code == 409
    assert resp.json()["error"]["code"] == "CONFLICT_BASE_LOCKED"
    # Session stays completed — abandon must not silently overwrite.
    assert _fetch_session_status(database_url, session_id) == "completed"


def test_abandon_unknown_session_returns_404(
    client: TestClient,
    access_token: str,
) -> None:
    resp = client.post(
        f"/v1/creation-sessions/{uuid.uuid4()}/abandon",
        headers=auth_headers(access_token),
    )
    assert resp.status_code == 404


def test_abandon_non_initiator_forbidden(
    client: TestClient,
    access_token: str,
    second_user: dict[str, Any],
    second_access_token: str,
) -> None:
    body = _create_character(client, access_token)
    session_id = body["creation_session"]["id"]

    resp = client.post(
        f"/v1/creation-sessions/{session_id}/abandon",
        headers=auth_headers(second_access_token),
    )
    assert resp.status_code == 403
    assert resp.json()["error"]["code"] == "AUTH_INSUFFICIENT_PERMISSION"


def test_abandon_requires_auth(client: TestClient) -> None:
    resp = client.post(f"/v1/creation-sessions/{uuid.uuid4()}/abandon")
    assert resp.status_code == 401


def test_post_checkpoint_after_abandon_returns_409(
    client: TestClient,
    access_token: str,
) -> None:
    """The pre-existing CONFLICT_SESSION_NOT_ACTIVE guard on the
    checkpoint POST needs to still fire after abandon — covered by
    T-017 indirectly, but worth pinning here so a future change to
    the abandon flow doesn't accidentally leave the session writable."""
    body = _create_character(client, access_token)
    session_id = body["creation_session"]["id"]

    abandon = client.post(
        f"/v1/creation-sessions/{session_id}/abandon",
        headers=auth_headers(access_token),
    )
    assert abandon.status_code == 204

    resp = client.post(
        f"/v1/creation-sessions/{session_id}/checkpoints",
        json={"mode": "fresh"},
        headers=auth_headers(access_token),
    )
    assert resp.status_code == 409
    assert resp.json()["error"]["code"] == "CONFLICT_SESSION_NOT_ACTIVE"
