"""PromptReconciler behaviour against a fake LLM client (T-015).

No external network: the LLM client is a `FakeReconcilerClient` from the
sibling conftest, and the cache lives in `fakeredis`. Each test maps to one
of the acceptance criteria in `tickets/T-015-backend-prompt-reconciler.md`.
"""

from __future__ import annotations

from typing import Any

import fakeredis.aioredis
import pytest

from app.core.errors import AgentErrorException
from app.prompt.constraints import ReconcileMode
from app.prompt.reconciler import PromptReconciler, ReconcileInput
from tests.prompt_reconciler.conftest import FakeReconcilerClient


def _no_conflict_response(_system: str, _user: str) -> dict[str, Any]:
    return {
        "reconciled_note_en": "an elegant figure in classical attire",
        "removed_segments": [],
    }


def _conflict_removes_market(_system: str, _user: str) -> dict[str, Any]:
    return {
        "reconciled_note_en": "an elegant classical-style woman",
        "removed_segments": [
            {
                "original_zh": "雜亂市場背景",
                "reason": "conflicts with transparent_background constraint",
            }
        ],
    }


async def test_reconcile_removes_conflicting_background(
    fake_redis: fakeredis.aioredis.FakeRedis,
) -> None:
    client = FakeReconcilerClient(_conflict_removes_market)
    rec = PromptReconciler(redis=fake_redis, client=client)

    output = await rec.reconcile(
        ReconcileInput(
            mode=ReconcileMode.CREATE_BASE,
            menu_selections={"gender": "female", "style": "ink_wash"},
            freeform_note="雜亂市場背景的古風美女",
        )
    )

    final_lower = output.final_prompt.lower()
    assert "transparent background" in final_lower
    assert "cluttered market" not in final_lower
    assert "雜亂市場" not in output.final_prompt
    assert len(output.removed_segments) == 1
    assert output.removed_segments[0].original_zh == "雜亂市場背景"
    assert "elegant classical-style woman" in output.final_prompt


async def test_reconcile_caches_result_and_skips_second_llm_call(
    fake_redis: fakeredis.aioredis.FakeRedis,
) -> None:
    client = FakeReconcilerClient(_no_conflict_response)
    rec = PromptReconciler(redis=fake_redis, client=client)

    inp = ReconcileInput(
        mode=ReconcileMode.CREATE_BASE,
        menu_selections={"gender": "female"},
        freeform_note="優雅的古裝女子",
    )
    first = await rec.reconcile(inp)
    second = await rec.reconcile(inp)

    assert len(client.calls) == 1
    assert second.final_prompt == first.final_prompt
    assert first.cached is False
    assert second.cached is True


async def test_final_prompt_structure_constraints_then_menu_then_note(
    fake_redis: fakeredis.aioredis.FakeRedis,
) -> None:
    client = FakeReconcilerClient(_no_conflict_response)
    rec = PromptReconciler(redis=fake_redis, client=client)

    output = await rec.reconcile(
        ReconcileInput(
            mode=ReconcileMode.CREATE_BASE,
            menu_selections={"gender": "female", "style": "anime"},
            freeform_note="戴眼鏡",
        )
    )

    final = output.final_prompt
    constraints_pos = final.find("transparent background")
    menu_pos = final.find("anime style")
    note_pos = final.find("an elegant figure")
    assert -1 < constraints_pos < menu_pos < note_pos


async def test_menu_only_no_note_skips_llm(
    fake_redis: fakeredis.aioredis.FakeRedis,
) -> None:
    def _refuse(_s: str, _u: str) -> dict[str, Any]:
        pytest.fail("LLM should not be called when freeform_note is empty")

    client = FakeReconcilerClient(_refuse)
    rec = PromptReconciler(redis=fake_redis, client=client)

    output = await rec.reconcile(
        ReconcileInput(
            mode=ReconcileMode.CREATE_BASE,
            menu_selections={"gender": "female"},
            freeform_note=None,
        )
    )

    assert output.reconciled_note_en == ""
    assert "adult woman" in output.final_prompt
    assert "transparent background" in output.final_prompt
    assert client.calls == []


