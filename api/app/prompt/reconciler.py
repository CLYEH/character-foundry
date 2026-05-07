"""PromptReconciler — turn user input into a final English image/video prompt.

Pipeline (per planning/backend/prompt-reconciler.md, with T-050 cookbook
upgrades):
  1. Resolve menu fragments deterministically from the static map.
  2. Look up scene + avoid platform constraints by mode.
  3. (LLM) Translate the freeform Chinese note + flag/rewrite conflicts.
     The LLM is briefed with cookbook prompting principles so its
     translation is enriched (photographic vocab, lock-gaze, literal-text
     handling) — the reconciler is the agent-skill carrier for them.
  4. Compose: scene → menu fragments → reconciled note → avoid.
     This order matches OpenAI's image-gen prompting guide
     (scene → subject → details → constraints to preserve/avoid).

Composition stays deterministic so:

  - Cache hits don't depend on LLM determinism.
  - Stub mode produces a valid prompt without an LLM.
  - The output structure mirrors `/v1/prompt/preview`'s contract directly,
    which T-019 will surface as-is.

Cache: Redis key `reconciler:{sha256}`, TTL 24h. The hashed input includes
the constraints version + model version, so a YAML bump or model rotation
auto-invalidates cached entries without callers having to flush Redis.
`preview()` reads cache but never writes it (avoids polluting the cache
with "what-if" prompts that the user may not actually generate from).
"""

from __future__ import annotations

import hashlib
import json
import logging
import time
from dataclasses import dataclass
from typing import Any

from redis.asyncio import Redis

from app.ai.reconciler_client import ReconcilerClient, get_reconciler_client
from app.core.platform_constraints import load_platform_constraints
from app.prompt.constraints import (
    ReconcileMode,
    get_avoid_constraints_for_mode,
    get_constraints_for_mode,
    get_constraints_version,
)
from app.prompt.errors import prompt_conflict
from app.prompt.menu_fragments import MENU_FRAGMENTS, resolve_menu_fragments

_logger = logging.getLogger(__name__)

_CACHE_TTL_SECONDS = 24 * 3600
_CACHE_KEY_PREFIX = "reconciler:"


@dataclass(frozen=True)
class ReconcileInput:
    mode: ReconcileMode
    menu_selections: dict[str, str] | None = None
    freeform_note: str | None = None
    has_reference_image: bool = False
    has_inpaint_mask: bool = False


@dataclass(frozen=True)
class RemovedSegment:
    original_zh: str
    reason: str


@dataclass(frozen=True)
class ReconcileOutput:
    final_prompt: str
    reconciled_note_en: str
    menu_fragments_en: tuple[str, ...]
    applied_constraints: tuple[str, ...]
    removed_segments: tuple[RemovedSegment, ...]
    llm_latency_ms: int
    cached: bool


