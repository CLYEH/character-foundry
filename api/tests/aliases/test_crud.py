"""Route-level tests for T-032 alias CRUD: list / detail / patch / delete.

Scope mirrors the ticket's Acceptance criteria:

  - GET list excludes soft-deleted, sorted `created_at ASC`
  - GET detail carries `motion_count`
  - PATCH 409 on duplicate name within same character
  - DELETE soft-deletes alias + cascade-soft-deletes its motions
  - 404 on stale (soft-deleted) ids; 403 on non-owner
  - `CharacterDetail.aliases` excludes deleted aliases

Seed pattern follows `tests/aliases/conftest.py` — direct SQL via
AUTOCOMMIT engine so the duplicate / delete fixtures don't depend on
the create flow (which would require running the worker).
"""

from __future__ import annotations

import asyncio
import uuid
from typing import Any

from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from tests.aliases.conftest import auth_headers

# ---------------------------------------------------------------------------
# Direct-SQL seeders — keep tests independent of the create-alias worker.
# ---------------------------------------------------------------------------


async def _insert_alias(
    database_url: str,
    *,
    character_id: uuid.UUID,
    name: str,
    image_key: str = "aliases/seed.png",
    deleted: bool = False,
) -> uuid.UUID:
    engine = create_async_engine(database_url, future=True, isolation_level="AUTOCOMMIT")
    try:
        async with engine.connect() as conn:
            row_id = (
                await conn.execute(
                    text(
                        "INSERT INTO aliases "
                        "(character_id, name, prompt, input_mode, image_key, "
                        " deleted_at) "
                        "VALUES (:c, :n, 'p', 'text2image', :k, "
                        "  CASE WHEN :d THEN now() ELSE NULL END) "
                        "RETURNING id"
                    ),
                    {"c": character_id, "n": name, "k": image_key, "d": deleted},
                )
            ).scalar_one()
            return uuid.UUID(str(row_id))
    finally:
        await engine.dispose()


async def _insert_motion_for_alias(
    database_url: str,
    *,
    alias_id: uuid.UUID,
    name: str,
    motion_type: str = "custom",
    description: str | None = "seed",
) -> uuid.UUID:
    engine = create_async_engine(database_url, future=True, isolation_level="AUTOCOMMIT")
    try:
        async with engine.connect() as conn:
            row_id = (
                await conn.execute(
                    text(
                        "INSERT INTO motions "
                        "(alias_id, motion_type, name, description, video_key) "
                        "VALUES (:a, :t, :n, :d, :v) RETURNING id"
                    ),
                    {
                        "a": alias_id,
                        "t": motion_type,
                        "n": name,
                        "d": description,
                        "v": f"motions/{uuid.uuid4()}.mp4",
                    },
                )
            ).scalar_one()
            return uuid.UUID(str(row_id))
    finally:
        await engine.dispose()


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


# ---------------------------------------------------------------------------
# GET /v1/characters/{id}/aliases
# ---------------------------------------------------------------------------


def test_list_aliases_sorted_created_at_asc_excludes_deleted(
    client: TestClient,
    access_token: str,
    seeded_character_with_base: dict[str, Any],
    database_url: str,
) -> None:
    """List returns active aliases in `created_at ASC` order; deleted
    rows are invisible."""
    character_id = seeded_character_with_base["id"]

    # Seed three aliases in order; the middle one is soft-deleted.
    first = asyncio.run(_insert_alias(database_url, character_id=character_id, name="First"))
    asyncio.run(_insert_alias(database_url, character_id=character_id, name="Hidden", deleted=True))
    last = asyncio.run(_insert_alias(database_url, character_id=character_id, name="Last"))

    resp = client.get(
        f"/v1/characters/{character_id}/aliases",
        headers=auth_headers(access_token),
    )
    assert resp.status_code == 200, resp.text
    items = resp.json()["items"]
    ids = [item["id"] for item in items]
    names = [item["name"] for item in items]
    assert names == ["First", "Last"]
    assert ids[0] == str(first)
    assert ids[1] == str(last)
    assert "Hidden" not in names


