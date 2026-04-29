"""DTOs for `POST /v1/prompt/preview` (T-019 + T-035).

Wire surface is a discriminated union by `mode` so the OpenAPI schema —
and any generated TS / Python client — narrows on the same field the
route uses to dispatch:

  - `create_base`  : the original Sprint-2 surface (T-019). Now also
    accepts `base_checkpoint_id` so a remix preview can faithfully reflect
    the worker-side image2image + has_reference_image=True path
    (closes STATUS.md backlog S2-5).
  - `create_alias` : Sprint-3 alias mode (text / image / inpaint / mixed).
    Carries `character_id` so the service can derive the base image
    surface, and an opt-in `mask: MaskInput` for inpaint/mixed.
  - `create_motion`: Sprint-3 motion mode (preset_* + custom). Carries
    `parent_type` + `parent_id` for the base/alias the motion will be
    attached to.

`MaskInput` is its own model so the upload-then-reference contract from
T-031 (`POST .../aliases/masks` returns `mask_id`, body uses
`{ mask: { mask_id } }`) shows up in OpenAPI rather than being hidden
behind a free-form `dict`. Closes STATUS.md backlog S3-1.
"""

from __future__ import annotations

import uuid
from typing import Annotated, Literal

from pydantic import BaseModel, Field

WirePromptMode = Literal["create_base", "create_alias", "create_motion"]

AliasInputMode = Literal["text", "image", "inpaint", "mixed"]

MotionParentType = Literal["base", "alias"]

MotionType = Literal[
    "preset_wave",
    "preset_nod",
    "preset_gesture",
    "preset_happy",
    "preset_idle",
    "custom",
]

# `motion_template_used` widens MotionType: preset_* values pass through
# verbatim (the worker reads the preset prompt from a static template),
# while `custom` is reported as `custom_reconciled` so callers can tell
# "user note went through the LLM" apart from "preset template applied".
MotionTemplateUsed = Literal[
    "preset_wave",
    "preset_nod",
    "preset_gesture",
    "preset_happy",
    "preset_idle",
    "custom_reconciled",
]


class MaskInput(BaseModel):
    """Reference to a mask uploaded via the alias-mask upload endpoint
    (T-031: `POST /v1/characters/{id}/aliases/masks` → returns
    `mask_id`).

    The wire field is `{ mask_id: UUID }` — only the identifier travels
    in the alias-create / preview body. The actual mask bytes stay in
    the storage backend until the alias worker reads them.
    """

    mask_id: uuid.UUID


class CreateBasePreviewRequest(BaseModel):
    mode: Literal["create_base"]
    # `dict[str, str]` (not `dict[str, Any]`) so the wire rejects e.g.
    # `{"age": 25}` upfront. The reconciler tolerates non-strings via a
    # `str(option)` fallback in `resolve_menu_fragments`, but the cache
    # key serialises `25` and `"25"` to different JSON, so a sloppy
    # frontend would fragment the cache for the same logical selection.
    menu_selections: dict[str, str] | None = None
    freeform_note: str | None = None
    reference_image_ids: list[uuid.UUID] | None = None
    # `base_checkpoint_id` is the source checkpoint a remix preview is
    # branching from. Its presence flips the worker into image2image
    # with `has_reference_image=True`; preview now mirrors that signal
    # so the modal renders the same prompt the worker would build.
    # Closes STATUS.md S2-5.
    base_checkpoint_id: uuid.UUID | None = None


class CreateAliasPreviewRequest(BaseModel):
    mode: Literal["create_alias"]
    character_id: uuid.UUID
    input_mode: AliasInputMode
    freeform_note: str | None = None
    reference_image_ids: list[uuid.UUID] | None = None
    mask: MaskInput | None = None


class CreateMotionPreviewRequest(BaseModel):
    mode: Literal["create_motion"]
    parent_type: MotionParentType
    parent_id: uuid.UUID
    motion_type: MotionType
    description: str | None = None


PromptPreviewRequest = Annotated[
    CreateBasePreviewRequest | CreateAliasPreviewRequest | CreateMotionPreviewRequest,
    Field(discriminator="mode"),
]


class DerivedFromInfo(BaseModel):
    """Alias-mode response surface — the base the alias will be derived
    from. `base_image_url` is a short-lived signed URL the modal uses
    as `<img src>`."""

    base_id: uuid.UUID
    base_image_url: str


class MotionParentInfo(BaseModel):
    """Motion-mode response surface — the base or alias the motion will
    animate. `image_url` is a signed URL of the parent's still frame."""

    type: MotionParentType
    id: uuid.UUID
    image_url: str


class PromptPreviewResponse(BaseModel):
    platform_constraints: str
    reconciled_note_en: str
    menu_fragments: list[str]
    final_prompt: str
    # Per-mode optional fields. Exactly one of these blocks is populated
    # per response (matching the request mode); the others stay None so
    # OpenAPI can keep a single response shape rather than a second
    # discriminated union purely for narrowing.
    derived_from: DerivedFromInfo | None = None
    parent: MotionParentInfo | None = None
    motion_template_used: MotionTemplateUsed | None = None
