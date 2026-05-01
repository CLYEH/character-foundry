"""Route-level tests for T-034 motion CRUD: list / detail / patch / delete.

Covers the ticket's Acceptance criteria:

  - GET list (base / alias) excludes soft-deleted, preset-first then
    `created_at ASC`
  - GET detail carries `description` + `generation` subset
  - PATCH preset → 422 `VALIDATION_PRESET_RENAME_FORBIDDEN`
  - PATCH custom duplicate → 409 `CONFLICT_DUPLICATE_NAME`
  - PATCH invalid chars → 400 `VALIDATION_INVALID_CHARS`
  - DELETE soft-deletes
  - Non-owner → 403 on PATCH / DELETE
  - Soft-deleted id → 404 on GET / PATCH / DELETE
  - Cross-team / unknown ids → NOT_FOUND_*

Seeders run direct SQL via an AUTOCOMMIT engine (mirrors
`tests/aliases/test_crud.py`) so the suite doesn't depend on the
worker pipeline being healthy.
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from tests.motions.conftest import (
    auth_headers,
    create_character_via_api,
    seed_alias_for_character,
    seed_base_for_character,
)

# ---------------------------------------------------------------------------
# Direct-SQL helpers — keep tests independent of the create-motion worker.
# ---------------------------------------------------------------------------


async def _insert_motion(
    database_url: str,
    *,
    parent_type: str,
    parent_id: str,
    motion_type: str,
    name: str,
    description: str | None = None,
    created_at: datetime | None = None,
    deleted: bool = False,
    generation_log_id: uuid.UUID | None = None,
) -> uuid.UUID:
    """Insert a motion row with optional `created_at` override + soft-delete flag."""
    motion_id = uuid.uuid4()
    base_id = parent_id if parent_type == "base" else None
    alias_id = parent_id if parent_type == "alias" else None
    video_key = (
        f"bases/{parent_id}/motions/{motion_id}.mp4"
        if parent_type == "base"
        else f"aliases/{parent_id}/motions/{motion_id}.mp4"
    )
    engine = create_async_engine(database_url, future=True, isolation_level="AUTOCOMMIT")
    try:
        async with engine.connect() as conn:
            await conn.execute(
                text(
                    "INSERT INTO motions "
                    "(id, base_id, alias_id, motion_type, name, description, "
                    " video_key, generation_log_id, created_at, deleted_at) "
                    "VALUES (:mid, :bid, :aid, :mt, :n, :d, :vk, :gid, "
                    "        COALESCE(:ca, now()), "
                    "        CASE WHEN :del THEN now() ELSE NULL END)"
                ),
                {
                    "mid": str(motion_id),
                    "bid": base_id,
                    "aid": alias_id,
                    "mt": motion_type,
                    "n": name,
                    "d": description,
                    "vk": video_key,
                    "gid": str(generation_log_id) if generation_log_id else None,
                    "ca": created_at,
                    "del": deleted,
                },
            )
    finally:
        await engine.dispose()
    return motion_id


async def _row_deleted_at(database_url: str, table: str, row_id: uuid.UUID) -> Any:
    engine = create_async_engine(database_url, future=True, isolation_level="AUTOCOMMIT")
    try:
        async with engine.connect() as conn:
            return (
                await conn.execute(
                    text(f"SELECT deleted_at FROM {table} WHERE id = :i"),
                    {"i": row_id},
                )
            ).scalar_one()
    finally:
        await engine.dispose()


async def _insert_generation_log(
    database_url: str,
    *,
    user_id: uuid.UUID,
    character_id: str,
    motion_id: uuid.UUID,
    model_name: str = "veo-3.1",
    duration_ms: int = 8500,
) -> uuid.UUID:
    """Insert a success-status generation log row and return its id."""
    started_at = datetime.now(UTC) - timedelta(seconds=10)
    completed_at = datetime.now(UTC)
    engine = create_async_engine(database_url, future=True, isolation_level="AUTOCOMMIT")
    try:
        async with engine.connect() as conn:
            row_id = (
                await conn.execute(
                    text(
                        "INSERT INTO generation_logs "
                        "(user_id, character_id, entity_type, entity_id, "
                        " model_name, model_version, final_prompt, "
                        " status, duration_ms, started_at, completed_at) "
                        "VALUES (:u, :c, 'motion', :e, :mn, 'preview', "
                        "        'wave hand prompt', 'success', :dur, "
                        "        :st, :ct) "
                        "RETURNING id"
                    ),
                    {
                        "u": user_id,
                        "c": character_id,
                        "e": motion_id,
                        "mn": model_name,
                        "dur": duration_ms,
                        "st": started_at,
                        "ct": completed_at,
                    },
                )
            ).scalar_one()
            return uuid.UUID(str(row_id))
    finally:
        await engine.dispose()


def _seed_base_for_test(
    client: TestClient,
    access_token: str,
    database_url: str,
    storage_root: Path,
    *,
    char_name: str = "MotionCRUDChar",
) -> tuple[str, str]:
    """Create a character + Base via the existing helpers; return (char_id, base_id)."""
    body = create_character_via_api(client, access_token, name=char_name)
    char_id = body["character"]["id"]
    session_id = body["creation_session"]["id"]
    base_id, _ = seed_base_for_character(
        database_url,
        character_id=char_id,
        creation_session_id=session_id,
        storage_root=storage_root,
    )
    return char_id, base_id


# ---------------------------------------------------------------------------
# GET /v1/bases/{id}/motions — list ordering
# ---------------------------------------------------------------------------


def test_list_base_motions_preset_first_then_created_at_asc(
    client: TestClient,
    access_token: str,
    database_url: str,
    storage_root: Path,
) -> None:
    """Preset rows pin ahead of customs; within each group, `created_at ASC`.
    Soft-deleted rows are invisible."""
    _char_id, base_id = _seed_base_for_test(
        client, access_token, database_url, storage_root, char_name="ListChar"
    )

    base_time = datetime.now(UTC)
    # Custom inserted first (oldest).
    custom_old = asyncio.run(
        _insert_motion(
            database_url,
            parent_type="base",
            parent_id=base_id,
            motion_type="custom",
            name="custom_old",
            description="d",
            created_at=base_time,
        )
    )
    # Preset inserted later but should still appear FIRST.
    preset_late = asyncio.run(
        _insert_motion(
            database_url,
            parent_type="base",
            parent_id=base_id,
            motion_type="preset_wave",
            name="wave",
            created_at=base_time + timedelta(seconds=30),
        )
    )
    # Soft-deleted preset must not appear.
    asyncio.run(
        _insert_motion(
            database_url,
            parent_type="base",
            parent_id=base_id,
            motion_type="preset_nod",
            name="hidden_nod",
            deleted=True,
        )
    )
    # Newer custom — should sit AFTER `custom_old` within the custom group.
    custom_new = asyncio.run(
        _insert_motion(
            database_url,
            parent_type="base",
            parent_id=base_id,
            motion_type="custom",
            name="custom_new",
            description="d2",
            created_at=base_time + timedelta(seconds=60),
        )
    )

    resp = client.get(f"/v1/bases/{base_id}/motions", headers=auth_headers(access_token))
    assert resp.status_code == 200, resp.text
    items = resp.json()["items"]
    ids = [item["id"] for item in items]
    names = [item["name"] for item in items]
    assert names == ["wave", "custom_old", "custom_new"]
    assert ids == [str(preset_late), str(custom_old), str(custom_new)]
    assert "hidden_nod" not in names


def test_list_alias_motions_team_member_can_read(
    client: TestClient,
    access_token: str,
    second_access_token: str,
    second_user: dict[str, Any],
    database_url: str,
    storage_root: Path,
) -> None:
    """Team-wide read — a teammate who can see the alias can list its motions."""
    body = create_character_via_api(client, access_token, name="TeamReadChar")
    char_id = body["character"]["id"]
    alias_id, _ = seed_alias_for_character(
        database_url, character_id=char_id, storage_root=storage_root, name="alias_one"
    )
    asyncio.run(
        _insert_motion(
            database_url,
            parent_type="alias",
            parent_id=alias_id,
            motion_type="custom",
            name="visible_to_team",
            description="seed",
        )
    )

    resp = client.get(
        f"/v1/aliases/{alias_id}/motions",
        headers=auth_headers(second_access_token),
    )
    assert resp.status_code == 200
    assert [m["name"] for m in resp.json()["items"]] == ["visible_to_team"]


def test_list_motions_unknown_base_404(
    client: TestClient,
    access_token: str,
    seeded_user: dict[str, Any],
) -> None:
    resp = client.get(
        f"/v1/bases/{uuid.uuid4()}/motions",
        headers=auth_headers(access_token),
    )
    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "NOT_FOUND_CHARACTER"


def test_list_motions_unknown_alias_404(
    client: TestClient,
    access_token: str,
    seeded_user: dict[str, Any],
) -> None:
    resp = client.get(
        f"/v1/aliases/{uuid.uuid4()}/motions",
        headers=auth_headers(access_token),
    )
    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "NOT_FOUND_ALIAS"


def test_list_motions_empty_for_no_rows(
    client: TestClient,
    access_token: str,
    database_url: str,
    storage_root: Path,
) -> None:
    _char_id, base_id = _seed_base_for_test(
        client, access_token, database_url, storage_root, char_name="EmptyMotionsChar"
    )
    resp = client.get(f"/v1/bases/{base_id}/motions", headers=auth_headers(access_token))
    assert resp.status_code == 200
    assert resp.json() == {"items": []}


# ---------------------------------------------------------------------------
# GET /v1/motions/{id} — detail with description + generation subset
# ---------------------------------------------------------------------------


def test_get_motion_detail_includes_description_and_generation(
    client: TestClient,
    access_token: str,
    seeded_user: dict[str, Any],
    database_url: str,
    storage_root: Path,
) -> None:
    """Detail surface carries `description` and the `generation` subset
    (model_name, duration, completed_at)."""
    char_id, base_id = _seed_base_for_test(
        client, access_token, database_url, storage_root, char_name="DetailChar"
    )
    motion_id = asyncio.run(
        _insert_motion(
            database_url,
            parent_type="base",
            parent_id=base_id,
            motion_type="custom",
            name="signature_dance",
            description="跳一段歡快的舞蹈",
        )
    )
    log_id = asyncio.run(
        _insert_generation_log(
            database_url,
            user_id=seeded_user["id"],
            character_id=char_id,
            motion_id=motion_id,
            model_name="veo-3.1",
            duration_ms=8500,
        )
    )

    # Wire the log id onto the motion row.
    async def _link() -> None:
        engine = create_async_engine(database_url, future=True, isolation_level="AUTOCOMMIT")
        try:
            async with engine.connect() as conn:
                await conn.execute(
                    text("UPDATE motions SET generation_log_id = :g WHERE id = :m"),
                    {"g": log_id, "m": motion_id},
                )
        finally:
            await engine.dispose()

    asyncio.run(_link())

    resp = client.get(f"/v1/motions/{motion_id}", headers=auth_headers(access_token))
    assert resp.status_code == 200, resp.text
    payload = resp.json()["motion"]
    assert payload["id"] == str(motion_id)
    assert payload["description"] == "跳一段歡快的舞蹈"
    assert payload["parent"] == {"type": "base", "id": base_id}
    gen = payload["generation"]
    assert gen is not None
    assert gen["model_name"] == "veo-3.1"
    assert gen["duration_ms"] == 8500
    assert gen["completed_at"] is not None


def test_get_motion_detail_generation_null_when_log_missing(
    client: TestClient,
    access_token: str,
    database_url: str,
    storage_root: Path,
) -> None:
    """A motion without a generation_log_id surfaces `generation: null`
    rather than 500."""
    _char_id, base_id = _seed_base_for_test(
        client, access_token, database_url, storage_root, char_name="NoLogChar"
    )
    motion_id = asyncio.run(
        _insert_motion(
            database_url,
            parent_type="base",
            parent_id=base_id,
            motion_type="preset_wave",
            name="wave_no_log",
        )
    )
    resp = client.get(f"/v1/motions/{motion_id}", headers=auth_headers(access_token))
    assert resp.status_code == 200, resp.text
    assert resp.json()["motion"]["generation"] is None


def test_get_motion_unknown_id_404(
    client: TestClient,
    access_token: str,
    seeded_user: dict[str, Any],
) -> None:
    resp = client.get(f"/v1/motions/{uuid.uuid4()}", headers=auth_headers(access_token))
    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "NOT_FOUND_MOTION"


def test_get_motion_soft_deleted_404(
    client: TestClient,
    access_token: str,
    database_url: str,
    storage_root: Path,
) -> None:
    _char_id, base_id = _seed_base_for_test(
        client, access_token, database_url, storage_root, char_name="DeletedDetailChar"
    )
    motion_id = asyncio.run(
        _insert_motion(
            database_url,
            parent_type="base",
            parent_id=base_id,
            motion_type="custom",
            name="gone",
            description="d",
            deleted=True,
        )
    )
    resp = client.get(f"/v1/motions/{motion_id}", headers=auth_headers(access_token))
    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "NOT_FOUND_MOTION"


def test_get_motion_team_member_can_read(
    client: TestClient,
    access_token: str,
    second_access_token: str,
    second_user: dict[str, Any],
    database_url: str,
    storage_root: Path,
) -> None:
    _char_id, base_id = _seed_base_for_test(
        client, access_token, database_url, storage_root, char_name="TeamDetailChar"
    )
    motion_id = asyncio.run(
        _insert_motion(
            database_url,
            parent_type="base",
            parent_id=base_id,
            motion_type="custom",
            name="shared",
            description="d",
        )
    )
    resp = client.get(f"/v1/motions/{motion_id}", headers=auth_headers(second_access_token))
    assert resp.status_code == 200
    assert resp.json()["motion"]["name"] == "shared"


# ---------------------------------------------------------------------------
# PATCH /v1/motions/{id}
# ---------------------------------------------------------------------------


def test_patch_motion_rename_custom_happy(
    client: TestClient,
    access_token: str,
    database_url: str,
    storage_root: Path,
) -> None:
    _char_id, base_id = _seed_base_for_test(
        client, access_token, database_url, storage_root, char_name="RenameChar"
    )
    motion_id = asyncio.run(
        _insert_motion(
            database_url,
            parent_type="base",
            parent_id=base_id,
            motion_type="custom",
            name="OldName",
            description="d",
        )
    )
    resp = client.patch(
        f"/v1/motions/{motion_id}",
        json={"name": "NewName"},
        headers=auth_headers(access_token),
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["motion"]["name"] == "NewName"


def test_patch_motion_preset_returns_422(
    client: TestClient,
    access_token: str,
    database_url: str,
    storage_root: Path,
) -> None:
    """Preset motions are name-locked → 422 VALIDATION_PRESET_RENAME_FORBIDDEN."""
    _char_id, base_id = _seed_base_for_test(
        client, access_token, database_url, storage_root, char_name="PresetRenameChar"
    )
    motion_id = asyncio.run(
        _insert_motion(
            database_url,
            parent_type="base",
            parent_id=base_id,
            motion_type="preset_wave",
            name="wave",
        )
    )
    resp = client.patch(
        f"/v1/motions/{motion_id}",
        json={"name": "招手歡迎"},
        headers=auth_headers(access_token),
    )
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "VALIDATION_PRESET_RENAME_FORBIDDEN"


def test_patch_motion_duplicate_name_409(
    client: TestClient,
    access_token: str,
    database_url: str,
    storage_root: Path,
) -> None:
    """Renaming a custom motion to a name held by a sibling under the
    same parent → 409 CONFLICT_DUPLICATE_NAME."""
    _char_id, base_id = _seed_base_for_test(
        client, access_token, database_url, storage_root, char_name="DupChar"
    )
    asyncio.run(
        _insert_motion(
            database_url,
            parent_type="base",
            parent_id=base_id,
            motion_type="custom",
            name="taken",
            description="d",
        )
    )
    other_id = asyncio.run(
        _insert_motion(
            database_url,
            parent_type="base",
            parent_id=base_id,
            motion_type="custom",
            name="free",
            description="d",
        )
    )
    resp = client.patch(
        f"/v1/motions/{other_id}",
        json={"name": "taken"},
        headers=auth_headers(access_token),
    )
    assert resp.status_code == 409
    assert resp.json()["error"]["code"] == "CONFLICT_DUPLICATE_NAME"


def test_patch_motion_invalid_chars_400(
    client: TestClient,
    access_token: str,
    database_url: str,
    storage_root: Path,
) -> None:
    _char_id, base_id = _seed_base_for_test(
        client, access_token, database_url, storage_root, char_name="InvCharsChar"
    )
    motion_id = asyncio.run(
        _insert_motion(
            database_url,
            parent_type="base",
            parent_id=base_id,
            motion_type="custom",
            name="ok_name",
            description="d",
        )
    )
    resp = client.patch(
        f"/v1/motions/{motion_id}",
        json={"name": "has spaces"},
        headers=auth_headers(access_token),
    )
    assert resp.status_code == 400
    assert resp.json()["error"]["code"] == "VALIDATION_INVALID_CHARS"


def test_patch_motion_no_op_rename_succeeds(
    client: TestClient,
    access_token: str,
    database_url: str,
    storage_root: Path,
) -> None:
    """Renaming to the current name short-circuits the duplicate probe."""
    _char_id, base_id = _seed_base_for_test(
        client, access_token, database_url, storage_root, char_name="NoOpChar"
    )
    motion_id = asyncio.run(
        _insert_motion(
            database_url,
            parent_type="base",
            parent_id=base_id,
            motion_type="custom",
            name="same",
            description="d",
        )
    )
    resp = client.patch(
        f"/v1/motions/{motion_id}",
        json={"name": "same"},
        headers=auth_headers(access_token),
    )
    assert resp.status_code == 200
    assert resp.json()["motion"]["name"] == "same"


def test_patch_motion_non_owner_403(
    client: TestClient,
    access_token: str,
    second_access_token: str,
    second_user: dict[str, Any],
    database_url: str,
    storage_root: Path,
) -> None:
    _char_id, base_id = _seed_base_for_test(
        client, access_token, database_url, storage_root, char_name="OwnerChar"
    )
    motion_id = asyncio.run(
        _insert_motion(
            database_url,
            parent_type="base",
            parent_id=base_id,
            motion_type="custom",
            name="owned",
            description="d",
        )
    )
    resp = client.patch(
        f"/v1/motions/{motion_id}",
        json={"name": "stolen"},
        headers=auth_headers(second_access_token),
    )
    assert resp.status_code == 403
    assert resp.json()["error"]["code"] == "AUTH_INSUFFICIENT_PERMISSION"


def test_patch_motion_preset_non_owner_returns_403_not_422(
    client: TestClient,
    access_token: str,
    second_access_token: str,
    second_user: dict[str, Any],
    database_url: str,
    storage_root: Path,
) -> None:
    """A non-owner trying to rename a preset must see 403 (perm gate
    fires before the preset-rename check) — not 422 leaking the
    motion's existence to a forbidden caller."""
    _char_id, base_id = _seed_base_for_test(
        client, access_token, database_url, storage_root, char_name="PresetNonOwnerChar"
    )
    motion_id = asyncio.run(
        _insert_motion(
            database_url,
            parent_type="base",
            parent_id=base_id,
            motion_type="preset_idle",
            name="idle",
        )
    )
    resp = client.patch(
        f"/v1/motions/{motion_id}",
        json={"name": "新待機"},
        headers=auth_headers(second_access_token),
    )
    assert resp.status_code == 403
    assert resp.json()["error"]["code"] == "AUTH_INSUFFICIENT_PERMISSION"