async def test_invalid_llm_json_raises_prompt_conflict(
    fake_redis: fakeredis.aioredis.FakeRedis,
) -> None:
    client = FakeReconcilerClient(lambda _s, _u: {"missing_required_field": True})
    rec = PromptReconciler(redis=fake_redis, client=client)

    with pytest.raises(AgentErrorException) as info:
        await rec.reconcile(
            ReconcileInput(
                mode=ReconcileMode.CREATE_BASE,
                freeform_note="任何補述",
            )
        )
    assert info.value.error.code == "PROMPT_CONFLICT"
    assert info.value.error.retryable is False


async def test_non_object_llm_response_raises_prompt_conflict(
    fake_redis: fakeredis.aioredis.FakeRedis,
) -> None:
    client = FakeReconcilerClient(
        lambda _s, _u: ["not", "a", "dict"],  # type: ignore[return-value]
    )
    rec = PromptReconciler(redis=fake_redis, client=client)

    with pytest.raises(AgentErrorException) as info:
        await rec.reconcile(
            ReconcileInput(
                mode=ReconcileMode.CREATE_BASE,
                freeform_note="補述",
            )
        )
    assert info.value.error.code == "PROMPT_CONFLICT"


async def test_constraint_version_bump_invalidates_cache(
    fake_redis: fakeredis.aioredis.FakeRedis,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = FakeReconcilerClient(_no_conflict_response)
    rec = PromptReconciler(redis=fake_redis, client=client)
    inp = ReconcileInput(
        mode=ReconcileMode.CREATE_BASE,
        freeform_note="某個補述",
    )

    await rec.reconcile(inp)
    assert len(client.calls) == 1

    # Bump the constraint version. The cache key must shift, forcing a
    # fresh LLM consultation rather than serving the stale entry.
    monkeypatch.setattr("app.prompt.reconciler.get_constraints_version", lambda: "v2")
    await rec.reconcile(inp)
    assert len(client.calls) == 2


async def test_preview_does_not_write_cache(
    fake_redis: fakeredis.aioredis.FakeRedis,
) -> None:
    client = FakeReconcilerClient(_no_conflict_response)
    rec = PromptReconciler(redis=fake_redis, client=client)
    inp = ReconcileInput(
        mode=ReconcileMode.CREATE_BASE,
        freeform_note="某個補述",
    )

    await rec.preview(inp)
    keys = [k async for k in fake_redis.scan_iter("reconciler:*")]
    assert keys == []

    # Subsequent reconcile() still calls the LLM — preview didn't seed the cache.
    await rec.reconcile(inp)
    assert len(client.calls) == 2


async def test_preview_reads_cache_populated_by_reconcile(
    fake_redis: fakeredis.aioredis.FakeRedis,
) -> None:
    client = FakeReconcilerClient(_no_conflict_response)
    rec = PromptReconciler(redis=fake_redis, client=client)
    inp = ReconcileInput(
        mode=ReconcileMode.CREATE_BASE,
        freeform_note="某個補述",
    )

    await rec.reconcile(inp)
    output = await rec.preview(inp)

    assert len(client.calls) == 1
    assert output.cached is True


async def test_alias_mode_includes_base_constraints(
    fake_redis: fakeredis.aioredis.FakeRedis,
) -> None:
    """alias_creation YAML says it `inherits base_creation rules` — flatten
    that here so the LLM sees the actual constraint list, not the marker."""
    client = FakeReconcilerClient(_no_conflict_response)
    rec = PromptReconciler(redis=fake_redis, client=client)

    output = await rec.reconcile(
        ReconcileInput(
            mode=ReconcileMode.CREATE_ALIAS,
            freeform_note="新衣服",
        )
    )

    assert "transparent background" in output.final_prompt
    assert "preserves character identity" in output.final_prompt
    assert not any("inherits" in c for c in output.applied_constraints)


async def test_menu_selection_order_does_not_affect_cache_key(
    fake_redis: fakeredis.aioredis.FakeRedis,
) -> None:
    """Cache key sorts menu_selections so callers passing the same data in
    a different dict-iteration order share a single cache entry."""
    client = FakeReconcilerClient(_no_conflict_response)
    rec = PromptReconciler(redis=fake_redis, client=client)

    a = ReconcileInput(
        mode=ReconcileMode.CREATE_BASE,
        menu_selections={"gender": "female", "style": "anime"},
        freeform_note="補述",
    )
    b = ReconcileInput(
        mode=ReconcileMode.CREATE_BASE,
        menu_selections={"style": "anime", "gender": "female"},
        freeform_note="補述",
    )

    await rec.reconcile(a)
    await rec.reconcile(b)
    assert len(client.calls) == 1


async def test_cache_key_isolates_stub_and_real_clients(
    fake_redis: fakeredis.aioredis.FakeRedis,
) -> None:
    """Codex P2 round-1: a stub-mode entry must not satisfy a real-model
    cache lookup. Otherwise toggling AI_STUB_MODE off would still serve the
    stub's empty `reconciled_note_en` for up to 24h, sending gpt-image-2 a
    prompt with no translation of the user's Chinese note.
    """

    def _stub_response(_s: str, _u: str) -> dict[str, Any]:
        return {"reconciled_note_en": "", "removed_segments": []}

    fake_real = FakeReconcilerClient(_no_conflict_response, identity="real:gpt-5-mini")
    fake_stub = FakeReconcilerClient(_stub_response, identity="stub:v1")

    rec_real = PromptReconciler(redis=fake_redis, client=fake_real)
    rec_stub = PromptReconciler(redis=fake_redis, client=fake_stub)

    inp = ReconcileInput(mode=ReconcileMode.CREATE_BASE, freeform_note="補述")

    # Stub populates its own slot.
    stub_out = await rec_stub.reconcile(inp)
    assert stub_out.reconciled_note_en == ""
    assert len(fake_stub.calls) == 1

    # Real client must not see the stub's empty note — it gets its own
    # cache slot and its own LLM call.
    real_out = await rec_real.reconcile(inp)
    assert real_out.reconciled_note_en == "an elegant figure in classical attire"
    assert len(fake_real.calls) == 1

    # Both keys coexist; neither client's second call hits the LLM.
    await rec_real.reconcile(inp)
    await rec_stub.reconcile(inp)
    assert len(fake_real.calls) == 1
    assert len(fake_stub.calls) == 1


async def test_system_prompt_change_invalidates_cache(
    fake_redis: fakeredis.aioredis.FakeRedis,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Codex P2 round-2: a SYSTEM_PROMPT update must shift the cache key.
    Otherwise a product-side prompt fix would still serve old translations
    for up to 24h."""
    client = FakeReconcilerClient(_no_conflict_response)
    rec = PromptReconciler(redis=fake_redis, client=client)
    inp = ReconcileInput(mode=ReconcileMode.CREATE_BASE, freeform_note="補述")

    await rec.reconcile(inp)
    assert len(client.calls) == 1

    monkeypatch.setattr(
        PromptReconciler,
        "SYSTEM_PROMPT",
        "Different system prompt — should force re-translation.",
    )
    await rec.reconcile(inp)
    assert len(client.calls) == 2


async def test_menu_fragments_change_invalidates_cache(
    fake_redis: fakeredis.aioredis.FakeRedis,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Codex P2 round-2: a MENU_FRAGMENTS mapping change must shift the
    cache key — otherwise a corrected option label still serves the old
    final_prompt for up to 24h."""
    client = FakeReconcilerClient(_no_conflict_response)
    rec = PromptReconciler(redis=fake_redis, client=client)
    inp = ReconcileInput(
        mode=ReconcileMode.CREATE_BASE,
        menu_selections={"gender": "female"},
        freeform_note="補述",
    )

    await rec.reconcile(inp)
    assert len(client.calls) == 1

    monkeypatch.setattr(
        "app.prompt.reconciler.MENU_FRAGMENTS",
        {"gender": {"female": "different translation"}},
    )
    await rec.reconcile(inp)
    assert len(client.calls) == 2


async def test_cache_corruption_falls_back_to_fresh_llm_call(
    fake_redis: fakeredis.aioredis.FakeRedis,
) -> None:
    """A stray non-JSON value at the cache key shouldn't break the call."""
    client = FakeReconcilerClient(_no_conflict_response)
    rec = PromptReconciler(redis=fake_redis, client=client)
    inp = ReconcileInput(
        mode=ReconcileMode.CREATE_BASE,
        freeform_note="補述",
    )

    cache_key = rec._cache_key(inp)  # type: ignore[attr-defined]
    await fake_redis.set(cache_key, "not-json{")

    output = await rec.reconcile(inp)
    assert output.cached is False
    assert len(client.calls) == 1
