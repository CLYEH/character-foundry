"""PromptReconciler — turn user input into a final English image/video prompt.

Pipeline (per planning/backend/prompt-reconciler.md):
  1. Resolve menu fragments deterministically from the static map.
  2. Look up applicable platform constraints by mode.
  3. (LLM) Translate the freeform Chinese note + flag/rewrite conflicts.
  4. Compose: constraints + menu fragments + reconciled_note_en.

The LLM is responsible only for step 3 (translation + conflict resolution).
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
from app.prompt.constraints import (
    ReconcileMode,
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

    SYSTEM_PROMPT = (
        "You are a prompt engineering assistant for an AI character "
        "generation platform.\n"
        "\n"
        "Your job: given (1) user menu selections, (2) a user freeform note "
        "in Chinese, and (3) platform-level fixed constraints, produce two "
        "things:\n"
        "  - A natural English translation of the freeform note, with any "
        "    segments that conflict with platform constraints rewritten or "
        "    removed.\n"
        "  - A list of removed segments with the reason each was dropped.\n"
        "\n"
        "RULES:\n"
        "1. Translate the freeform Chinese note to natural English.\n"
        "2. Identify any user intent that conflicts with the platform "
        "   constraints. Rewrite or remove the conflicting parts so the "
        "   constraint wins. Record what was removed and why.\n"
        "3. Do NOT add content the user did not imply (no creative bloat).\n"
        "4. If the user note is empty, return reconciled_note_en as the "
        "   empty string.\n"
        "5. Return JSON matching this schema EXACTLY:\n"
        '   {"reconciled_note_en": str, "removed_segments": '
        '[{"original_zh": str, "reason": str}]}\n'
        "Do NOT include any other fields."
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

        constraints = get_constraints_for_mode(inp.mode)
        menu_fragments = resolve_menu_fragments(inp.menu_selections)
        note = (inp.freeform_note or "").strip()

        if not note:
            # Skip the LLM when there's nothing to translate. Composition
            # stays identical; we save cost + latency.
            output = self._compose_output(
                reconciled_note_en="",
                removed_segments=(),
                constraints=constraints,
                menu_fragments=menu_fragments,
                llm_latency_ms=0,
                cached=False,
            )
        else:
            llm_response, latency_ms = await self._call_llm(inp, constraints, menu_fragments)
            reconciled, removed = self._validate_llm_response(llm_response)
            output = self._compose_output(
                reconciled_note_en=reconciled,
                removed_segments=removed,
                constraints=constraints,
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
        payload = {
            "mode": inp.mode.value,
            "menu_selections": dict(sorted((inp.menu_selections or {}).items())),
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
        """SHA-256 prefix of the reconciler's prompt + menu mapping.

        Computed on each cache-key call; the hash is microseconds-cheap
        compared to the LLM hop it gates. Updating SYSTEM_PROMPT or
        MENU_FRAGMENTS in code automatically shifts the digest and
        invalidates entries written under the old logic.
        """
        blob = json.dumps(
            {
                "system_prompt": self.SYSTEM_PROMPT,
                "menu_fragments": MENU_FRAGMENTS,
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
        constraints: tuple[str, ...],
        menu_fragments: list[str],
    ) -> tuple[dict[str, Any], int]:
        user_prompt = self._compose_user_prompt(inp, constraints, menu_fragments)
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
        constraints: tuple[str, ...],
        menu_fragments: list[str],
    ) -> str:
        menu_pretty = (
            "\n".join(f"- {k}: {v}" for k, v in (inp.menu_selections or {}).items()) or "(none)"
        )
        constraints_pretty = "\n".join(f"- {c}" for c in constraints)
        fragments_pretty = "\n".join(f"- {f}" for f in menu_fragments) or "(none)"
        note = (inp.freeform_note or "").strip() or "(empty)"
        return (
            f"Mode: {inp.mode.value}\n"
            f"Has reference image: {inp.has_reference_image}\n"
            f"Has inpaint mask: {inp.has_inpaint_mask}\n"
            f"\nMenu selections:\n{menu_pretty}\n"
            f"\nMenu fragments (already mapped to English):\n{fragments_pretty}\n"
            f"\nUser freeform note (Chinese):\n{note}\n"
            f"\nPlatform constraints (must respect):\n{constraints_pretty}\n"
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
        removed_raw = payload.get("removed_segments", [])
        if not isinstance(removed_raw, list):
            raise prompt_conflict(
                problem="Reconciler LLM returned removed_segments as a non-list value.",
                cause="LLM did not honour the required output schema.",
                fix="Retry; if persistent, inspect the system prompt for schema drift.",
            )
        removed: list[RemovedSegment] = []
        for seg in removed_raw:
            if not isinstance(seg, dict):
                continue
            original = seg.get("original_zh")
            reason = seg.get("reason")
            if isinstance(original, str) and isinstance(reason, str):
                removed.append(RemovedSegment(original_zh=original, reason=reason))
        return reconciled, tuple(removed)

    def _compose_output(
        self,
        *,
        reconciled_note_en: str,
        removed_segments: tuple[RemovedSegment, ...],
        constraints: tuple[str, ...],
        menu_fragments: list[str],
        llm_latency_ms: int,
        cached: bool,
    ) -> ReconcileOutput:
        # Ordering per planning §5 rule 3: scene constraints → character
        # attributes (menu) → user-specific (note). Sentences keep gpt-image-2
        # focused on each block; downstream tweaks can re-tokenise safely.
        parts: list[str] = []
        if constraints:
            parts.append(", ".join(constraints))
        if menu_fragments:
            parts.append(", ".join(menu_fragments))
        if reconciled_note_en:
            parts.append(reconciled_note_en)
        final_prompt = ". ".join(parts) + ("." if parts else "")
        return ReconcileOutput(
            final_prompt=final_prompt,
            reconciled_note_en=reconciled_note_en,
            menu_fragments_en=tuple(menu_fragments),
            applied_constraints=tuple(constraints),
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