def test_list_aliases_empty_for_no_aliases(
    client: TestClient,
    access_token: str,
    seeded_character_with_base: dict[str, Any],
) -> None:
    resp = client.get(
        f"/v1/characters/{seeded_character_with_base['id']}/aliases",
        headers=auth_headers(access_token),
    )
    assert resp.status_code == 200
    assert resp.json() == {"items": []}


def test_list_aliases_team_member_can_read(
    client: TestClient,
    access_token: str,
    second_access_token: str,
    second_user: dict[str, Any],
    seeded_character_with_base: dict[str, Any],
    database_url: str,
) -> None:
    """Team-wide read parity with `GET /v1/characters/{id}` — a teammate
    who can see the character can also see its alias list (the embedded
    `aliases` field on `CharacterDetail` already exposes them, so the
    standalone read should not 403)."""
    character_id = seeded_character_with_base["id"]
    asyncio.run(_insert_alias(database_url, character_id=character_id, name="Visible"))
    resp = client.get(
        f"/v1/characters/{character_id}/aliases",
        headers=auth_headers(second_access_token),
    )
    assert resp.status_code == 200, resp.text
    assert [a["name"] for a in resp.json()["items"]] == ["Visible"]


def test_list_aliases_unknown_character_404(
    client: TestClient,
    access_token: str,
    seeded_user: dict[str, Any],
) -> None:
    resp = client.get(
        f"/v1/characters/{uuid.uuid4()}/aliases",
        headers=auth_headers(access_token),
    )
    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "NOT_FOUND_CHARACTER"


# ---------------------------------------------------------------------------
# GET /v1/aliases/{id}
# ---------------------------------------------------------------------------


def test_get_alias_detail_carries_motion_count(
    client: TestClient,
    access_token: str,
    seeded_character_with_base: dict[str, Any],
    database_url: str,
) -> None:
    """Detail surface aggregates active motions under the alias."""
    character_id = seeded_character_with_base["id"]
    alias_id = asyncio.run(_insert_alias(database_url, character_id=character_id, name="Detail"))
    asyncio.run(_insert_motion_for_alias(database_url, alias_id=alias_id, name="m1"))
    asyncio.run(_insert_motion_for_alias(database_url, alias_id=alias_id, name="m2"))
    # A soft-deleted motion must NOT contribute to the count.
    deleted_motion = asyncio.run(
        _insert_motion_for_alias(database_url, alias_id=alias_id, name="m3-deleted")
    )

    async def _soft_delete_motion() -> None:
        engine = create_async_engine(database_url, future=True, isolation_level="AUTOCOMMIT")
        try:
            async with engine.connect() as conn:
                await conn.execute(
                    text("UPDATE motions SET deleted_at = now() WHERE id = :i"),
                    {"i": deleted_motion},
                )
        finally:
            await engine.dispose()

    asyncio.run(_soft_delete_motion())

    resp = client.get(f"/v1/aliases/{alias_id}", headers=auth_headers(access_token))
    assert resp.status_code == 200, resp.text
    payload = resp.json()["alias"]
    assert payload["id"] == str(alias_id)
    assert payload["name"] == "Detail"
    assert payload["motion_count"] == 2


def test_get_alias_unknown_id_404(
    client: TestClient,
    access_token: str,
    seeded_user: dict[str, Any],
) -> None:
    resp = client.get(f"/v1/aliases/{uuid.uuid4()}", headers=auth_headers(access_token))
    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "NOT_FOUND_ALIAS"


def test_get_alias_soft_deleted_404(
    client: TestClient,
    access_token: str,
    seeded_character_with_base: dict[str, Any],
    database_url: str,
) -> None:
    alias_id = asyncio.run(
        _insert_alias(
            database_url,
            character_id=seeded_character_with_base["id"],
            name="Gone",
            deleted=True,
        )
    )
    resp = client.get(f"/v1/aliases/{alias_id}", headers=auth_headers(access_token))
    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "NOT_FOUND_ALIAS"


