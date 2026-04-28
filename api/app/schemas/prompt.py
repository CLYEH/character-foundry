"""DTOs for `POST /v1/prompt/preview` (T-019).

Wire enum is the 3-mode product surface (`create_base | create_alias |
create_motion`). The reconciler's 4th mode `CREATE_BASE_WITH_REF` is an
internal fan-out the route resolves from `reference_image_ids`, so the
public API doesn't ask callers to know that distinction.

`mask` is accepted but its contents are ignored on the wire — only
`mask is not None` flows through as `has_inpaint_mask`. The full mask
schema lands with the alias inpaint flow in Sprint 3; preserving the
field shape here means a frontend that already passes mask data won't
need a request rewrite when that lands.
"""

from __future__ import annotations

import uuid
from typing import Any, Literal

from pydantic import BaseModel

WirePromptMode = Literal["create_base", "create_alias", "create_motion"]


class PromptPreviewRequest(BaseModel):
    mode: WirePromptMode
    # `dict[str, str]` (not `dict[str, Any]`) so the wire rejects e.g.
    # `{"age": 25}` upfront. The reconciler tolerates non-strings via a
    # `str(option)` fallback in `resolve_menu_fragments`, but the cache
    # key serialises `25` and `"25"` to different JSON, so a sloppy
    # frontend would fragment the cache for the same logical selection.
    # `CreateCheckpointRequest` (T-016) still accepts `dict[str, Any]`
    # — that's a separate hardening pass; the wire seam shouldn't loosen
    # here just to match a known-loose sibling.
    menu_selections: dict[str, str] | None = None
    freeform_note: str | None = None
    reference_image_ids: list[uuid.UUID] | None = None
    mask: dict[str, Any] | None = None


class PromptPreviewResponse(BaseModel):
    platform_constraints: str
    reconciled_note_en: str
    menu_fragments: list[str]
    final_prompt: str