def test_patch_motion_soft_deleted_404(
    client: TestClient,
    access_token: str,
    database_url: str,
    storage_root: Path,
) -> None:
    _char_id, base_id = _seed_base_for_test(
        client, access_token, database_url, storage_root, char_name="PatchDeletedChar"
    )
    motion_id = asyncio.run(
        _insert_motion(
            database_url,
            parent_type="base",
            parent_id=base_id,
            motion_type="custom",
            name="gone",
            description="d",
            deleted=True,
        )
    )
    resp = client.patch(
        f"/v1/motions/{motion_id}",
        json={"name": "alive"},
        headers=auth_headers(access_token),
    )
    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "NOT_FOUND_MOTION"


# ---------------------------------------------------------------------------
# DELETE /v1/motions/{id}
# ---------------------------------------------------------------------------


def test_delete_motion_soft_deletes(
    client: TestClient,
    access_token: str,
    database_url: str,
    storage_root: Path,
) -> None:
    _char_id, base_id = _seed_base_for_test(
        client, access_token, database_url, storage_root, char_name="DelChar"
    )
    motion_id = asyncio.run(
        _insert_motion(
            database_url,
            parent_type="base",
            parent_id=base_id,
            motion_type="custom",
            name="to_delete",
            description="d",
        )
    )
    resp = client.delete(f"/v1/motions/{motion_id}", headers=auth_headers(access_token))
    assert resp.status_code == 204
    assert resp.text == ""
    assert asyncio.run(_row_deleted_at(database_url, "motions", motion_id)) is not None


