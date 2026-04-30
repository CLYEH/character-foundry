"""Route-level tests for `POST /v1/bases/{id}/motions` +
`POST /v1/aliases/{id}/motions` (T-033)."""

from __future__ import annotations

import uuid
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient

from tests.motions.conftest import (
    auth_headers,
    create_character_via_api,
    seed_alias_for_character,
    seed_base_for_character,
    seed_motion_row,
)
from tests.tasks.conftest import FakeArqPool


def _post_base_motion(
    client: TestClient,
    token: str,
    base_id: str,
    *,
    motion_type: str = "preset_wave",
    name: str = "招手",
    description: str | None = None,
) -> Any:
    body: dict[str, Any] = {"motion_type": motion_type, "name": name}
    if description is not None:
        body["description"] = description
    return client.post(
        f"/v1/bases/{base_id}/motions",
        json=body,
        headers=auth_headers(token),
    )


def _post_alias_motion(
    client: TestClient,
    token: str,
    alias_id: str,
    *,
    motion_type: str = "custom",
    name: str = "alias_motion",
    description: str | None = None,
) -> Any:
    body: dict[str, Any] = {"motion_type": motion_type, "name": name}
    if description is not None:
        body["description"] = description
    return client.post(
        f"/v1/aliases/{alias_id}/motions",
        json=body,
        headers=auth_headers(token),
    )


def test_post_base_preset_motion_returns_202_with_task_and_motion_ids(
    client: TestClient,
    access_token: str,
    database_url: str,
    storage_root: Path,
    fake_arq_pool: FakeArqPool,
) -> None:
    body = create_character_via_api(client, access_token, name="WaveChar")
    char_id = body["character"]["id"]
    session_id = body["creation_session"]["id"]

    base_id, _ = seed_base_for_character(
        database_url,
        character_id=char_id,
        creation_session_id=session_id,
        storage_root=storage_root,
    )

    resp = _post_base_motion(client, access_token, base_id, motion_type="preset_wave", name="招手")
    assert resp.status_code == 202, resp.text
    payload = resp.json()
    assert "task_id" in payload
    assert "motion_id" in payload
    # task got enqueued via the fake pool with the run_create_motion handler.
    assert any(call[0] == "run_create_motion" for call in fake_arq_pool.enqueued)


def test_post_alias_custom_motion_returns_202(
    client: TestClient,
    access_token: str,
    database_url: str,
    storage_root: Path,
) -> None:
    body = create_character_via_api(client, access_token, name="AliasMotionChar")
    char_id = body["character"]["id"]

    alias_id, _ = seed_alias_for_character(
        database_url,
        character_id=char_id,
        storage_root=storage_root,
    )

    resp = _post_alias_motion(
        client,
        access_token,
        alias_id,
        motion_type="custom",
        name="dance",
        description="跳一段歡快的舞蹈",
    )
    assert resp.status_code == 202, resp.text


def test_post_preset_motion_duplicate_returns_409_preset_already_exists(
    client: TestClient,
    access_token: str,
    database_url: str,
    storage_root: Path,
) -> None:
    body = create_character_via_api(client, access_token, name="DupPresetChar")
    char_id = body["character"]["id"]
    session_id = body["creation_session"]["id"]

    base_id, _ = seed_base_for_character(
        database_url,
        character_id=char_id,
        creation_session_id=session_id,
        storage_root=storage_root,
    )

    # Pre-seed a preset motion under this base.
    seed_motion_row(
        database_url,
        parent_type="base",
        parent_id=base_id,
        motion_type="preset_wave",
        name="existing_wave",
    )

    # Different name, but same preset slot → 409.
    resp = _post_base_motion(client, access_token, base_id, motion_type="preset_wave", name="別的")
    assert resp.status_code == 409, resp.text
    assert resp.json()["error"]["code"] == "CONFLICT_PRESET_ALREADY_EXISTS"


