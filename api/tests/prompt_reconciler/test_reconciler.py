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


async def test_final_prompt_structure_scene_then_menu_then_note_then_avoid(
    fake_redis: fakeredis.aioredis.FakeRedis,
) -> None:
    """T-050: final prompt order matches OpenAI's image-gen cookbook —
    scene constraints, then subject (menu fragments), then user note
    (details), then avoid constraints last so the image model attends
    to "what to preserve / what to avoid" most recently.
    """
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
    scene_pos = final.find("transparent background")
    menu_pos = final.find("anime style")
    note_pos = final.find("an elegant figure")
    avoid_pos = final.find("no watermarks or signatures")
    assert -1 < scene_pos < menu_pos < note_pos < avoid_pos


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


async def test_missing_removed_segments_field_raises_prompt_conflict(
    fake_redis: fakeredis.aioredis.FakeRedis,
) -> None:
    """Codex P2 round-4: a response that omits `removed_segments` entirely
    is partial schema drift — must raise PROMPT_CONFLICT same as a missing
    `reconciled_note_en`, not be silently defaulted to `[]`."""
    client = FakeReconcilerClient(
        lambda _s, _u: {"reconciled_note_en": "ok"},  # removed_segments absent
    )
    rec = PromptReconciler(redis=fake_redis, client=client)

    with pytest.raises(AgentErrorException) as info:
        await rec.reconcile(ReconcileInput(mode=ReconcileMode.CREATE_BASE, freeform_note="補述"))
    assert info.value.error.code == "PROMPT_CONFLICT"


async def test_malformed_removed_segment_item_raises_prompt_conflict(
    fake_redis: fakeredis.aioredis.FakeRedis,
) -> None:
    """Codex P2 round-3: a non-object entry inside removed_segments must
    fail loud, not be silently dropped. Otherwise partial schema drift
    gets cached as a 'successful' reconcile with missing audit data."""
    client = FakeReconcilerClient(
        lambda _s, _u: {
            "reconciled_note_en": "ok",
            "removed_segments": ["not-an-object"],
        }
    )
    rec = PromptReconciler(redis=fake_redis, client=client)

    with pytest.raises(AgentErrorException) as info:
        await rec.reconcile(ReconcileInput(mode=ReconcileMode.CREATE_BASE, freeform_note="補述"))
    assert info.value.error.code == "PROMPT_CONFLICT"


async def test_removed_segment_missing_field_raises_prompt_conflict(
    fake_redis: fakeredis.aioredis.FakeRedis,
) -> None:
    """A removed_segments item missing the `reason` field is partial
    schema drift — must raise PROMPT_CONFLICT (Codex P2 round-3)."""
    client = FakeReconcilerClient(
        lambda _s, _u: {
            "reconciled_note_en": "ok",
            "removed_segments": [{"original_zh": "雜亂"}],  # `reason` missing
        }
    )
    rec = PromptReconciler(redis=fake_redis, client=client)

    with pytest.raises(AgentErrorException) as info:
        await rec.reconcile(ReconcileInput(mode=ReconcileMode.CREATE_BASE, freeform_note="補述"))
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


async def test_menu_selection_order_isolates_cache_slots(
    fake_redis: fakeredis.aioredis.FakeRedis,
) -> None:
    """Codex P2 round-3: menu_selections insertion order is preserved in
    the cache key. resolve_menu_fragments() iterates the dict in insertion
    order, so two requests with the same key/value pairs in different
    orders compose different fragment orders — they MUST get distinct
    cache slots, otherwise whichever request wrote first would dictate
    fragment order for both regardless of caller intent.
    """
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

    out_a = await rec.reconcile(a)
    out_b = await rec.reconcile(b)

    assert len(client.calls) == 2
    assert out_a.menu_fragments_en != out_b.menu_fragments_en
    assert out_a.menu_fragments_en[0] == "adult woman"
    # T-050 expanded the `style` mappings; assert via prefix so we catch
    # the order without pinning the full descriptive string.
    assert out_b.menu_fragments_en[0].startswith("anime style")
    assert out_b.menu_fragments_en[1] == "adult woman"


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