def test_delete_preset_motion_soft_deletes(
    client: TestClient,
    access_token: str,
    database_url: str,
    storage_root: Path,
) -> None:
    """Preset motions are renamable=False but DELETE is allowed — that's
    how the user "regenerates" a preset slot (delete then re-create)."""
    _char_id, base_id = _seed_base_for_test(
        client, access_token, database_url, storage_root, char_name="DelPresetChar"
    )
    motion_id = asyncio.run(
        _insert_motion(
            database_url,
            parent_type="base",
            parent_id=base_id,
            motion_type="preset_wave",
            name="wave",
        )
    )
    resp = client.delete(f"/v1/motions/{motion_id}", headers=auth_headers(access_token))
    assert resp.status_code == 204
    assert asyncio.run(_row_deleted_at(database_url, "motions", motion_id)) is not None


def test_delete_motion_idempotent_404_on_repeat(
    client: TestClient,
    access_token: str,
    database_url: str,
    storage_root: Path,
) -> None:
    _char_id, base_id = _seed_base_for_test(
        client, access_token, database_url, storage_root, char_name="DelTwiceChar"
    )
    motion_id = asyncio.run(
        _insert_motion(
            database_url,
            parent_type="base",
            parent_id=base_id,
            motion_type="custom",
            name="once",
            description="d",
        )
    )
    first = client.delete(f"/v1/motions/{motion_id}", headers=auth_headers(access_token))
    assert first.status_code == 204

    second = client.delete(f"/v1/motions/{motion_id}", headers=auth_headers(access_token))
    assert second.status_code == 404
    assert second.json()["error"]["code"] == "NOT_FOUND_MOTION"


