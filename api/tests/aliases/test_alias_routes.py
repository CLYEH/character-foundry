"""Route-level tests for `POST /v1/characters/{id}/aliases` + mask upload (T-031)."""

from __future__ import annotations

import uuid
from typing import Any

from fastapi.testclient import TestClient

from tests.aliases.conftest import _png_bytes, auth_headers


# ---------------------------------------------------------------------------
# Mask upload
# ---------------------------------------------------------------------------


def test_upload_alias_mask_happy_path(
    client: TestClient,
    access_token: str,
    seeded_character_with_base: dict[str, Any],
) -> None:
    character_id = seeded_character_with_base["id"]
    files = {"file": ("mask.png", _png_bytes(), "image/png")}
    resp = client.post(
        f"/v1/characters/{character_id}/aliases/masks",
        files=files,
        headers=auth_headers(access_token),
    )
    assert resp.status_code == 201, resp.text
    payload = resp.json()
    uuid.UUID(payload["mask_id"])
    assert payload["url"].startswith("/storage/")


def test_upload_alias_mask_rejects_non_png(
    client: TestClient,
    access_token: str,
    seeded_character_with_base: dict[str, Any],
) -> None:
    """Mask must be PNG — JPEG drops alpha which the inpaint convention requires."""
    character_id = seeded_character_with_base["id"]
    files = {"file": ("mask.jpg", b"fake-jpeg-bytes", "image/jpeg")}
    resp = client.post(
        f"/v1/characters/{character_id}/aliases/masks",
        files=files,
        headers=auth_headers(access_token),
    )
    assert resp.status_code == 400
    assert resp.json()["error"]["code"] == "VALIDATION_REFERENCE_IMAGE_TYPE"


def test_upload_alias_mask_rejects_non_owner(
    client: TestClient,
    seeded_character_with_base: dict[str, Any],
    second_user: dict[str, Any],
    second_access_token: str,
) -> None:
    """Same team but not the character owner — write paths are owner-only."""
    character_id = seeded_character_with_base["id"]
    files = {"file": ("mask.png", _png_bytes(), "image/png")}
    resp = client.post(
        f"/v1/characters/{character_id}/aliases/masks",
        files=files,
        headers=auth_headers(second_access_token),
    )
    assert resp.status_code == 403
    assert resp.json()["error"]["code"] == "AUTH_INSUFFICIENT_PERMISSION"


def test_upload_alias_mask_404_unknown_character(
    client: TestClient,
    access_token: str,
    seeded_user: dict[str, Any],
) -> None:
    files = {"file": ("mask.png", _png_bytes(), "image/png")}
    resp = client.post(
        f"/v1/characters/{uuid.uuid4()}/aliases/masks",
        files=files,
        headers=auth_headers(access_token),
    )
    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "NOT_FOUND_CHARACTER"


# ---------------------------------------------------------------------------
# Alias create — validation matrix
# ---------------------------------------------------------------------------


def _create_alias(
    client: TestClient,
    *,
    token: str,
    character_id: uuid.UUID,
    body: dict[str, Any],
) -> Any:
    return client.post(
        f"/v1/characters/{character_id}/aliases",
        json=body,
        headers=auth_headers(token),
    )


def test_create_alias_text_mode_happy(
    client: TestClient,
    access_token: str,
    seeded_character_with_base: dict[str, Any],
) -> None:
    """Text-only happy path — returns 202 with task_id + alias_id."""
    resp = _create_alias(
        client,
        token=access_token,
        character_id=seeded_character_with_base["id"],
        body={
            "name": "RedDress",
            "input_mode": "text",
            "freeform_note": "穿著紅色洋裝",
        },
    )
    assert resp.status_code == 202, resp.text
    payload = resp.json()
    uuid.UUID(payload["task_id"])
    uuid.UUID(payload["alias_id"])


def test_create_alias_empty_input_rejected(
    client: TestClient,
    access_token: str,
    seeded_character_with_base: dict[str, Any],
) -> None:
    """All three fields empty → 422 VALIDATION_EMPTY_INPUT."""
    resp = _create_alias(
        client,
        token=access_token,
        character_id=seeded_character_with_base["id"],
        body={
            "name": "Empty",
            "input_mode": "mixed",
            "freeform_note": "",
            "reference_image_ids": [],
        },
    )
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "VALIDATION_EMPTY_INPUT"


def test_create_alias_inpaint_without_mask_rejected(
    client: TestClient,
    access_token: str,
    seeded_character_with_base: dict[str, Any],
) -> None:
    """input_mode=inpaint but no mask → 422 mismatch."""
    resp = _create_alias(
        client,
        token=access_token,
        character_id=seeded_character_with_base["id"],
        body={
            "name": "InpaintNoMask",
            "input_mode": "inpaint",
            "freeform_note": "make it red",
        },
    )
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "VALIDATION_ALIAS_INPUT_MODE_MISMATCH"


def test_create_alias_image_without_refs_rejected(
    client: TestClient,
    access_token: str,
    seeded_character_with_base: dict[str, Any],
) -> None:
    resp = _create_alias(
        client,
        token=access_token,
        character_id=seeded_character_with_base["id"],
        body={
            "name": "ImageNoRefs",
            "input_mode": "image",
            "freeform_note": "use this style",
        },
    )
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "VALIDATION_ALIAS_INPUT_MODE_MISMATCH"


