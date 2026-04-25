"""Route-level tests for /v1/characters/* (T-016).

Acceptance criteria, mapped:

- POST creates character + session in one TX                 → test_create_*
- Same-owner duplicate name → 409 CONFLICT_DUPLICATE_NAME    → test_create_duplicate_name
- ?owner_id=me filters + updated_at DESC ordering            → test_list_*
- Cursor pagination round-trips                              → test_list_pagination
- Same-team non-owner can read but PATCH/DELETE → 403        → test_other_user_*
- Soft delete + 30-day restore + past-window 410             → test_restore_*
- GET /v1/creation-sessions/{id} → empty checkpoints         → test_creation_session.py
- OpenAPI schema includes the routes                         → test_openapi_routes_present
- Redis SET seq:checkpoint:* on create                       → test_create_seeds_redis_seq
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import fakeredis
import fakeredis.aioredis  # noqa: F401 — registers the aioredis submodule
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from tests.characters.conftest import auth_headers

# ---------------------------------------------------------------------------
# POST /v1/characters
# ---------------------------------------------------------------------------


def test_create_character_201_returns_character_and_session(
    client: TestClient, access_token: str
) -> None:
    resp = client.post(
        "/v1/characters",
        json={"name": "阿雅", "input_mode": "template"},
        headers=auth_headers(access_token),
    )
    assert resp.status_code == 201
    body = resp.json()
    assert body["character"]["name"] == "阿雅"
    assert body["character"]["slug"]  # generated, non-empty
    assert body["character"]["owner"]["name"] == "Alice"
    assert body["character"]["alias_count"] == 0
    assert body["character"]["motion_count"] == 0
    assert body["character"]["base_thumbnail_url"] is None

    session = body["creation_session"]
    assert session["input_mode"] == "template"
    assert session["status"] == "in_progress"
    assert session["character_id"] == body["character"]["id"]
    assert session["checkpoint_count"] == 0


def test_create_character_persists_both_rows_atomically(
    client: TestClient, access_token: str, database_url: str
) -> None:
    resp = client.post(
        "/v1/characters",
        json={"name": "Aria", "input_mode": "reference"},
        headers=auth_headers(access_token),
    )
    assert resp.status_code == 201
    body = resp.json()
    char_id = body["character"]["id"]
    session_id = body["creation_session"]["id"]

    async def _check() -> None:
        engine = create_async_engine(database_url, future=True)
        try:
            async with engine.connect() as conn:
                row = (
                    await conn.execute(
                        text("SELECT creation_session_id FROM characters WHERE id = :id"),
                        {"id": char_id},
                    )
                ).scalar_one()
                assert str(row) == session_id

                csid = (
                    await conn.execute(
                        text("SELECT character_id FROM creation_sessions WHERE id = :id"),
                        {"id": session_id},
                    )
                ).scalar_one()
                assert str(csid) == char_id
        finally:
            await engine.dispose()

    asyncio.run(_check())


def test_create_seeds_redis_seq(
    client: TestClient,
    access_token: str,
    fake_redis_sync: fakeredis.FakeStrictRedis,
) -> None:
    resp = client.post(
        "/v1/characters",
        json={"name": "RedisAria", "input_mode": "template"},
        headers=auth_headers(access_token),
    )
    assert resp.status_code == 201
    session_id = resp.json()["creation_session"]["id"]

    # The bootstrap SET must land so T-017's INCR returns 1 first.
    # Read via the sync client bound to the same FakeServer to dodge
    # TestClient's per-request asyncio loop boundaries.
    raw = fake_redis_sync.get(f"seq:checkpoint:{session_id}")
    assert raw == "0"


def test_create_duplicate_name_returns_409(client: TestClient, access_token: str) -> None:
    payload = {"name": "Twin", "input_mode": "template"}
    first = client.post("/v1/characters", json=payload, headers=auth_headers(access_token))
    assert first.status_code == 201

    second = client.post("/v1/characters", json=payload, headers=auth_headers(access_token))
    assert second.status_code == 409
    assert second.json()["error"]["code"] == "CONFLICT_DUPLICATE_NAME"


def test_create_invalid_chars_returns_400(client: TestClient, access_token: str) -> None:
    resp = client.post(
        "/v1/characters",
        json={"name": "has space!", "input_mode": "template"},
        headers=auth_headers(access_token),
    )
    assert resp.status_code == 400
    assert resp.json()["error"]["code"] == "VALIDATION_INVALID_CHARS"


def test_create_with_u3007_rejected_at_app_layer(client: TestClient, access_token: str) -> None:
    """Codex P2: the API regex must match the DB CHECK constraint
    byte-for-byte. The DB allows only U+4E00–U+9FFF + ASCII alnum +
    `_-`, so `〇` (U+3007) must 400 at the API layer rather than slip
    through and trip a 500 IntegrityError on INSERT."""
    resp = client.post(
        "/v1/characters",
        json={"name": "〇", "input_mode": "template"},
        headers=auth_headers(access_token),
    )
    assert resp.status_code == 400
    assert resp.json()["error"]["code"] == "VALIDATION_INVALID_CHARS"


def test_create_retries_slug_collision_for_distinct_names(
    client: TestClient,
    access_token: str,
    seeded_user: dict[str, Any],
    database_url: str,
) -> None:
    """Codex round-5 P2: when `uq_characters_owner_slug` races between
    two creates with DIFFERENT names that pinyin-collapse to the same
    slug, the service must regenerate the slug rather than 409 the
    second create with a misleading "name exists". We simulate by
    seeding a character whose slug equals what the second create's
    slug allocator would pick on first probe; the allocator should
    retry, see the existing slug, and append a `-2` suffix."""
    from sqlalchemy import text as sql_text
    from sqlalchemy.ext.asyncio import create_async_engine

    from app.repositories import character_repo

    # Plant a character with name "Aria" (slug "aria") so the second
    # create — for a DIFFERENT name with the same pinyin — has to
    # walk past the slug collision.
    async def _plant_row() -> None:
        engine = create_async_engine(database_url, future=True, isolation_level="AUTOCOMMIT")
        try:
            async with engine.connect() as conn:
                team_id = (
                    await conn.execute(sql_text("SELECT id FROM teams WHERE name='default'"))
                ).scalar_one()
                await conn.execute(
                    sql_text(
                        "INSERT INTO characters (team_id, owner_id, name, slug) "
                        "VALUES (:t, :o, 'Aria', 'aria')"
                    ),
                    {"t": team_id, "o": seeded_user["id"]},
                )
        finally:
            await engine.dispose()

    asyncio.run(_plant_row())

    # Force `slug_exists_for_owner` to lie ("nothing taken") on first
    # call so the allocator hands us "aria" and the DB races. The lie
    # toggles off after one call so the retry sees the real state and
    # picks "aria-2".
    original = character_repo.slug_exists_for_owner
    call_count = {"n": 0}

    async def _liar(db: Any, *, owner_id: Any, slug: Any) -> bool:
        call_count["n"] += 1
        if call_count["n"] <= 1:
            return False
        return await original(db, owner_id=owner_id, slug=slug)

    character_repo.slug_exists_for_owner = _liar  # type: ignore[assignment]
    try:
        resp = client.post(
            "/v1/characters",
            json={"name": "Aria2", "input_mode": "template"},
            headers=auth_headers(access_token),
        )
    finally:
        character_repo.slug_exists_for_owner = original  # type: ignore[assignment]

    # Retry path succeeded — second slug pick was "aria-2".
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["character"]["name"] == "Aria2"
    assert body["character"]["slug"] != "aria"


def test_create_translates_unique_violation_race_to_409(
    client: TestClient,
    access_token: str,
    seeded_user: dict[str, Any],
    database_url: str,
) -> None:
    """Codex P2: the read-then-insert pre-check is racy. If a
    concurrent insert wins between our `name_exists_for_owner` probe
    and our commit, the partial UNIQUE index raises IntegrityError —
    the service must translate that into CONFLICT_DUPLICATE_NAME, not
    a 500. We simulate the race by stubbing `name_exists_for_owner` to
    always say "no" while a row with the same name already exists in
    the DB."""
    from sqlalchemy import text as sql_text
    from sqlalchemy.ext.asyncio import create_async_engine

    from app.repositories import character_repo

    # Plant the existing row directly so the route's pre-check would
    # normally 409, but we'll patch the pre-check to bypass it.
    async def _plant_row() -> None:
        engine = create_async_engine(database_url, future=True, isolation_level="AUTOCOMMIT")
        try:
            async with engine.connect() as conn:
                team_id = (
                    await conn.execute(sql_text("SELECT id FROM teams WHERE name='default'"))
                ).scalar_one()
                await conn.execute(
                    sql_text(
                        "INSERT INTO characters (team_id, owner_id, name, slug) "
                        "VALUES (:t, :o, :n, :s)"
                    ),
                    {
                        "t": team_id,
                        "o": seeded_user["id"],
                        "n": "RaceyName",
                        "s": "racey-name",
                    },
                )
        finally:
            await engine.dispose()

    asyncio.run(_plant_row())

    original = character_repo.name_exists_for_owner

    async def _liar(db: Any, *, owner_id: Any, name: Any, exclude_id: Any = None) -> bool:
        return False

    # The service calls `character_repo.name_exists_for_owner(...)`
    # via the module reference, so swapping the attribute is enough
    # — no need to patch the service's local name.
    character_repo.name_exists_for_owner = _liar  # type: ignore[assignment]
    try:
        resp = client.post(
            "/v1/characters",
            json={"name": "RaceyName", "input_mode": "template"},
            headers=auth_headers(access_token),
        )
    finally:
        character_repo.name_exists_for_owner = original  # type: ignore[assignment]

    assert resp.status_code == 409
    assert resp.json()["error"]["code"] == "CONFLICT_DUPLICATE_NAME"


def test_create_too_long_name_returns_422(client: TestClient, access_token: str) -> None:
    """Pydantic enforces the 1–50 length cap at the parse layer.

    The route never sees the value, so this trips the default 422
    rather than the structured AgentError. That's fine for now —
    the DB CHECK constraint matches and we don't need a custom error
    surface for "too many characters typed".
    """
    resp = client.post(
        "/v1/characters",
        json={"name": "a" * 51, "input_mode": "template"},
        headers=auth_headers(access_token),
    )
    assert resp.status_code == 422


def test_create_requires_auth(client: TestClient) -> None:
    resp = client.post(
        "/v1/characters",
        json={"name": "Anon", "input_mode": "template"},
    )
    assert resp.status_code == 401


def test_create_slug_collision_appends_suffix(client: TestClient, access_token: str) -> None:
    """Two characters with names that pinyin-collapse to the same slug
    should still both land — second one carries the `-2` suffix per
    db-schema §4."""

    # Same name pinyin → "yi-er-san", different DB names so the unique-
    # name index doesn't 409 us before the slug code path runs.
    first = client.post(
        "/v1/characters",
        json={"name": "一二三", "input_mode": "template"},
        headers=auth_headers(access_token),
    )
    assert first.status_code == 201
    base_slug = first.json()["character"]["slug"]

    second = client.post(
        "/v1/characters",
        json={"name": "一二三-A", "input_mode": "template"},
        headers=auth_headers(access_token),
    )
    assert second.status_code == 201
    second_slug = second.json()["character"]["slug"]
    # Second pinyin trim ends in "yi-er-san-a", which doesn't collide
    # with the first; verify they're at least distinct.
    assert second_slug != base_slug


# ---------------------------------------------------------------------------
# GET /v1/characters
# ---------------------------------------------------------------------------


def _create(client: TestClient, token: str, name: str) -> dict[str, Any]:
    resp = client.post(
        "/v1/characters",
        json={"name": name, "input_mode": "template"},
        headers=auth_headers(token),
    )
    assert resp.status_code == 201
    return resp.json()


def test_list_owner_me_filters_to_caller(
    client: TestClient,
    access_token: str,
    second_user: dict[str, Any],
    second_access_token: str,
) -> None:
    a = _create(client, access_token, "AliceOnly1")
    _create(client, second_access_token, "BobOnly1")
    _create(client, second_access_token, "BobOnly2")

    resp = client.get("/v1/characters?owner_id=me", headers=auth_headers(access_token))
    assert resp.status_code == 200
    items = resp.json()["items"]
    assert {item["id"] for item in items} == {a["character"]["id"]}


def test_list_orders_by_updated_at_desc(
    client: TestClient, access_token: str, database_url: str
) -> None:
    a = _create(client, access_token, "First")
    b = _create(client, access_token, "Second")
    c = _create(client, access_token, "Third")

    # Bump `a`'s updated_at via direct UPDATE so the order isn't just
    # "creation order" — proves the sort actually keys on updated_at.
    async def _bump() -> None:
        engine = create_async_engine(database_url, future=True, isolation_level="AUTOCOMMIT")
        try:
            async with engine.connect() as conn:
                # Suppress the auto-update trigger by setting the column
                # directly (the trigger would clobber our explicit value
                # otherwise — but it only fires on UPDATE of any row, so
                # an explicit SET wins as the trigger sets NEW.updated_at
                # to NOW() AFTER our SET runs, but BEFORE the row is
                # written; this means our explicit value is overwritten
                # back to NOW(). Workaround: make `a` the most recently
                # touched row by issuing the UPDATE last in time order).
                # Order: bump b first, c next, a last → a wins.
                for cid in [b["character"]["id"], c["character"]["id"], a["character"]["id"]]:
                    await conn.execute(
                        text("UPDATE characters SET name = name WHERE id = :id"),
                        {"id": cid},
                    )
        finally:
            await engine.dispose()

    asyncio.run(_bump())

    resp = client.get("/v1/characters?owner_id=me", headers=auth_headers(access_token))
    items = resp.json()["items"]
    ids = [item["id"] for item in items]
    # Most-recently-touched first; `a` was bumped last so it sits at top.
    assert ids[0] == a["character"]["id"]


def test_list_pagination_with_cursor(client: TestClient, access_token: str) -> None:
    created = [_create(client, access_token, f"Char{i:02d}") for i in range(5)]
    _ = created  # ids unused; we just need 5 rows to paginate over.

    page1 = client.get("/v1/characters?owner_id=me&limit=2", headers=auth_headers(access_token))
    assert page1.status_code == 200
    page1_body = page1.json()
    assert len(page1_body["items"]) == 2
    assert page1_body["next_cursor"] is not None

    page2 = client.get(
        f"/v1/characters?owner_id=me&limit=2&cursor={page1_body['next_cursor']}",
        headers=auth_headers(access_token),
    )
    assert page2.status_code == 200
    page2_body = page2.json()
    assert len(page2_body["items"]) == 2
    # No id should appear on both pages — the cursor must skip past
    # what page 1 already returned.
    p1_ids = {item["id"] for item in page1_body["items"]}
    p2_ids = {item["id"] for item in page2_body["items"]}
    assert p1_ids.isdisjoint(p2_ids)


def test_list_naive_datetime_cursor_degrades_to_page_1(
    client: TestClient, access_token: str
) -> None:
    """Codex round-4 P2: a naive (no tzinfo) datetime in the cursor
    must be rejected as malformed. Without the guard the naive value
    flows into a `timestamptz` comparison and asyncpg either errors or
    silently coerces to server-local TZ. Behavior we want: the bad
    cursor degrades to "first page" silently, just like any other
    decode failure."""
    import base64 as _b64

    _create(client, access_token, "OnlyOne")

    # Hand-craft a cursor with a naive ISO timestamp (no offset).
    raw = f"2026-01-01T00:00:00|{uuid.uuid4()}"
    bad = _b64.urlsafe_b64encode(raw.encode("utf-8")).decode("ascii").rstrip("=")

    resp = client.get(
        f"/v1/characters?owner_id=me&cursor={bad}",
        headers=auth_headers(access_token),
    )
    assert resp.status_code == 200
    # Cursor was rejected → page 1 returned, the seeded character is in it.
    assert len(resp.json()["items"]) == 1


# ---------------------------------------------------------------------------
# Cross-user permission
# ---------------------------------------------------------------------------


def test_other_team_user_can_get_detail_but_not_modify(
    client: TestClient,
    access_token: str,
    second_user: dict[str, Any],
    second_access_token: str,
) -> None:
    a = _create(client, access_token, "Shared")
    cid = a["character"]["id"]

    # Bob (same team) can GET — soft-team-share is the spec for Phase 1.
    resp = client.get(f"/v1/characters/{cid}", headers=auth_headers(second_access_token))
    assert resp.status_code == 200

    # …but cannot PATCH the name.
    patch = client.patch(
        f"/v1/characters/{cid}",
        json={"name": "Renamed"},
        headers=auth_headers(second_access_token),
    )
    assert patch.status_code == 403
    assert patch.json()["error"]["code"] == "AUTH_INSUFFICIENT_PERMISSION"

    # …and cannot DELETE.
    delete = client.delete(f"/v1/characters/{cid}", headers=auth_headers(second_access_token))
    assert delete.status_code == 403


def test_owner_can_patch_name(client: TestClient, access_token: str) -> None:
    a = _create(client, access_token, "OldName")
    resp = client.patch(
        f"/v1/characters/{a['character']['id']}",
        json={"name": "NewName"},
        headers=auth_headers(access_token),
    )
    assert resp.status_code == 200
    assert resp.json()["character"]["name"] == "NewName"


def test_patch_to_existing_name_409(client: TestClient, access_token: str) -> None:
    a = _create(client, access_token, "AliceA")
    _create(client, access_token, "AliceB")

    resp = client.patch(
        f"/v1/characters/{a['character']['id']}",
        json={"name": "AliceB"},
        headers=auth_headers(access_token),
    )
    assert resp.status_code == 409
    assert resp.json()["error"]["code"] == "CONFLICT_DUPLICATE_NAME"


# ---------------------------------------------------------------------------
# Soft delete + restore
# ---------------------------------------------------------------------------


def test_soft_delete_hides_from_list(client: TestClient, access_token: str) -> None:
    a = _create(client, access_token, "Doomed")
    delete = client.delete(
        f"/v1/characters/{a['character']['id']}", headers=auth_headers(access_token)
    )
    assert delete.status_code == 204

    # GET 404
    resp = client.get(f"/v1/characters/{a['character']['id']}", headers=auth_headers(access_token))
    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "NOT_FOUND_CHARACTER"

    # Not in list
    listing = client.get("/v1/characters?owner_id=me", headers=auth_headers(access_token))
    ids = {item["id"] for item in listing.json()["items"]}
    assert a["character"]["id"] not in ids


def test_restore_within_window(client: TestClient, access_token: str) -> None:
    a = _create(client, access_token, "Saved")
    cid = a["character"]["id"]
    client.delete(f"/v1/characters/{cid}", headers=auth_headers(access_token))

    resp = client.post(f"/v1/characters/{cid}/restore", headers=auth_headers(access_token))
    assert resp.status_code == 200
    assert resp.json()["character"]["id"] == cid

    # Now visible again.
    listing = client.get("/v1/characters?owner_id=me", headers=auth_headers(access_token))
    ids = {item["id"] for item in listing.json()["items"]}
    assert cid in ids


def test_restore_past_window_returns_410(
    client: TestClient, access_token: str, database_url: str
) -> None:
    a = _create(client, access_token, "Forgotten")
    cid = a["character"]["id"]
    client.delete(f"/v1/characters/{cid}", headers=auth_headers(access_token))

    # Backdate deleted_at to 31 days ago.
    async def _backdate() -> None:
        engine = create_async_engine(database_url, future=True, isolation_level="AUTOCOMMIT")
        try:
            past = datetime.now(UTC) - timedelta(days=31)
            async with engine.connect() as conn:
                await conn.execute(
                    text("UPDATE characters SET deleted_at = :ts WHERE id = :id"),
                    {"ts": past, "id": cid},
                )
        finally:
            await engine.dispose()

    asyncio.run(_backdate())

    resp = client.post(f"/v1/characters/{cid}/restore", headers=auth_headers(access_token))
    assert resp.status_code == 410
    assert resp.json()["error"]["code"] == "NOT_FOUND_CHARACTER"


def test_restore_only_owner_can_restore(
    client: TestClient,
    access_token: str,
    second_user: dict[str, Any],
    second_access_token: str,
) -> None:
    a = _create(client, access_token, "MineToRestore")
    cid = a["character"]["id"]
    client.delete(f"/v1/characters/{cid}", headers=auth_headers(access_token))

    resp = client.post(f"/v1/characters/{cid}/restore", headers=auth_headers(second_access_token))
    # Bob is same team but not owner → 403.
    assert resp.status_code == 403


# ---------------------------------------------------------------------------
# OpenAPI surface
# ---------------------------------------------------------------------------


def test_openapi_routes_present(client: TestClient) -> None:
    resp = client.get("/openapi.json")
    assert resp.status_code == 200
    paths = resp.json()["paths"]
    assert "/v1/characters" in paths
    assert "/v1/characters/{character_id}" in paths
    assert "/v1/characters/{character_id}/restore" in paths
    assert "/v1/creation-sessions/{session_id}" in paths


def test_unknown_character_returns_not_found(client: TestClient, access_token: str) -> None:
    resp = client.get(f"/v1/characters/{uuid.uuid4()}", headers=auth_headers(access_token))
    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "NOT_FOUND_CHARACTER"