def test_delete_motion_non_owner_403(
    client: TestClient,
    access_token: str,
    second_access_token: str,
    second_user: dict[str, Any],
    database_url: str,
    storage_root: Path,
) -> None:
    _char_id, base_id = _seed_base_for_test(
        client, access_token, database_url, storage_root, char_name="DelNonOwnerChar"
    )
    motion_id = asyncio.run(
        _insert_motion(
            database_url,
            parent_type="base",
            parent_id=base_id,
            motion_type="custom",
            name="owned",
            description="d",
        )
    )
    resp = client.delete(f"/v1/motions/{motion_id}", headers=auth_headers(second_access_token))
    assert resp.status_code == 403
    assert resp.json()["error"]["code"] == "AUTH_INSUFFICIENT_PERMISSION"

    # The motion must still be active — the 403 must not have stamped
    # `deleted_at` as a side effect.
    assert asyncio.run(_row_deleted_at(database_url, "motions", motion_id)) is None


# ---------------------------------------------------------------------------
# Cross-team opacity — must collapse to NOT_FOUND_MOTION on every surface.
# ---------------------------------------------------------------------------


async def _seed_other_team_user(database_url: str) -> tuple[uuid.UUID, uuid.UUID]:
    """Insert a fresh team + user on it. Returns `(user_id, team_id)`.

    The autouse `clean_tables` fixture only deletes from the `_TABLES_TO_CLEAN`
    list (which doesn't include `teams`), so we use a unique team name per
    test to avoid collisions across reruns.
    """
    from app.auth.passwords import hash_password

    team_name = f"other-{uuid.uuid4().hex[:8]}"
    email = f"carol-{uuid.uuid4().hex[:8]}@example.com"
    pw = hash_password("whatever")
    engine = create_async_engine(database_url, future=True, isolation_level="AUTOCOMMIT")
    try:
        async with engine.connect() as conn:
            team_id = (
                await conn.execute(
                    text("INSERT INTO teams (name) VALUES (:n) RETURNING id"),
                    {"n": team_name},
                )
            ).scalar_one()
            user_id = (
                await conn.execute(
                    text(
                        "INSERT INTO users (team_id, name, email, password_hash) "
                        "VALUES (:t, 'Carol', :e, :h) RETURNING id"
                    ),
                    {"t": team_id, "e": email, "h": pw},
                )
            ).scalar_one()
            return uuid.UUID(str(user_id)), uuid.UUID(str(team_id))
    finally:
        await engine.dispose()