def test_post_motion_duplicate_name_returns_409(
    client: TestClient,
    access_token: str,
    database_url: str,
    storage_root: Path,
) -> None:
    body = create_character_via_api(client, access_token, name="DupNameChar")
    char_id = body["character"]["id"]
    session_id = body["creation_session"]["id"]

    base_id, _ = seed_base_for_character(
        database_url,
        character_id=char_id,
        creation_session_id=session_id,
        storage_root=storage_root,
    )

    # Pre-seed a custom motion under this base with a fixed name.
    seed_motion_row(
        database_url,
        parent_type="base",
        parent_id=base_id,
        motion_type="custom",
        name="signature",
        description="initial",
    )

    # Same name, different motion_type → still 409 (name uniqueness is
    # per parent regardless of type).
    resp = _post_base_motion(
        client,
        access_token,
        base_id,
        motion_type="custom",
        name="signature",
        description="another description",
    )
    assert resp.status_code == 409, resp.text
    assert resp.json()["error"]["code"] == "CONFLICT_DUPLICATE_NAME"


def test_post_custom_motion_missing_description_returns_422(
    client: TestClient,
    access_token: str,
    database_url: str,
    storage_root: Path,
) -> None:
    body = create_character_via_api(client, access_token, name="MissingDescChar")
    char_id = body["character"]["id"]
    session_id = body["creation_session"]["id"]

    base_id, _ = seed_base_for_character(
        database_url,
        character_id=char_id,
        creation_session_id=session_id,
        storage_root=storage_root,
    )

    # Custom without description → 422 with the structured AgentError.
    resp = _post_base_motion(client, access_token, base_id, motion_type="custom", name="no_desc")
    assert resp.status_code == 422, resp.text
    assert resp.json()["error"]["code"] == "VALIDATION_MOTION_DESCRIPTION_REQUIRED"


def test_post_custom_motion_blank_description_returns_422(
    client: TestClient,
    access_token: str,
    database_url: str,
    storage_root: Path,
) -> None:
    body = create_character_via_api(client, access_token, name="BlankDescChar")
    char_id = body["character"]["id"]
    session_id = body["creation_session"]["id"]

    base_id, _ = seed_base_for_character(
        database_url,
        character_id=char_id,
        creation_session_id=session_id,
        storage_root=storage_root,
    )

    resp = _post_base_motion(
        client,
        access_token,
        base_id,
        motion_type="custom",
        name="blank_desc",
        description="   ",
    )
    assert resp.status_code == 422, resp.text
    assert resp.json()["error"]["code"] == "VALIDATION_MOTION_DESCRIPTION_REQUIRED"


def test_post_motion_non_owner_returns_403(
    client: TestClient,
    access_token: str,
    second_access_token: str,
    database_url: str,
    storage_root: Path,
) -> None:
    body = create_character_via_api(client, access_token, name="OwnedChar")
    char_id = body["character"]["id"]
    session_id = body["creation_session"]["id"]

    base_id, _ = seed_base_for_character(
        database_url,
        character_id=char_id,
        creation_session_id=session_id,
        storage_root=storage_root,
    )

    resp = _post_base_motion(
        client, second_access_token, base_id, motion_type="preset_nod", name="nod"
    )
    assert resp.status_code == 403, resp.text
    assert resp.json()["error"]["code"] == "AUTH_INSUFFICIENT_PERMISSION"


def test_post_motion_unknown_base_returns_404(
    client: TestClient,
    access_token: str,
) -> None:
    resp = _post_base_motion(
        client,
        access_token,
        str(uuid.uuid4()),
        motion_type="preset_idle",
        name="idle",
    )
    assert resp.status_code == 404, resp.text
    assert resp.json()["error"]["code"] == "NOT_FOUND_CHARACTER"


def test_post_motion_unknown_alias_returns_404(
    client: TestClient,
    access_token: str,
) -> None:
    resp = _post_alias_motion(
        client,
        access_token,
        str(uuid.uuid4()),
        motion_type="custom",
        name="ghost",
        description="跳一段舞",
    )
    assert resp.status_code == 404, resp.text
    assert resp.json()["error"]["code"] == "NOT_FOUND_ALIAS"
