"""DB-backed coverage for `POST /v1/prompt/preview` alias / motion modes.

T-035 acceptance:
  - create_alias happy → derived_from + reconciled prompt
  - create_alias mask not found → 404 NOT_FOUND_MASK
  - create_alias non-owner → 403 AUTH_INSUFFICIENT_PERMISSION
  - create_alias mask owned by another character → 404 NOT_FOUND_MASK
  - create_alias all-empty → 400 VALIDATION_EMPTY_INPUT
  - create_motion preset → 200, motion_template_used=preset_*, no LLM call
  - create_motion custom → 200, motion_template_used=custom_reconciled,
    LLM called once
  - create_motion parent_type/id mismatch → 400
    VALIDATION_MOTION_PARENT_MISMATCH
  - create_motion custom missing description → 422
    VALIDATION_MOTION_DESCRIPTION_REQUIRED
  - create_motion non-owner → 403

Hermetic create_base coverage stays in `../test_prompt_preview.py`; this
suite intentionally limits itself to the cases that need real character
/ alias / mask rows so the conftest cost is justified.
"""

from __future__ import annotations

import uuid
from typing import Any

from fastapi.testclient import TestClient

from tests.prompt_preview.alias_motion.conftest import auth_headers
from tests.prompt_reconciler.conftest import FakeReconcilerClient

# ---------------------------------------------------------------------------
# create_alias
# ---------------------------------------------------------------------------