# ---------------------------------------------------------------------------
# T-050 — cookbook structure (scene → menu → note → avoid) and per-mode
# avoid-block content. These tests pin the new behaviour so a future tweak
# can't silently regress the cookbook ordering or drop the alias preserve
# rules.
# ---------------------------------------------------------------------------


async def test_base_avoid_block_appears_after_user_note(
    fake_redis: fakeredis.aioredis.FakeRedis,
) -> None:
    """Cookbook ordering: avoid clauses must come AFTER the reconciled
    note, not before. Otherwise the image model reads avoid rules first
    and they lose salience under attention pooling."""
    client = FakeReconcilerClient(_no_conflict_response)
    rec = PromptReconciler(redis=fake_redis, client=client)

    output = await rec.reconcile(
        ReconcileInput(
            mode=ReconcileMode.CREATE_BASE,
            freeform_note="戴眼鏡",
        )
    )

    note_pos = output.final_prompt.find("an elegant figure")
    avoid_pos = output.final_prompt.find("no watermarks or signatures")
    assert -1 < note_pos < avoid_pos
    assert "no cropping at frame edges" in output.final_prompt


async def test_alias_avoid_includes_preservation_clauses(
    fake_redis: fakeredis.aioredis.FakeRedis,
) -> None:
    """Alias avoid block must surface the cookbook edit-mode preservation
    list (identity / geometry / proportions / pose / lighting). Otherwise
    image-to-image edits drift on unintended dimensions."""
    client = FakeReconcilerClient(_no_conflict_response)
    rec = PromptReconciler(redis=fake_redis, client=client)

    output = await rec.reconcile(
        ReconcileInput(
            mode=ReconcileMode.CREATE_ALIAS,
            freeform_note="改成西裝",
        )
    )

    final = output.final_prompt
    assert "preserves character identity" in final
    assert "preserves overall body proportions and build" in final
    assert "do not alter pose, framing, or lighting" in final
    # Alias avoid inherits base avoid clauses too.
    assert "no watermarks or signatures" in final


async def test_motion_mode_unchanged_no_avoid_block(
    fake_redis: fakeredis.aioredis.FakeRedis,
) -> None:
    """T-050 only tunes gpt-image-bound modes. Motion mode keeps the
    pre-T-050 composition (scene → menu → note, no avoid block) — i2v
    prompt tuning is deferred to a future ticket. This test fails loudly
    if anyone adds motion_creation_avoid entries by mistake."""
    client = FakeReconcilerClient(_no_conflict_response)
    rec = PromptReconciler(redis=fake_redis, client=client)

    output = await rec.reconcile(
        ReconcileInput(
            mode=ReconcileMode.CREATE_MOTION,
            freeform_note="揮手",
        )
    )

    # Motion's scene constraints are present.
    assert "transparent background (match base)" in output.final_prompt
    # No avoid clauses for motion (those would all be base/alias-specific).
    assert "preserve character's appearance" not in output.final_prompt
    assert "no identity morphing" not in output.final_prompt
    # Composition still ends with the user note (no trailing avoid block).
    assert output.final_prompt.rstrip(".").endswith("an elegant figure in classical attire")