def _other_team_token(database_url: str) -> str:
    """Sign an access token for a freshly-seeded user on a brand-new team."""
    from app.auth.jwt import sign_access_token

    user_id, team_id = asyncio.run(_seed_other_team_user(database_url))
    token, _ = sign_access_token(user_id=user_id, team_id=team_id)
    return token


def test_cross_team_get_motion_collapses_to_not_found(
    client: TestClient,
    access_token: str,
    database_url: str,
    storage_root: Path,
) -> None:
    """A user on a sibling team must see NOT_FOUND_MOTION (404), not a
    403 — the response can't reveal that a motion exists outside the
    caller's team boundary."""
    _char_id, base_id = _seed_base_for_test(
        client, access_token, database_url, storage_root, char_name="CrossTeamGetChar"
    )
    motion_id = asyncio.run(
        _insert_motion(
            database_url,
            parent_type="base",
            parent_id=base_id,
            motion_type="custom",
            name="hidden",
            description="d",
        )
    )

    other_token = _other_team_token(database_url)
    resp = client.get(f"/v1/motions/{motion_id}", headers=auth_headers(other_token))
    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "NOT_FOUND_MOTION"


def test_cross_team_patch_motion_collapses_to_not_found(
    client: TestClient,
    access_token: str,
    database_url: str,
    storage_root: Path,
) -> None:
    _char_id, base_id = _seed_base_for_test(
        client, access_token, database_url, storage_root, char_name="CrossTeamPatchChar"
    )
    motion_id = asyncio.run(
        _insert_motion(
            database_url,
            parent_type="base",
            parent_id=base_id,
            motion_type="custom",
            name="hidden_p",
            description="d",
        )
    )
    other_token = _other_team_token(database_url)
    resp = client.patch(
        f"/v1/motions/{motion_id}",
        json={"name": "stolen"},
        headers=auth_headers(other_token),
    )
    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "NOT_FOUND_MOTION"
    # The motion must still be active — cross-team 404 must never fall
    # through to the rename path.
    assert asyncio.run(_row_deleted_at(database_url, "motions", motion_id)) is None