def test_alias_happy_returns_derived_from_and_reconciled_prompt(
    client: TestClient,
    seeded_character: dict[str, Any],
    access_token: str,
    fake_reconciler_client: FakeReconcilerClient,
) -> None:
    resp = client.post(
        "/v1/prompt/preview",
        headers=auth_headers(access_token),
        json={
            "mode": "create_alias",
            "character_id": str(seeded_character["id"]),
            "input_mode": "text",
            "freeform_note": "改成西裝造型",
        },
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["final_prompt"]
    assert body["derived_from"]["base_id"] == str(seeded_character["base_id"])
    # Signed URL is generated against the seeded base image_key — exact
    # signing is storage-backend specific; we only assert the URL is
    # non-empty so this test stays decoupled from URL signing internals.
    assert body["derived_from"]["base_image_url"]
    # Mode-specific fields shouldn't leak across — only `derived_from`
    # is populated for alias mode.
    assert "parent" not in body
    assert "motion_template_used" not in body
    # LLM was called once for the freeform note translation.
    assert len(fake_reconciler_client.calls) == 1


def test_alias_with_mask_passes_inpaint_signal_to_reconciler(
    client: TestClient,
    seeded_character: dict[str, Any],
    seeded_mask: uuid.UUID,
    access_token: str,
    fake_reconciler_client: FakeReconcilerClient,
) -> None:
    resp = client.post(
        "/v1/prompt/preview",
        headers=auth_headers(access_token),
        json={
            "mode": "create_alias",
            "character_id": str(seeded_character["id"]),
            "input_mode": "inpaint",
            "freeform_note": "把領口改成 V 領",
            "mask": {"mask_id": str(seeded_mask)},
        },
    )
    assert resp.status_code == 200, resp.text
    _, user_prompt = fake_reconciler_client.calls[0]
    assert "Has inpaint mask: True" in user_prompt


def test_alias_rejects_unknown_mask_id(
    client: TestClient,
    seeded_character: dict[str, Any],
    access_token: str,
) -> None:
    resp = client.post(
        "/v1/prompt/preview",
        headers=auth_headers(access_token),
        json={
            "mode": "create_alias",
            "character_id": str(seeded_character["id"]),
            "input_mode": "inpaint",
            "freeform_note": "改造型",
            "mask": {"mask_id": str(uuid.uuid4())},
        },
    )
    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "NOT_FOUND_MASK"


def test_alias_rejects_mask_owned_by_other_character(
    client: TestClient,
    seeded_character: dict[str, Any],
    seeded_mask: uuid.UUID,
    seeded_user: dict[str, Any],
    database_url: str,
    access_token: str,
) -> None:
    """Cross-character mask access collapses to NOT_FOUND_MASK rather
    than a distinct code — otherwise an attacker could probe for the
    existence of other characters' masks via id enumeration.

    Seeds a second character with its own Base so the alias path
    actually reaches the mask cross-character check (an other-character
    without a Base would 409 on `CONFLICT_BASE_NOT_SET` before mask
    validation runs)."""
    import asyncio

    from tests.prompt_preview.alias_motion.conftest import (
        _insert_base,
        _insert_character,
        _insert_creation_session_and_checkpoint,
    )

    async def _seed_other_character_with_base() -> uuid.UUID:
        char_id = await _insert_character(
            database_url,
            owner_id=seeded_user["id"],
            team_id=seeded_user["team_id"],
            name="other-char",
            slug="other-char",
        )
        session_id, checkpoint_id = await _insert_creation_session_and_checkpoint(
            database_url,
            character_id=char_id,
            initiator_id=seeded_user["id"],
        )
        await _insert_base(
            database_url,
            character_id=char_id,
            from_checkpoint_id=checkpoint_id,
            image_key=f"checkpoints/{session_id}/output/seq-1.png",
        )
        return char_id

    other_character_id = asyncio.run(_seed_other_character_with_base())
    resp = client.post(
        "/v1/prompt/preview",
        headers=auth_headers(access_token),
        json={
            "mode": "create_alias",
            "character_id": str(other_character_id),
            "input_mode": "inpaint",
            "freeform_note": "改造型",
            "mask": {"mask_id": str(seeded_mask)},
        },
    )
    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "NOT_FOUND_MASK"


def test_alias_non_owner_returns_403(
    client: TestClient,
    seeded_character: dict[str, Any],
    second_access_token: str,
) -> None:
    """`seeded_character` belongs to alice; bob's token shouldn't be
    able to peek at the alias-prompt surface."""
    resp = client.post(
        "/v1/prompt/preview",
        headers=auth_headers(second_access_token),
        json={
            "mode": "create_alias",
            "character_id": str(seeded_character["id"]),
            "input_mode": "text",
            "freeform_note": "改造型",
        },
    )
    assert resp.status_code == 403
    assert resp.json()["error"]["code"] == "AUTH_INSUFFICIENT_PERMISSION"


def test_alias_inpaint_mode_requires_mask(
    client: TestClient,
    seeded_character: dict[str, Any],
    access_token: str,
) -> None:
    """Codex P2 (PR #42, commit b144149): preview must mirror T-031's
    alias-create payload contract — `inpaint` mode requires a mask.
    Otherwise preview would 200 on a combination the worker will reject."""
    resp = client.post(
        "/v1/prompt/preview",
        headers=auth_headers(access_token),
        json={
            "mode": "create_alias",
            "character_id": str(seeded_character["id"]),
            "input_mode": "inpaint",
            "freeform_note": "改成 V 領",
            # No mask sent — should 422.
        },
    )
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "VALIDATION_ALIAS_INPUT_MODE_MISMATCH"


def test_alias_image_mode_requires_reference_images(
    client: TestClient,
    seeded_character: dict[str, Any],
    access_token: str,
) -> None:
    """Same as above but for `input_mode='image'` — reference_image_ids
    must be supplied per T-031 contract."""
    resp = client.post(
        "/v1/prompt/preview",
        headers=auth_headers(access_token),
        json={
            "mode": "create_alias",
            "character_id": str(seeded_character["id"]),
            "input_mode": "image",
            "freeform_note": "參考圖中的造型",
            # No reference_image_ids sent — should 422.
        },
    )
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "VALIDATION_ALIAS_INPUT_MODE_MISMATCH"


def test_alias_rejects_all_empty(
    client: TestClient,
    seeded_character: dict[str, Any],
    access_token: str,
) -> None:
    resp = client.post(
        "/v1/prompt/preview",
        headers=auth_headers(access_token),
        json={
            "mode": "create_alias",
            "character_id": str(seeded_character["id"]),
            "input_mode": "text",
        },
    )
    assert resp.status_code == 400
    assert resp.json()["error"]["code"] == "VALIDATION_EMPTY_INPUT"


# ---------------------------------------------------------------------------
# create_motion
# ---------------------------------------------------------------------------


def test_motion_preset_skips_reconciler_and_echoes_template(
    client: TestClient,
    seeded_character: dict[str, Any],
    access_token: str,
    fake_reconciler_client: FakeReconcilerClient,
) -> None:
    resp = client.post(
        "/v1/prompt/preview",
        headers=auth_headers(access_token),
        json={
            "mode": "create_motion",
            "parent_type": "base",
            "parent_id": str(seeded_character["base_id"]),
            "motion_type": "preset_wave",
        },
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["motion_template_used"] == "preset_wave"
    # Preset prompt content surfaces in `final_prompt` so the modal can
    # show it without re-fetching from a separate templates endpoint.
    assert "wave" in body["final_prompt"].lower()
    # Parent block is populated; no derived_from for motion mode.
    assert body["parent"]["type"] == "base"
    assert body["parent"]["id"] == str(seeded_character["base_id"])
    assert body["parent"]["image_url"]
    assert "derived_from" not in body
    # Preset path skips the LLM entirely.
    assert fake_reconciler_client.calls == []


def test_motion_custom_runs_reconciler_and_reports_custom_template(
    client: TestClient,
    seeded_character: dict[str, Any],
    access_token: str,
    fake_reconciler_client: FakeReconcilerClient,
) -> None:
    resp = client.post(
        "/v1/prompt/preview",
        headers=auth_headers(access_token),
        json={
            "mode": "create_motion",
            "parent_type": "base",
            "parent_id": str(seeded_character["base_id"]),
            "motion_type": "custom",
            "description": "把手插腰然後左右擺動",
        },
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["motion_template_used"] == "custom_reconciled"
    assert body["final_prompt"]
    # Custom path goes through the reconciler exactly once. The fake
    # responder echoes "an updated outfit, suit and tie" — we only
    # assert the call happened; exact prompt content is the
    # reconciler's contract, not this route's.
    assert len(fake_reconciler_client.calls) == 1


def test_motion_custom_requires_description(
    client: TestClient,
    seeded_character: dict[str, Any],
    access_token: str,
) -> None:
    resp = client.post(
        "/v1/prompt/preview",
        headers=auth_headers(access_token),
        json={
            "mode": "create_motion",
            "parent_type": "base",
            "parent_id": str(seeded_character["base_id"]),
            "motion_type": "custom",
        },
    )
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "VALIDATION_MOTION_DESCRIPTION_REQUIRED"


def test_motion_parent_type_mismatch_against_alias(
    client: TestClient,
    seeded_character: dict[str, Any],
    seeded_alias: dict[str, Any],
    access_token: str,
) -> None:
    """`parent_type='base'` with an alias id surfaces as a structured
    mismatch — the row exists but is the wrong kind. Distinct from
    NOT_FOUND_* so the frontend can render a "type mismatch" hint.

    Note: only fires for owners — same-team-non-owners get 404
    instead so the error surface is consistent with the legitimate
    parent path's 403 (see test_motion_parent_mismatch_collapses_for_non_owner).
    """
    resp = client.post(
        "/v1/prompt/preview",
        headers=auth_headers(access_token),
        json={
            "mode": "create_motion",
            "parent_type": "base",
            "parent_id": str(seeded_alias["id"]),
            "motion_type": "preset_idle",
        },
    )
    assert resp.status_code == 400
    assert resp.json()["error"]["code"] == "VALIDATION_MOTION_PARENT_MISMATCH"


def test_base_remix_rejects_other_users_checkpoint(
    client: TestClient,
    seeded_character: dict[str, Any],
    second_access_token: str,
) -> None:
    """Codex P2 (PR #42, commit 0b04ff4): preview must enforce the same
    same-session ownership the worker's `_resolve_base_checkpoint`
    enforces. Otherwise an authenticated caller could 200 on any
    checkpoint id (cross-user existence oracle) while the generate path
    would later 404 on the same id.

    Bob (different user) sending alice's checkpoint id under
    `mode='create_base'` must collapse to NOT_FOUND_CHECKPOINT — same
    envelope a typo'd id would produce."""
    resp = client.post(
        "/v1/prompt/preview",
        headers=auth_headers(second_access_token),
        json={
            "mode": "create_base",
            "freeform_note": "remix something",
            "base_checkpoint_id": str(seeded_character["checkpoint_id"]),
        },
    )
    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "NOT_FOUND_CHECKPOINT"


def test_motion_parent_mismatch_collapses_for_non_owner(
    client: TestClient,
    seeded_character: dict[str, Any],
    seeded_alias: dict[str, Any],
    second_access_token: str,
) -> None:
    """Codex P2 (PR #42, commit 4e26141): a same-team non-owner sending
    `parent_type='base'` with an alias id from owner's character must
    NOT see VALIDATION_MOTION_PARENT_MISMATCH (400) — the legitimate
    `parent_type='alias'` path returns 403 for the same caller, so the
    mismatch envelope leaks more info than the legitimate path does.

    Bob is in alice's team but doesn't own seeded_character. Sending the
    alias id under parent_type='base' must collapse to NOT_FOUND_*."""
    resp = client.post(
        "/v1/prompt/preview",
        headers=auth_headers(second_access_token),
        json={
            "mode": "create_motion",
            "parent_type": "base",
            "parent_id": str(seeded_alias["id"]),
            "motion_type": "preset_idle",
        },
    )
    assert resp.status_code == 404
    assert resp.json()["error"]["code"] != "VALIDATION_MOTION_PARENT_MISMATCH"


def test_motion_alias_parent_happy(
    client: TestClient,
    seeded_alias: dict[str, Any],
    access_token: str,
) -> None:
    resp = client.post(
        "/v1/prompt/preview",
        headers=auth_headers(access_token),
        json={
            "mode": "create_motion",
            "parent_type": "alias",
            "parent_id": str(seeded_alias["id"]),
            "motion_type": "preset_nod",
        },
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["parent"]["type"] == "alias"
    assert body["parent"]["id"] == str(seeded_alias["id"])
    assert body["motion_template_used"] == "preset_nod"


def test_motion_non_owner_returns_403(
    client: TestClient,
    seeded_character: dict[str, Any],
    second_access_token: str,
) -> None:
    resp = client.post(
        "/v1/prompt/preview",
        headers=auth_headers(second_access_token),
        json={
            "mode": "create_motion",
            "parent_type": "base",
            "parent_id": str(seeded_character["base_id"]),
            "motion_type": "preset_wave",
        },
    )
    assert resp.status_code == 403
    assert resp.json()["error"]["code"] == "AUTH_INSUFFICIENT_PERMISSION"
