"""Route-level tests for `POST /v1/checkpoints/{id}/fork` (T-018)."""

from __future__ import annotations

import asyncio
import uuid
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient
from sqlalchemy import text as sql_text
from sqlalchemy.ext.asyncio import create_async_engine

from tests.select_base.conftest import auth_headers, seed_committed_checkpoint


def _create_character(client: TestClient, token: str, *, name: str = "ForkSrc") -> dict[str, Any]:
    resp = client.post(
        "/v1/characters",
        json={"name": name, "input_mode": "template"},
        headers=auth_headers(token),
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


def _fetch_checkpoint_image_key(database_url: str, checkpoint_id: str) -> str:
    async def _q() -> str:
        engine = create_async_engine(database_url, future=True, isolation_level="AUTOCOMMIT")
        try:
            async with engine.connect() as conn:
                row = (
                    await conn.execute(
                        sql_text("SELECT output_image_key FROM checkpoints WHERE id=:c"),
                        {"c": checkpoint_id},
                    )
                ).scalar_one()
                return str(row)
        finally:
            await engine.dispose()

    return asyncio.run(_q())


def _fetch_first_checkpoint_for_session(database_url: str, session_id: str) -> dict[str, Any]:
    async def _q() -> dict[str, Any]:
        engine = create_async_engine(database_url, future=True, isolation_level="AUTOCOMMIT")
        try:
            async with engine.connect() as conn:
                row = (
                    await conn.execute(
                        sql_text(
                            "SELECT id, sequence, output_image_key, prompt, generation_log_id "
                            "FROM checkpoints "
                            "WHERE creation_session_id=:s "
                            "ORDER BY sequence ASC LIMIT 1"
                        ),
                        {"s": session_id},
                    )
                ).first()
                return {
                    "id": str(row[0]),
                    "sequence": row[1],
                    "output_image_key": row[2],
                    "prompt": row[3],
                    "generation_log_id": str(row[4]) if row[4] is not None else None,
                }
        finally:
            await engine.dispose()

    return asyncio.run(_q())


def test_fork_creates_new_character_session_and_first_checkpoint(
    client: TestClient,
    access_token: str,
    database_url: str,
    storage_root: Path,
) -> None:
    src = _create_character(client, access_token)
    src_session_id = src["creation_session"]["id"]
    src_cid, src_image_key = seed_committed_checkpoint(
        database_url, src_session_id, storage_root=storage_root, write_image=True
    )

    resp = client.post(
        f"/v1/checkpoints/{src_cid}/fork",
        json={"new_character_name": "ForkedChar"},
        headers=auth_headers(access_token),
    )
    assert resp.status_code == 201, resp.text
    payload = resp.json()
    new_char_id = payload["character"]["id"]
    new_session_id = payload["creation_session"]["id"]

    assert new_char_id != src["character"]["id"]
    assert new_session_id != src_session_id
    assert payload["creation_session"]["status"] == "in_progress"
    assert payload["creation_session"]["input_mode"] == "template"
    assert payload["creation_session"]["checkpoint_count"] == 1

    # First checkpoint inherits prompt by reference, owns its own image.
    forked_first = _fetch_first_checkpoint_for_session(database_url, new_session_id)
    assert forked_first["sequence"] == 1
    assert forked_first["prompt"] == "a base prompt"
    new_image_key = forked_first["output_image_key"]
    assert new_image_key != src_image_key
    assert new_image_key == f"checkpoints/{new_session_id}/{forked_first['id']}.png"

    # Image bytes copied — the file exists at the new key.
    assert (storage_root / new_image_key).is_file()
    # Source still exists too — fork doesn't move.
    assert (storage_root / src_image_key).is_file()


def test_fork_does_not_mutate_source_character_or_session(
    client: TestClient,
    access_token: str,
    database_url: str,
    storage_root: Path,
) -> None:
    src = _create_character(client, access_token)
    src_session_id = src["creation_session"]["id"]
    src_cid, _ = seed_committed_checkpoint(
        database_url, src_session_id, storage_root=storage_root, write_image=True
    )

    resp = client.post(
        f"/v1/checkpoints/{src_cid}/fork",
        json={"new_character_name": "Fork2"},
        headers=auth_headers(access_token),
    )
    assert resp.status_code == 201

    # Source character: same name, base_id still null, no copied_from set
    # in the new character (per ticket: "fork 是不同語義").
    src_char = client.get(
        f"/v1/characters/{src['character']['id']}",
        headers=auth_headers(access_token),
    ).json()["character"]
    assert src_char["name"] == "ForkSrc"
    assert src_char["base"] is None
    assert src_char["copied_from"] is None

    # Source session is still in_progress.
    async def _status() -> str:
        engine = create_async_engine(database_url, future=True, isolation_level="AUTOCOMMIT")
        try:
            async with engine.connect() as conn:
                return str(
                    (
                        await conn.execute(
                            sql_text("SELECT status FROM creation_sessions WHERE id=:s"),
                            {"s": src_session_id},
                        )
                    ).scalar_one()
                )
        finally:
            await engine.dispose()

    assert asyncio.run(_status()) == "in_progress"

    # Source checkpoint is still readable (selected_as_base unchanged).
    src_ckpt_resp = client.get(
        f"/v1/checkpoints/{src_cid}",
        headers=auth_headers(access_token),
    )
    assert src_ckpt_resp.status_code == 200
    assert src_ckpt_resp.json()["checkpoint"]["selected_as_base"] is False


def test_fork_copied_image_survives_source_session_image_deletion(
    client: TestClient,
    access_token: str,
    database_url: str,
    storage_root: Path,
) -> None:
    """Acceptance criteria: 模擬 source session abandoned + cleanup →
    確認 forked character 的 image 仍可讀（檔已複製）.

    Simulate the cleanup by deleting the source image file directly
    after fork — the forked character's image must still be readable
    because we copied (not aliased) the bytes."""
    src = _create_character(client, access_token)
    src_session_id = src["creation_session"]["id"]
    src_cid, src_image_key = seed_committed_checkpoint(
        database_url, src_session_id, storage_root=storage_root, write_image=True
    )

    resp = client.post(
        f"/v1/checkpoints/{src_cid}/fork",
        json={"new_character_name": "Survivor"},
        headers=auth_headers(access_token),
    )
    assert resp.status_code == 201
    new_session_id = resp.json()["creation_session"]["id"]
    forked_first = _fetch_first_checkpoint_for_session(database_url, new_session_id)
    new_image_path = storage_root / forked_first["output_image_key"]

    assert new_image_path.is_file()

    # Wipe the source bytes.
    (storage_root / src_image_key).unlink()
    assert not (storage_root / src_image_key).exists()

    # Forked image still on disk — proof the bytes were copied
    # rather than aliased via a soft reference. (LocalFilesystemBackend
    # uses os.link which is fine: hardlinks share an inode but are
    # independently unlinkable.)
    assert new_image_path.is_file()


def test_fork_unknown_checkpoint_returns_404(
    client: TestClient,
    access_token: str,
) -> None:
    resp = client.post(
        f"/v1/checkpoints/{uuid.uuid4()}/fork",
        json={"new_character_name": "Whatever"},
        headers=auth_headers(access_token),
    )
    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "NOT_FOUND_CHECKPOINT"


def test_fork_other_users_checkpoint_returns_404(
    client: TestClient,
    access_token: str,
    second_user: dict[str, Any],
    second_access_token: str,
    database_url: str,
    storage_root: Path,
) -> None:
    """Even same-team Bob can't fork Alice's checkpoint — initiator-only
    per storage-layout §5.1, mirrored from `get_checkpoint_for_read`."""
    src = _create_character(client, access_token)
    src_cid, _ = seed_committed_checkpoint(
        database_url,
        src["creation_session"]["id"],
        storage_root=storage_root,
        write_image=True,
    )
    resp = client.post(
        f"/v1/checkpoints/{src_cid}/fork",
        json={"new_character_name": "BobFork"},
        headers=auth_headers(second_access_token),
    )
    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "NOT_FOUND_CHECKPOINT"


def test_fork_duplicate_new_character_name_returns_409(
    client: TestClient,
    access_token: str,
    database_url: str,
    storage_root: Path,
) -> None:
    src = _create_character(client, access_token, name="ExistingName")
    src_cid, _ = seed_committed_checkpoint(
        database_url,
        src["creation_session"]["id"],
        storage_root=storage_root,
        write_image=True,
    )

    resp = client.post(
        f"/v1/checkpoints/{src_cid}/fork",
        json={"new_character_name": "ExistingName"},
        headers=auth_headers(access_token),
    )
    assert resp.status_code == 409
    assert resp.json()["error"]["code"] == "CONFLICT_DUPLICATE_NAME"


def test_fork_invalid_name_returns_400(
    client: TestClient,
    access_token: str,
    database_url: str,
    storage_root: Path,
) -> None:
    src = _create_character(client, access_token)
    src_cid, _ = seed_committed_checkpoint(
        database_url,
        src["creation_session"]["id"],
        storage_root=storage_root,
        write_image=True,
    )

    resp = client.post(
        f"/v1/checkpoints/{src_cid}/fork",
        json={"new_character_name": "has spaces!"},
        headers=auth_headers(access_token),
    )
    assert resp.status_code == 400
    assert resp.json()["error"]["code"] == "VALIDATION_INVALID_CHARS"


def test_fork_oversized_name_returns_422(
    client: TestClient,
    access_token: str,
    database_url: str,
    storage_root: Path,
) -> None:
    """Codex round-1 P1: a 51+ character name must be rejected at the
    wire layer (Pydantic NameStr length check) instead of slipping
    past `name_pattern_ok` and tripping the DB CHECK constraint
    `chk_characters_name_length` as a 500."""
    src = _create_character(client, access_token)
    src_cid, _ = seed_committed_checkpoint(
        database_url,
        src["creation_session"]["id"],
        storage_root=storage_root,
        write_image=True,
    )
    # 60 'a's — passes the regex but exceeds the 50-char cap.
    long_name = "a" * 60

    resp = client.post(
        f"/v1/checkpoints/{src_cid}/fork",
        json={"new_character_name": long_name},
        headers=auth_headers(access_token),
    )
    # Pydantic's StringConstraints surfaces as 422; we don't (yet)
    # remap to the 400 AgentError envelope for this case — the
    # important thing is no 500 / DB integrity surface.
    assert resp.status_code == 422


def test_fork_after_source_session_abandoned_still_works(
    client: TestClient,
    access_token: str,
    database_url: str,
    storage_root: Path,
) -> None:
    """Acceptance: "Abandon 後 checkpoint 仍可被 fork（這是刻意 — 讓
    「先放著後再回來做」可行）"."""
    src = _create_character(client, access_token)
    src_session_id = src["creation_session"]["id"]
    src_cid, _ = seed_committed_checkpoint(
        database_url, src_session_id, storage_root=storage_root, write_image=True
    )

    abandon = client.post(
        f"/v1/creation-sessions/{src_session_id}/abandon",
        headers=auth_headers(access_token),
    )
    assert abandon.status_code == 204

    resp = client.post(
        f"/v1/checkpoints/{src_cid}/fork",
        json={"new_character_name": "AfterAbandon"},
        headers=auth_headers(access_token),
    )
    assert resp.status_code == 201


def test_fork_requires_auth(client: TestClient) -> None:
    resp = client.post(
        f"/v1/checkpoints/{uuid.uuid4()}/fork",
        json={"new_character_name": "Whatever"},
    )
    assert resp.status_code == 401
