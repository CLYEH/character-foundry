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


def test_get_session_cross_team_returns_session_not_found(
    client: TestClient,
    access_token: str,
    seeded_user: dict[str, Any],
    database_url: str,
) -> None:
    """Codex round-3 P2: cross-team requests must surface as
    NOT_FOUND_CREATION_SESSION, not NOT_FOUND_CHARACTER. The endpoint
    contract is "session not visible"; leaking a character-shaped
    error envelope would let callers distinguish "session id maps to
    a character in another team" from "session id is bogus".

    Phase 1 has a single team, so we synthesize the cross-team
    scenario by minting a JWT for a user belonging to a freshly
    created second team. The service's team-check path runs against
    the real `users.team_id` column, so we have to insert a real user
    row in the new team rather than just patching the JWT payload.
    """
    import asyncio
    import uuid as _uuid

    from sqlalchemy import text as sql_text
    from sqlalchemy.ext.asyncio import create_async_engine

    from app.auth.jwt import sign_access_token
    from app.auth.passwords import hash_password

    body = _create_character(client, access_token)
    session_id = body["creation_session"]["id"]

    async def _seed_other_team_user() -> _uuid.UUID:
        engine = create_async_engine(database_url, future=True, isolation_level="AUTOCOMMIT")
        try:
            async with engine.connect() as conn:
                team_id = (
                    await conn.execute(
                        sql_text("INSERT INTO teams (name) VALUES ('other') RETURNING id")
                    )
                ).scalar_one()
                user_id = (
                    await conn.execute(
                        sql_text(
                            "INSERT INTO users (team_id, name, email, password_hash) "
                            "VALUES (:t, 'Carol', 'carol@example.com', :h) RETURNING id"
                        ),
                        {"t": team_id, "h": hash_password("whatever")},
                    )
                ).scalar_one()
                return _uuid.UUID(str(user_id))
        finally:
            await engine.dispose()

    other_user_id = asyncio.run(_seed_other_team_user())
    token, _ = sign_access_token(user_id=other_user_id, team_id=_uuid.UUID(int=0))

    resp = client.get(
        f"/v1/creation-sessions/{session_id}",
        headers=auth_headers(token),
    )
    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "NOT_FOUND_CREATION_SESSION"

    # Cleanup so the autouse cleaner doesn't trip on the extra team row.
    async def _cleanup_other_team() -> None:
        engine = create_async_engine(database_url, future=True, isolation_level="AUTOCOMMIT")
        try:
            async with engine.connect() as conn:
                await conn.execute(sql_text("DELETE FROM users WHERE email = 'carol@example.com'"))
                await conn.execute(sql_text("DELETE FROM teams WHERE name = 'other'"))
        finally:
            await engine.dispose()

    asyncio.run(_cleanup_other_team())