class PromptReconciler:
    """Reconcile menu + freeform note + constraints into a final prompt.

    Construct one per process; instances are cheap and keep no per-call
    state. Pass a `ReconcilerClient` (real or stub) for the LLM hop.
    """

    # T-050: SYSTEM_PROMPT bakes in OpenAI's image-gen prompting cookbook
    # (https://developers.openai.com/cookbook/examples/multimodal/image-gen-models-prompting-guide)
    # so the reconciler is the agent-skill carrier for those principles.
    # Updates here automatically shift the cache key via `_logic_version`.
    SYSTEM_PROMPT = (
        "You are a prompt-engineering specialist for an AI character-generation "
        "platform. The character is a virtual avatar for an AI tour-guide system "
        "— asset-quality, not scene/storytelling.\n"
        "\n"
        "OUTPUT TARGETS (your translation will be assembled into prompts for):\n"
        "  - gpt-image-2 — text-to-image and image-to-image edits\n"
        "  - Veo 3.1 — image-to-video\n"
        "\n"
        "MODES you may receive:\n"
        "  - create_base: generate a brand-new character from scratch\n"
        "  - create_base_with_ref: generate a character matching a reference "
        "image's style\n"
        "  - create_alias: edit the existing base character into a new outfit / "
        "variant; identity (face, body, build) MUST be preserved\n"
        "  - create_motion: describe a short motion for an i2v model. "
        "Translate the user's note faithfully; do NOT apply the enrichment "
        "principles below (i2v prompt tuning lives in a separate skill).\n"
        "\n"
        "SCOPE OF THE PROMPTING PRINCIPLES BELOW\n"
        "Apply principles 1-5 ONLY to create_base, create_base_with_ref, and "
        "create_alias (gpt-image targets). For create_motion, follow only the "
        "baseline behaviour described in YOUR TASK / CONFLICT HANDLING — "
        "translate the note and flag conflicts, but skip enrichment.\n"
        "\n"
        "YOUR TASK\n"
        "Given (1) menu selections, (2) a freeform user note in Chinese, and "
        "(3) the platform's scene + avoid constraints, produce TWO outputs:\n"
        "  - reconciled_note_en: natural-English translation of the freeform "
        "note, with any user intent that conflicts with platform constraints "
        "rewritten or removed.\n"
        "  - removed_segments: list of dropped fragments, each with the "
        "original Chinese and the reason.\n"
        "\n"
        "PROMPTING PRINCIPLES (apply when translating)\n"
        "\n"
        "1. STRUCTURE WITHIN THE NOTE\n"
        "   The downstream code already places content in this fixed order:\n"
        "       scene constraints  ->  subject (menu fragments)  ->  YOUR "
        "translation  ->  avoid constraints\n"
        "   Your translation is the 'key details' section. Keep it concrete "
        "and tight. Do NOT restate menu selections or scene constraints — "
        "they are already present in their own slots.\n"
        "\n"
        "2. ENRICH WITH TECHNICAL VOCABULARY (do not invent content)\n"
        "   You MAY add photographic / stylistic vocabulary that is consistent "
        "with the menu selections and the mode:\n"
        "       lens type (50mm, 35mm), lighting direction (soft diffuse, "
        "golden hour, rim light), depth of field (shallow, deep), texture "
        "(visible pores, fabric weave), composition cues (eye-level, "
        "low-angle).\n"
        "   You MUST NOT introduce new subjects, objects, props, scene "
        "elements, or background details that the user did not mention. "
        "More vivid wording yes; extra characters / props / background no.\n"
        "\n"
        "3. PEOPLE-SPECIFIC HINTS (when the user describes the character)\n"
        "   Prefer concrete cues over vague ones:\n"
        "       - Lock gaze: 'looking directly at the camera' / 'looking down "
        "at the book', not just 'looking'.\n"
        "       - Body framing: 'full body visible, feet included'.\n"
        "       - Interactions: 'hands resting on the desk', not 'with hands'.\n"
        "\n"
        "4. LITERAL TEXT IN THE IMAGE\n"
        "   If the user specifies text that must appear in the image (signs, "
        "badges, labels, name tags, tattoos, banners), wrap the literal text "
        "in DOUBLE QUOTES and ALL CAPS. Example:\n"
        '       the badge reads "GUIDE".\n'
        "   For non-Latin scripts, keep the original characters in quotes:\n"
        '       the banner reads "歡迎光臨".\n'
        "   Never paraphrase literal text — it must reach the image model "
        "verbatim.\n"
        "\n"
        "5. EDITS (create_alias) — PRESERVATION DEFAULT\n"
        "   Alias mode is image-to-image. The user's note is an EDIT "
        "instruction, not a fresh description. Translate ONLY the requested "
        "change. If the user implies altering identity (face / proportions / "
        "build), record it in removed_segments. Default behavior: preserve "
        "identity, geometry, pose, lighting, and surrounding objects.\n"
        "\n"
        "CONFLICT HANDLING\n"
        "- For each segment of the user's note that contradicts a platform "
        "  constraint, rewrite or drop it so the constraint wins. Record "
        "  what was dropped and why in removed_segments.\n"
        "- Empty user note: reconciled_note_en is the empty string and "
        "  removed_segments is [].\n"
        "\n"
        "OUTPUT\n"
        "Return JSON matching this schema EXACTLY. No additional fields:\n"
        '  {"reconciled_note_en": str, "removed_segments": '
        '[{"original_zh": str, "reason": str}]}'
    )

    def __init__(self, redis: Redis, client: ReconcilerClient) -> None:
        self.redis = redis
        self.client = client

    async def reconcile(self, inp: ReconcileInput) -> ReconcileOutput:
        return await self._run(inp, write_cache=True)

    async def preview(self, inp: ReconcileInput) -> ReconcileOutput:
        """Same composition as reconcile, but never writes the cache.

        Reads cache when a real reconcile already populated it (preview hits
        the same key shape) — that's deliberate: hitting "進階檢視" right
        after a real generation should be free.
        """
        return await self._run(inp, write_cache=False)

    async def _run(self, inp: ReconcileInput, *, write_cache: bool) -> ReconcileOutput:
        cache_key = self._cache_key(inp)
        cached = await self._read_cache(cache_key)
        if cached is not None:
            return cached

        scene = get_constraints_for_mode(inp.mode)
        avoid = get_avoid_constraints_for_mode(inp.mode)
        menu_fragments = resolve_menu_fragments(inp.menu_selections)
        note = (inp.freeform_note or "").strip()

        if not note:
            # Skip the LLM when there's nothing to translate. Composition
            # stays identical; we save cost + latency.
            output = self._compose_output(
                reconciled_note_en="",
                removed_segments=(),
                scene=scene,
                avoid=avoid,
                menu_fragments=menu_fragments,
                llm_latency_ms=0,
                cached=False,
            )
        else:
            llm_response, latency_ms = await self._call_llm(inp, scene, avoid, menu_fragments)
            reconciled, removed = self._validate_llm_response(llm_response)
            output = self._compose_output(
                reconciled_note_en=reconciled,
                removed_segments=removed,
                scene=scene,
                avoid=avoid,
                menu_fragments=menu_fragments,
                llm_latency_ms=latency_ms,
                cached=False,
            )

        if write_cache:
            await self._write_cache(cache_key, output)
        return output

    def _cache_key(self, inp: ReconcileInput) -> str:
        # `client_identity` (not `config.reconciler_model()`) so toggling
        # AI_STUB_MODE — or otherwise swapping the wired client — invalidates
        # cached entries. Otherwise a stub-mode entry could be served to a
        # subsequent real-model run for up to 24h, returning a degraded
        # prompt with no Chinese-note translation. Codex P2 round-1.
        #
        # `logic_version` hashes the SYSTEM_PROMPT and MENU_FRAGMENTS table
        # so a prompt-template tweak or menu-mapping correction auto-shifts
        # the cache key — no manual version-bump needed. Without this, a
        # product fix to the mapping would still serve old entries for up
        # to 24h. Codex P2 round-2.
        # `menu_selections_ordered` is a list-of-pairs (NOT a sorted dict) so
        # the cache key preserves insertion order. resolve_menu_fragments()
        # composes the final prompt in insertion order, so two callers with
        # the same key/value pairs in different orders MUST get distinct
        # cache slots — otherwise whichever request writes first determines
        # the returned fragment order for both (Codex P2 round-3). Lists
        # serialise positionally in JSON; dicts get re-sorted by sort_keys
        # below, which would defeat the fix.
        payload = {
            "mode": inp.mode.value,
            "menu_selections_ordered": list((inp.menu_selections or {}).items()),
            "freeform_note": (inp.freeform_note or "").strip(),
            "has_reference_image": bool(inp.has_reference_image),
            "has_inpaint_mask": bool(inp.has_inpaint_mask),
            "constraint_version": get_constraints_version(),
            "client_identity": self.client.client_identity,
            "logic_version": self._logic_version(),
        }
        blob = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
        digest = hashlib.sha256(blob.encode("utf-8")).hexdigest()
        return f"{_CACHE_KEY_PREFIX}{digest}"

    def _logic_version(self) -> str:
        """SHA-256 prefix of the reconciler's prompt + menu mapping +
        constraint YAML payload.

        Computed on each cache-key call; the hash is microseconds-cheap
        compared to the LLM hop it gates (`load_platform_constraints` is
        lru_cached). Updating SYSTEM_PROMPT, MENU_FRAGMENTS, OR any
        constraint list in `platform_constraints.yaml` automatically
        shifts the digest and invalidates entries written under the old
        logic — so a YAML wording fix that forgets to bump the manual
        `version:` field still gets picked up safely. Codex P1 (T-050).
        """
        constraints = load_platform_constraints()
        blob = json.dumps(
            {
                "system_prompt": self.SYSTEM_PROMPT,
                "menu_fragments": MENU_FRAGMENTS,
                "constraints_payload": {
                    "base_creation": list(constraints.base_creation),
                    "base_creation_avoid": list(constraints.base_creation_avoid),
                    "alias_creation": list(constraints.alias_creation),
                    "alias_creation_avoid": list(constraints.alias_creation_avoid),
                    "motion_creation": list(constraints.motion_creation),
                    "motion_creation_avoid": list(constraints.motion_creation_avoid),
                },
            },
            sort_keys=True,
            ensure_ascii=False,
        )
        return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:16]

    async def _read_cache(self, key: str) -> ReconcileOutput | None:
        try:
            raw = await self.redis.get(key)
        except Exception:  # noqa: BLE001 — cache is advisory
            _logger.exception("reconciler cache read failed for %s", key)
            return None
        if raw is None:
            return None
        try:
            payload = json.loads(raw)
        except (TypeError, ValueError):
            _logger.warning("reconciler cache value for %s is not JSON; treating as miss", key)
            return None
        try:
            return ReconcileOutput(
                final_prompt=str(payload["final_prompt"]),
                reconciled_note_en=str(payload["reconciled_note_en"]),
                menu_fragments_en=tuple(payload["menu_fragments_en"]),
                applied_constraints=tuple(payload["applied_constraints"]),
                removed_segments=tuple(
                    RemovedSegment(
                        original_zh=str(seg["original_zh"]),
                        reason=str(seg["reason"]),
                    )
                    for seg in payload.get("removed_segments", [])
                ),
                llm_latency_ms=int(payload.get("llm_latency_ms", 0)),
                cached=True,
            )
        except (KeyError, TypeError, ValueError):
            _logger.warning("reconciler cache value for %s has unexpected shape; ignoring", key)
            return None

    async def _write_cache(self, key: str, output: ReconcileOutput) -> None:
        payload: dict[str, Any] = {
            "final_prompt": output.final_prompt,
            "reconciled_note_en": output.reconciled_note_en,
            "menu_fragments_en": list(output.menu_fragments_en),
            "applied_constraints": list(output.applied_constraints),
            "removed_segments": [
                {"original_zh": s.original_zh, "reason": s.reason} for s in output.removed_segments
            ],
            "llm_latency_ms": output.llm_latency_ms,
        }
        try:
            await self.redis.set(
                key,
                json.dumps(payload, ensure_ascii=False),
                ex=_CACHE_TTL_SECONDS,
            )
        except Exception:  # noqa: BLE001 — cache write is advisory
            _logger.exception("reconciler cache write failed for %s", key)

    async def _call_llm(
        self,
        inp: ReconcileInput,
        scene: tuple[str, ...],
        avoid: tuple[str, ...],
        menu_fragments: list[str],
    ) -> tuple[dict[str, Any], int]:
        user_prompt = self._compose_user_prompt(inp, scene, avoid, menu_fragments)
        started = time.perf_counter()
        response = await self.client.call(
            system_prompt=self.SYSTEM_PROMPT,
            user_prompt=user_prompt,
        )
        latency_ms = int((time.perf_counter() - started) * 1000)
        return response, latency_ms

    def _compose_user_prompt(
        self,
        inp: ReconcileInput,
        scene: tuple[str, ...],
        avoid: tuple[str, ...],
        menu_fragments: list[str],
    ) -> str:
        menu_pretty = (
            "\n".join(f"- {k}: {v}" for k, v in (inp.menu_selections or {}).items()) or "(none)"
        )
        scene_pretty = "\n".join(f"- {c}" for c in scene) or "(none)"
        avoid_pretty = "\n".join(f"- {c}" for c in avoid) or "(none)"
        fragments_pretty = "\n".join(f"- {f}" for f in menu_fragments) or "(none)"
        note = (inp.freeform_note or "").strip() or "(empty)"
        return (
            f"Mode: {inp.mode.value}\n"
            f"Has reference image: {inp.has_reference_image}\n"
            f"Has inpaint mask: {inp.has_inpaint_mask}\n"
            f"\nMenu selections:\n{menu_pretty}\n"
            f"\nMenu fragments (already mapped to English; do NOT restate):\n"
            f"{fragments_pretty}\n"
            f"\nUser freeform note (Chinese):\n{note}\n"
            f"\nPlatform scene constraints "
            f"(injected BEFORE your translation; do NOT restate):\n{scene_pretty}\n"
            f"\nPlatform avoid constraints "
            f"(injected AFTER your translation; flag user intent that conflicts):\n"
            f"{avoid_pretty}\n"
        )

    def _validate_llm_response(
        self, payload: dict[str, Any]
    ) -> tuple[str, tuple[RemovedSegment, ...]]:
        """Parse the LLM JSON; raise PROMPT_CONFLICT on shape errors.

        Per planning §8: a malformed reconciler output is treated as an
        irrecoverable failure for this request. The retry loop in the LLM
        client already gave up; pushing an unparseable structure into the
        image model would either crash the worker or feed it Chinese
        verbatim — both worse than failing the task with a structured error.
        """
        if not isinstance(payload, dict):
            raise prompt_conflict(
                problem="Reconciler LLM returned a non-object response.",
                cause="LLM JSON mode produced a non-object payload (string / array / null).",
                fix="Retry the request; persistent failures need a system-prompt review.",
            )
        reconciled = payload.get("reconciled_note_en")
        if not isinstance(reconciled, str):
            raise prompt_conflict(
                problem="Reconciler LLM omitted reconciled_note_en or sent a non-string value.",
                cause="LLM did not honour the required output schema.",
                fix="Retry; if persistent, inspect the system prompt for schema drift.",
            )
        # Codex P2 round-4: missing `removed_segments` is partial schema
        # drift, same severity as a missing `reconciled_note_en`. Previously
        # defaulted to `[]` (via `.get(..., [])`) and silently accepted —
        # cached output for that input lost the audit trail until 24h expiry.
        # Treat absence as failure, matching how the per-item validation
        # below treats malformed entries.
        if "removed_segments" not in payload:
            raise prompt_conflict(
                problem="Reconciler LLM omitted the required `removed_segments` field.",
                cause="LLM did not honour the required output schema.",
                fix="Retry; if persistent, inspect the system prompt for schema drift.",
            )
        removed_raw = payload["removed_segments"]
        if not isinstance(removed_raw, list):
            raise prompt_conflict(
                problem="Reconciler LLM returned removed_segments as a non-list value.",
                cause="LLM did not honour the required output schema.",
                fix="Retry; if persistent, inspect the system prompt for schema drift.",
            )
        # Strict per-item validation. Codex P2 round-3: previously skipped
        # malformed entries with `continue`, which silently dropped
        # conflict-audit info and cached a partially-correct output. The
        # contract requires every removed_segments entry to be
        # {original_zh: str, reason: str} — partial schema drift should
        # surface as PROMPT_CONFLICT, same as a missing top-level field.
        removed: list[RemovedSegment] = []
        for seg in removed_raw:
            if not isinstance(seg, dict):
                raise prompt_conflict(
                    problem="Reconciler LLM returned a removed_segments item "
                    "that is not an object.",
                    cause="LLM did not honour the required output schema.",
                    fix="Retry; if persistent, inspect the system prompt for schema drift.",
                )
            original = seg.get("original_zh")
            reason = seg.get("reason")
            if not isinstance(original, str) or not isinstance(reason, str):
                raise prompt_conflict(
                    problem="Reconciler LLM returned a removed_segments item "
                    "with a missing or non-string field "
                    "(original_zh / reason).",
                    cause="LLM did not honour the required output schema.",
                    fix="Retry; if persistent, inspect the system prompt for schema drift.",
                )
            removed.append(RemovedSegment(original_zh=original, reason=reason))
        return reconciled, tuple(removed)

    def _compose_output(
        self,
        *,
        reconciled_note_en: str,
        removed_segments: tuple[RemovedSegment, ...],
        scene: tuple[str, ...],
        avoid: tuple[str, ...],
        menu_fragments: list[str],
        llm_latency_ms: int,
        cached: bool,
    ) -> ReconcileOutput:
        # T-050: cookbook ordering — scene → subject (menu) → details
        # (note) → avoid (preserve/avoid). The image model reads "what to
        # preserve, what to avoid" last, so it stays salient against the
        # other blocks during attention pooling.
        #
        # Strip trailing whitespace AND a trailing period from each part
        # before the `". "` join so parts that already end in a period
        # (typical when the LLM emits literal-text examples like
        # `the badge reads "GUIDE".` per SYSTEM_PROMPT principle 4) don't
        # produce a `..` boundary. Whitespace must be stripped first
        # because real LLM responses can wrap with `\n` after the period
        # (`"GUIDE".\n`); rstrip(".") on that string sees `\n` and leaves
        # the period intact. Codex P2 (T-050) round-3.
        def _trim(p: str) -> str:
            return p.rstrip().rstrip(".").rstrip()

        parts = [
            trimmed
            for trimmed in (
                _trim(p)
                for p in (
                    ", ".join(scene) if scene else "",
                    ", ".join(menu_fragments) if menu_fragments else "",
                    reconciled_note_en,
                    ", ".join(avoid) if avoid else "",
                )
            )
            if trimmed
        ]
        final_prompt = ". ".join(parts) + ("." if parts else "")
        return ReconcileOutput(
            final_prompt=final_prompt,
            reconciled_note_en=reconciled_note_en,
            menu_fragments_en=tuple(menu_fragments),
            applied_constraints=tuple(scene) + tuple(avoid),
            removed_segments=removed_segments,
            llm_latency_ms=llm_latency_ms,
            cached=cached,
        )


def get_prompt_reconciler(redis: Redis) -> PromptReconciler:
    """Convenience factory: real or stub LLM client based on AI_STUB_MODE.

    T-017 (worker) and T-019 (preview endpoint) call this so they don't
    have to know about the LLM client wiring.
    """
    return PromptReconciler(redis=redis, client=get_reconciler_client(redis))