def test_create_alias_text_without_note_rejected(
    client: TestClient,
    access_token: str,
    seeded_character_with_base: dict[str, Any],
) -> None:
    resp = _create_alias(
        client,
        token=access_token,
        character_id=seeded_character_with_base["id"],
        body={
            "name": "TextEmpty",
            "input_mode": "text",
            "freeform_note": "   ",
            "reference_image_ids": [],
        },
    )
    # The empty-input check fires first because none of (note, refs, mask)
    # carry signal once `note` is whitespace-only — service-layer ordering
    # puts the empty check ahead of the per-mode matrix.
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "VALIDATION_EMPTY_INPUT"


def test_create_alias_duplicate_name_conflict(
    client: TestClient,
    access_token: str,
    seeded_character_with_base: dict[str, Any],
    database_url: str,
) -> None:
    """Same name twice (within a character) → 409 CONFLICT_DUPLICATE_NAME."""
    import asyncio

    from sqlalchemy import text
    from sqlalchemy.ext.asyncio import create_async_engine

    # Pre-seed an alias with the name we're about to retry. Using SQL
    # rather than the API so the duplicate trigger is independent of
    # any side effect from the create call (no worker run, no race).
    async def _seed() -> None:
        engine = create_async_engine(
            database_url, future=True, isolation_level="AUTOCOMMIT"
        )
        try:
            async with engine.connect() as conn:
                await conn.execute(
                    text(
                        "INSERT INTO aliases "
                        "(character_id, name, prompt, input_mode, image_key) "
                        "VALUES (:c, 'Existing', 'p', 'text2image', 'aliases/x.png')"
                    ),
                    {"c": seeded_character_with_base["id"]},
                )
        finally:
            await engine.dispose()

    asyncio.run(_seed())

    resp = _create_alias(
        client,
        token=access_token,
        character_id=seeded_character_with_base["id"],
        body={
            "name": "Existing",
            "input_mode": "text",
            "freeform_note": "different freeform",
        },
    )
    assert resp.status_code == 409
    assert resp.json()["error"]["code"] == "CONFLICT_DUPLICATE_NAME"


def test_create_alias_no_base_returns_409(
    client: TestClient,
    access_token: str,
    character_without_base: dict[str, Any],
) -> None:
    """Character without a Base → 409 CONFLICT_BASE_NOT_SET."""
    resp = _create_alias(
        client,
        token=access_token,
        character_id=character_without_base["id"],
        body={
            "name": "NoBase",
            "input_mode": "text",
            "freeform_note": "anything",
        },
    )
    assert resp.status_code == 409
    assert resp.json()["error"]["code"] == "CONFLICT_BASE_NOT_SET"


def test_create_alias_non_owner_returns_403(
    client: TestClient,
    second_access_token: str,
    second_user: dict[str, Any],
    seeded_character_with_base: dict[str, Any],
) -> None:
    """Bob (same team) tries to create an alias on Alice's character → 403."""
    resp = _create_alias(
        client,
        token=second_access_token,
        character_id=seeded_character_with_base["id"],
        body={
            "name": "BobsAttempt",
            "input_mode": "text",
            "freeform_note": "should fail",
        },
    )
    assert resp.status_code == 403
    assert resp.json()["error"]["code"] == "AUTH_INSUFFICIENT_PERMISSION"


def test_create_alias_unknown_character_404(
    client: TestClient,
    access_token: str,
    seeded_user: dict[str, Any],
) -> None:
    resp = _create_alias(
        client,
        token=access_token,
        character_id=uuid.uuid4(),
        body={
            "name": "Whatever",
            "input_mode": "text",
            "freeform_note": "no character here",
        },
    )
    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "NOT_FOUND_CHARACTER"


def test_create_alias_unknown_mask_404(
    client: TestClient,
    access_token: str,
    seeded_character_with_base: dict[str, Any],
) -> None:
    """mask_id refers to a row that doesn't exist (or belongs to another
    character) → 404 NOT_FOUND_MASK."""
    resp = _create_alias(
        client,
        token=access_token,
        character_id=seeded_character_with_base["id"],
        body={
            "name": "BadMask",
            "input_mode": "inpaint",
            "mask": {"mask_id": str(uuid.uuid4())},
        },
    )
    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "NOT_FOUND_MASK"


def test_create_alias_inpaint_with_uploaded_mask_happy(
    client: TestClient,
    access_token: str,
    seeded_character_with_base: dict[str, Any],
) -> None:
    """Upload a mask via the route, then reference it in alias create.
    End-to-end of the upload-then-reference contract."""
    character_id = seeded_character_with_base["id"]

    # Upload mask first.
    files = {"file": ("mask.png", _png_bytes(), "image/png")}
    upload = client.post(
        f"/v1/characters/{character_id}/aliases/masks",
        files=files,
        headers=auth_headers(access_token),
    )
    assert upload.status_code == 201, upload.text
    mask_id = upload.json()["mask_id"]

    # Now create the alias referring to it.
    resp = _create_alias(
        client,
        token=access_token,
        character_id=character_id,
        body={
            "name": "InpaintAlias",
            "input_mode": "inpaint",
            "mask": {"mask_id": mask_id},
        },
    )
    assert resp.status_code == 202, resp.text
    uuid.UUID(resp.json()["task_id"])
    uuid.UUID(resp.json()["alias_id"])
