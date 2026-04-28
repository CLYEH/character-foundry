"""Behavioural coverage for `POST /v1/prompt/preview` (T-019).

Covers each acceptance criterion in `tickets/T-019-backend-prompt-preview.md`:
  - happy path (4 fields, non-empty `final_prompt`)
  - empty input → 400 `VALIDATION_EMPTY_INPUT`
  - reconciler conflict → 400 `PROMPT_CONFLICT`
  - preview never writes the reconciler cache
  - preview reads a cache slot a prior `reconcile()` populated
  - OpenAPI surfaces the path and the request schema
"""

from __future__ import annotations

import uuid
from typing import Any

import fakeredis.aioredis
from fastapi.testclient import TestClient

from app.main import app
from app.prompt.constraints import ReconcileMode
from app.prompt.errors import prompt_conflict
from app.prompt.reconciler import PromptReconciler, ReconcileInput
from tests.prompt_reconciler.conftest import FakeReconcilerClient

# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


def test_preview_returns_four_fields_with_non_empty_final_prompt(
    client: TestClient,
) -> None:
    resp = client.post(
        "/v1/prompt/preview",
        json={
            "mode": "create_base",
            "menu_selections": {"gender": "female", "style": "ink_wash"},
            "freeform_note": "優雅的古裝女子",
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    assert set(body.keys()) == {
        "platform_constraints",
        "reconciled_note_en",
        "menu_fragments",
        "final_prompt",
    }
    assert body["final_prompt"]
    assert isinstance(body["menu_fragments"], list)
    # Platform constraints are joined into one human-readable string per the
    # api-shape §5.6 contract; verify the canonical "transparent background"
    # constraint surfaces here so frontend can render it directly.
    assert "transparent background" in body["platform_constraints"]
    assert "an elegant figure in classical attire" in body["final_prompt"]


def test_preview_routes_create_base_with_reference_to_base_with_ref_mode(
    client: TestClient,
    fake_reconciler_client: FakeReconcilerClient,
) -> None:
    """`create_base` + reference_image_ids must flow through as
    CREATE_BASE_WITH_REF — verified indirectly by the user prompt the LLM
    sees (`Has reference image: True`)."""
    resp = client.post(
        "/v1/prompt/preview",
        json={
            "mode": "create_base",
            "freeform_note": "古裝美女",
            "reference_image_ids": [str(uuid.uuid4())],
        },
    )
    assert resp.status_code == 200
    assert len(fake_reconciler_client.calls) == 1
    _, user_prompt = fake_reconciler_client.calls[0]
    assert "Has reference image: True" in user_prompt


def test_preview_passes_mask_signal_through_as_inpaint_flag(
    client: TestClient,
    fake_reconciler_client: FakeReconcilerClient,
) -> None:
    """Mask contents are ignored on the wire (T-019 is alias-pre-Sprint-3)
    but presence MUST flip `has_inpaint_mask` so the reconciler sees the
    same signal it will see at generate time."""
    resp = client.post(
        "/v1/prompt/preview",
        json={
            "mode": "create_alias",
            "freeform_note": "改成西裝",
            "mask": {"polygon": [[0, 0], [10, 0], [10, 10]]},
        },
    )
    assert resp.status_code == 200
    _, user_prompt = fake_reconciler_client.calls[0]
    assert "Has inpaint mask: True" in user_prompt


# ---------------------------------------------------------------------------
# Validation: empty input → VALIDATION_EMPTY_INPUT
# ---------------------------------------------------------------------------


def test_preview_rejects_empty_input(client: TestClient) -> None:
    resp = client.post("/v1/prompt/preview", json={"mode": "create_base"})
    assert resp.status_code == 400
    assert resp.json()["error"]["code"] == "VALIDATION_EMPTY_INPUT"


def test_preview_rejects_only_whitespace_freeform_note(client: TestClient) -> None:
    """A note that is whitespace-only is treated as empty — otherwise the
    reconciler would skip the LLM (real reconciler shortcut) and the
    response would be `constraints` alone, which isn't a meaningful preview.
    """
    resp = client.post(
        "/v1/prompt/preview",
        json={"mode": "create_base", "freeform_note": "   "},
    )
    assert resp.status_code == 400
    assert resp.json()["error"]["code"] == "VALIDATION_EMPTY_INPUT"


def test_preview_rejects_non_string_menu_value(client: TestClient) -> None:
    """`menu_selections` is `dict[str, str]` on the wire so the cache key
    can't fragment between e.g. `{"age": 25}` and `{"age": "25"}` — pydantic
    422s the bad shape upfront."""
    resp = client.post(
        "/v1/prompt/preview",
        json={"mode": "create_base", "menu_selections": {"age": 25}},
    )
    assert resp.status_code == 422


def test_preview_rejects_empty_reference_image_list(client: TestClient) -> None:
    """`reference_image_ids: []` is the same as "no reference"; require some
    other signal so the wire treats both as the same empty input."""
    resp = client.post(
        "/v1/prompt/preview",
        json={"mode": "create_base", "reference_image_ids": []},
    )
    assert resp.status_code == 400
    assert resp.json()["error"]["code"] == "VALIDATION_EMPTY_INPUT"


# ---------------------------------------------------------------------------
# Reconciler failure → PROMPT_CONFLICT
# ---------------------------------------------------------------------------


def test_preview_surfaces_reconciler_prompt_conflict(
    client: TestClient,
    fake_reconciler_client: FakeReconcilerClient,
) -> None:
    """A reconciler that raises PROMPT_CONFLICT must propagate as the same
    AgentError to the wire — the route doesn't swallow or remap."""

    def _raise(_s: str, _u: str) -> dict[str, Any]:
        raise prompt_conflict(
            problem="Reconciler LLM omitted reconciled_note_en or sent a non-string value.",
            cause="LLM did not honour the required output schema.",
            fix="Retry; if persistent, inspect the system prompt for schema drift.",
        )

    fake_reconciler_client._responder = _raise  # noqa: SLF001 — test-only

    resp = client.post(
        "/v1/prompt/preview",
        json={"mode": "create_base", "freeform_note": "天馬行空"},
    )
    assert resp.status_code == 400
    err = resp.json()["error"]
    assert err["code"] == "PROMPT_CONFLICT"
    assert err["retryable"] is False


# ---------------------------------------------------------------------------
# Cache contract: preview never writes; preview reads what reconcile wrote.
# ---------------------------------------------------------------------------


def test_preview_does_not_write_reconciler_cache(
    client: TestClient,
    sync_redis: fakeredis.FakeStrictRedis,
) -> None:
    resp = client.post(
        "/v1/prompt/preview",
        json={"mode": "create_base", "freeform_note": "某個補述"},
    )
    assert resp.status_code == 200

    # Acceptance criterion: no `reconciler:*` key materialised in Redis.
    # `sync_redis` shares the same FakeServer the route's per-request
    # async client wrote into (if it had written anything), so an empty
    # scan here proves preview() never touched the cache.
    assert list(sync_redis.scan_iter("reconciler:*")) == []


async def test_preview_reads_cache_populated_by_prior_reconcile(
    client: TestClient,
    fake_server: fakeredis.FakeServer,
    sync_redis: fakeredis.FakeStrictRedis,
    fake_reconciler_client: FakeReconcilerClient,
) -> None:
    """Acceptance: reconcile() then preview() with the SAME input must hit
    the cache (LLM not re-called) and not add a new key.

    We drive `reconcile()` directly (no public reconcile endpoint) so the
    cache slot is real, then call preview over HTTP and assert the LLM
    call count stayed at 1. The reconciler we build here uses a *new*
    async client bound to the test's loop, but it shares `FakeServer`
    state with the route's per-request client — the cache key matches
    because both reconcilers see the same SYSTEM_PROMPT, MENU_FRAGMENTS,
    and `client_identity` ("fake:test").
    """
    import fakeredis.aioredis  # noqa: PLC0415

    test_loop_redis = fakeredis.aioredis.FakeRedis(server=fake_server, decode_responses=True)
    rec = PromptReconciler(redis=test_loop_redis, client=fake_reconciler_client)

    await rec.reconcile(
        ReconcileInput(mode=ReconcileMode.CREATE_BASE, freeform_note="一致的補述")
    )
    assert len(fake_reconciler_client.calls) == 1

    keys_before = list(sync_redis.scan_iter("reconciler:*"))
    assert len(keys_before) == 1

    resp = client.post(
        "/v1/prompt/preview",
        json={"mode": "create_base", "freeform_note": "一致的補述"},
    )
    assert resp.status_code == 200

    # LLM not re-invoked — cache hit.
    assert len(fake_reconciler_client.calls) == 1

    # And no new keys were written by preview.
    keys_after = list(sync_redis.scan_iter("reconciler:*"))
    assert keys_after == keys_before


# ---------------------------------------------------------------------------
# OpenAPI
# ---------------------------------------------------------------------------


def test_openapi_surfaces_prompt_preview() -> None:
    schema = app.openapi()
    assert "/v1/prompt/preview" in schema["paths"]
    op = schema["paths"]["/v1/prompt/preview"]["post"]
    assert op["tags"] == ["prompt"]

    body_ref = op["requestBody"]["content"]["application/json"]["schema"]["$ref"]
    request_name = body_ref.rsplit("/", 1)[-1]
    request_schema = schema["components"]["schemas"][request_name]
    # All four optional fields surface in the OpenAPI spec so agent
    # callers get a self-describing contract.
    assert request_schema["properties"].keys() >= {
        "mode",
        "menu_selections",
        "freeform_note",
        "reference_image_ids",
        "mask",
    }