def test_get_alias_team_member_can_read(
    client: TestClient,
    second_access_token: str,
    second_user: dict[str, Any],
    seeded_character_with_base: dict[str, Any],
    database_url: str,
) -> None:
    """Team-wide read: a teammate can fetch alias detail. Owner-only is
    enforced on PATCH/DELETE (covered separately)."""
    alias_id = asyncio.run(
        _insert_alias(
            database_url,
            character_id=seeded_character_with_base["id"],
            name="OwnedByAlice",
        )
    )
    resp = client.get(f"/v1/aliases/{alias_id}", headers=auth_headers(second_access_token))
    assert resp.status_code == 200
    assert resp.json()["alias"]["name"] == "OwnedByAlice"


# ---------------------------------------------------------------------------
# PATCH /v1/aliases/{id}
# ---------------------------------------------------------------------------


def test_patch_alias_rename_happy(
    client: TestClient,
    access_token: str,
    seeded_character_with_base: dict[str, Any],
    database_url: str,
) -> None:
    alias_id = asyncio.run(
        _insert_alias(database_url, character_id=seeded_character_with_base["id"], name="OldName")
    )
    resp = client.patch(
        f"/v1/aliases/{alias_id}",
        json={"name": "NewName"},
        headers=auth_headers(access_token),
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["alias"]["name"] == "NewName"


def test_patch_alias_duplicate_name_409(
    client: TestClient,
    access_token: str,
    seeded_character_with_base: dict[str, Any],
    database_url: str,
) -> None:
    """Renaming to a name held by a sibling alias under the same
    character → 409 CONFLICT_DUPLICATE_NAME."""
    character_id = seeded_character_with_base["id"]
    asyncio.run(_insert_alias(database_url, character_id=character_id, name="Taken"))
    other_id = asyncio.run(_insert_alias(database_url, character_id=character_id, name="Free"))

    resp = client.patch(
        f"/v1/aliases/{other_id}",
        json={"name": "Taken"},
        headers=auth_headers(access_token),
    )
    assert resp.status_code == 409
    assert resp.json()["error"]["code"] == "CONFLICT_DUPLICATE_NAME"


def test_patch_alias_invalid_chars_400(
    client: TestClient,
    access_token: str,
    seeded_character_with_base: dict[str, Any],
    database_url: str,
) -> None:
    alias_id = asyncio.run(
        _insert_alias(database_url, character_id=seeded_character_with_base["id"], name="Original")
    )
    resp = client.patch(
        f"/v1/aliases/{alias_id}",
        json={"name": "has spaces"},
        headers=auth_headers(access_token),
    )
    assert resp.status_code == 400
    assert resp.json()["error"]["code"] == "VALIDATION_INVALID_CHARS"


def test_patch_alias_no_op_rename_succeeds(
    client: TestClient,
    access_token: str,
    seeded_character_with_base: dict[str, Any],
    database_url: str,
) -> None:
    """Renaming to the current name short-circuits the duplicate probe
    so the owner can PATCH idempotently (mirrors character rename)."""
    alias_id = asyncio.run(
        _insert_alias(database_url, character_id=seeded_character_with_base["id"], name="Same")
    )
    resp = client.patch(
        f"/v1/aliases/{alias_id}",
        json={"name": "Same"},
        headers=auth_headers(access_token),
    )
    assert resp.status_code == 200
    assert resp.json()["alias"]["name"] == "Same"


def test_patch_alias_soft_deleted_404(
    client: TestClient,
    access_token: str,
    seeded_character_with_base: dict[str, Any],
    database_url: str,
) -> None:
    alias_id = asyncio.run(
        _insert_alias(
            database_url,
            character_id=seeded_character_with_base["id"],
            name="Gone",
            deleted=True,
        )
    )
    resp = client.patch(
        f"/v1/aliases/{alias_id}",
        json={"name": "NewName"},
        headers=auth_headers(access_token),
    )
    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "NOT_FOUND_ALIAS"


def test_patch_alias_non_owner_403(
    client: TestClient,
    second_access_token: str,
    second_user: dict[str, Any],
    seeded_character_with_base: dict[str, Any],
    database_url: str,
) -> None:
    alias_id = asyncio.run(
        _insert_alias(
            database_url,
            character_id=seeded_character_with_base["id"],
            name="Alice",
        )
    )
    resp = client.patch(
        f"/v1/aliases/{alias_id}",
        json={"name": "Bobby"},
        headers=auth_headers(second_access_token),
    )
    assert resp.status_code == 403
    assert resp.json()["error"]["code"] == "AUTH_INSUFFICIENT_PERMISSION"


# ---------------------------------------------------------------------------
# DELETE /v1/aliases/{id}
# ---------------------------------------------------------------------------


def test_delete_alias_cascade_soft_deletes_motions(
    client: TestClient,
    access_token: str,
    seeded_character_with_base: dict[str, Any],
    database_url: str,
) -> None:
    """DELETE stamps `deleted_at` on the alias AND every active motion
    bound to it (per F-12)."""
    character_id = seeded_character_with_base["id"]
    alias_id = asyncio.run(_insert_alias(database_url, character_id=character_id, name="ToDelete"))
    motion_a = asyncio.run(_insert_motion_for_alias(database_url, alias_id=alias_id, name="ma"))
    motion_b = asyncio.run(_insert_motion_for_alias(database_url, alias_id=alias_id, name="mb"))

    resp = client.delete(f"/v1/aliases/{alias_id}", headers=auth_headers(access_token))
    assert resp.status_code == 204
    assert resp.text == ""

    assert asyncio.run(_row_deleted_at(database_url, "aliases", alias_id)) is not None
    assert asyncio.run(_row_deleted_at(database_url, "motions", motion_a)) is not None
    assert asyncio.run(_row_deleted_at(database_url, "motions", motion_b)) is not None


def test_delete_alias_idempotent_404_on_repeat(
    client: TestClient,
    access_token: str,
    seeded_character_with_base: dict[str, Any],
    database_url: str,
) -> None:
    """A second DELETE on the same id sees a soft-deleted row → 404
    NOT_FOUND_ALIAS (no restore endpoint in Phase 1)."""
    alias_id = asyncio.run(
        _insert_alias(database_url, character_id=seeded_character_with_base["id"], name="Once")
    )
    first = client.delete(f"/v1/aliases/{alias_id}", headers=auth_headers(access_token))
    assert first.status_code == 204

    second = client.delete(f"/v1/aliases/{alias_id}", headers=auth_headers(access_token))
    assert second.status_code == 404
    assert second.json()["error"]["code"] == "NOT_FOUND_ALIAS"


def test_delete_alias_non_owner_403(
    client: TestClient,
    second_access_token: str,
    second_user: dict[str, Any],
    seeded_character_with_base: dict[str, Any],
    database_url: str,
) -> None:
    alias_id = asyncio.run(
        _insert_alias(database_url, character_id=seeded_character_with_base["id"], name="Alice")
    )
    resp = client.delete(f"/v1/aliases/{alias_id}", headers=auth_headers(second_access_token))
    assert resp.status_code == 403
    assert resp.json()["error"]["code"] == "AUTH_INSUFFICIENT_PERMISSION"

    # The alias must still be active — the 403 must not have stamped
    # `deleted_at` as a side effect.
    assert asyncio.run(_row_deleted_at(database_url, "aliases", alias_id)) is None


# ---------------------------------------------------------------------------
# CharacterDetail.aliases excludes soft-deleted
# ---------------------------------------------------------------------------


def test_character_detail_excludes_deleted_aliases(
    client: TestClient,
    access_token: str,
    seeded_character_with_base: dict[str, Any],
    database_url: str,
) -> None:
    character_id = seeded_character_with_base["id"]
    asyncio.run(_insert_alias(database_url, character_id=character_id, name="Visible"))
    asyncio.run(_insert_alias(database_url, character_id=character_id, name="Hidden", deleted=True))

    resp = client.get(
        f"/v1/characters/{character_id}",
        headers=auth_headers(access_token),
    )
    assert resp.status_code == 200, resp.text
    aliases = resp.json()["character"]["aliases"]
    names = [a["name"] for a in aliases]
    assert names == ["Visible"]