def test_cross_team_delete_motion_collapses_to_not_found(
    client: TestClient,
    access_token: str,
    database_url: str,
    storage_root: Path,
) -> None:
    _char_id, base_id = _seed_base_for_test(
        client, access_token, database_url, storage_root, char_name="CrossTeamDelChar"
    )
    motion_id = asyncio.run(
        _insert_motion(
            database_url,
            parent_type="base",
            parent_id=base_id,
            motion_type="custom",
            name="hidden_d",
            description="d",
        )
    )
    other_token = _other_team_token(database_url)
    resp = client.delete(f"/v1/motions/{motion_id}", headers=auth_headers(other_token))
    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "NOT_FOUND_MOTION"
    assert asyncio.run(_row_deleted_at(database_url, "motions", motion_id)) is None


def test_cross_team_list_base_motions_collapses_to_not_found(
    client: TestClient,
    access_token: str,
    database_url: str,
    storage_root: Path,
) -> None:
    """`GET /v1/bases/{id}/motions` must 404 (not 200 with empty items)
    for a sibling-team caller — the parent base itself is not visible."""
    _char_id, base_id = _seed_base_for_test(
        client, access_token, database_url, storage_root, char_name="CrossTeamListChar"
    )
    other_token = _other_team_token(database_url)
    resp = client.get(f"/v1/bases/{base_id}/motions", headers=auth_headers(other_token))
    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "NOT_FOUND_CHARACTER"