async def test_constraint_yaml_content_change_invalidates_cache(
    fake_redis: fakeredis.aioredis.FakeRedis,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """T-050 codex round-2: editing a constraint string in
    `platform_constraints.yaml` without bumping `version:` must still
    shift the cache key. `_logic_version` now hashes the loaded constraint
    payload alongside SYSTEM_PROMPT + MENU_FRAGMENTS so the safety net
    no longer relies on operators remembering to bump the version.
    """
    from app.core.platform_constraints import PlatformConstraints

    client = FakeReconcilerClient(_no_conflict_response)
    rec = PromptReconciler(redis=fake_redis, client=client)
    inp = ReconcileInput(mode=ReconcileMode.CREATE_BASE, freeform_note="補述")

    await rec.reconcile(inp)
    assert len(client.calls) == 1

    # Mutate the YAML content (different wording) but keep version unchanged.
    mutated = PlatformConstraints(
        version="v1.2",
        updated_at="2026-05-07",
        base_creation=("transparent background", "different wording here"),
        base_creation_avoid=("no watermarks or signatures",),
        alias_creation=(),
        alias_creation_avoid=(),
        motion_creation=(),
        motion_creation_avoid=(),
    )
    monkeypatch.setattr(
        "app.prompt.reconciler.load_platform_constraints",
        lambda: mutated,
    )

    await rec.reconcile(inp)
    assert len(client.calls) == 2


async def test_final_prompt_has_no_double_period_when_note_ends_with_one(
    fake_redis: fakeredis.aioredis.FakeRedis,
) -> None:
    """T-050 codex round-2: SYSTEM_PROMPT principle 4 (literal-text
    handling) shows the LLM a period-terminated example, so the
    reconciled note may legitimately end in `.`. The composer must strip
    trailing periods before joining so we never emit `... "GUIDE".. <next>`.
    """

    def _note_with_trailing_period(_s: str, _u: str) -> dict[str, Any]:
        return {
            "reconciled_note_en": 'wearing a badge that reads "GUIDE".',
            "removed_segments": [],
        }

    client = FakeReconcilerClient(_note_with_trailing_period)
    rec = PromptReconciler(redis=fake_redis, client=client)

    output = await rec.reconcile(
        ReconcileInput(
            mode=ReconcileMode.CREATE_BASE,
            freeform_note="戴著寫著導覽員的徽章",
        )
    )

    assert ".." not in output.final_prompt
    # Final prompt still ends with exactly one terminating period.
    assert output.final_prompt.endswith(".")
    assert not output.final_prompt.endswith("..")


async def test_final_prompt_no_double_period_when_note_ends_with_period_then_whitespace(
    fake_redis: fakeredis.aioredis.FakeRedis,
) -> None:
    """T-050 codex round-3 (P2): the reconciled note can end with a
    period followed by trailing whitespace / newline (e.g.
    `'"GUIDE".\\n'`). Whitespace must be stripped BEFORE the trailing
    period — otherwise `rstrip(".")` sees `\\n` and leaves the period
    intact, regressing the round-2 fix on a realistic LLM output shape.
    """

    def _note(_s: str, _u: str) -> dict[str, Any]:
        return {
            "reconciled_note_en": 'wearing a badge that reads "GUIDE".\n',
            "removed_segments": [],
        }

    client = FakeReconcilerClient(_note)
    rec = PromptReconciler(redis=fake_redis, client=client)

    output = await rec.reconcile(
        ReconcileInput(
            mode=ReconcileMode.CREATE_BASE,
            freeform_note="戴著寫著導覽員的徽章",
        )
    )

    assert ".." not in output.final_prompt
    assert output.final_prompt.endswith(".")
    assert not output.final_prompt.endswith("..")
    # The avoid block must still be present and well-attached at the end.
    assert "no watermarks or signatures" in output.final_prompt


async def test_user_prompt_presents_scene_and_avoid_blocks_separately(
    fake_redis: fakeredis.aioredis.FakeRedis,
) -> None:
    """The LLM needs to know which constraints will be injected before vs
    after its translation, so it can flag user-note conflicts against
    BOTH lists without restating either in `reconciled_note_en`."""
    client = FakeReconcilerClient(_no_conflict_response)
    rec = PromptReconciler(redis=fake_redis, client=client)

    await rec.reconcile(
        ReconcileInput(
            mode=ReconcileMode.CREATE_BASE,
            freeform_note="某個補述",
        )
    )

    assert len(client.calls) == 1
    _, user_prompt = client.calls[0]
    assert "Platform scene constraints" in user_prompt
    assert "Platform avoid constraints" in user_prompt
    # Both blocks are rendered with the actual entries.
    assert "transparent background" in user_prompt
    assert "no watermarks or signatures" in user_prompt
