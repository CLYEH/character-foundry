"""Input schema for the `prompt.preview` MCP tool (T-088).

The wrapped endpoint `POST /v1/prompt/preview` takes a discriminated union
(`PromptPreviewRequest`) as its request body. FastMCP derives a tool's wire
schema from the handler signature, so the handler takes a single `request`
argument of that union type; this wrapper model carries the same shape as
the registry's declarative `input_schema`. Output reuses
`app.schemas.prompt.PromptPreviewResponse`.
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from app.schemas.prompt import PromptPreviewRequest


class PromptPreviewInput(BaseModel):
    """Input for `prompt.preview` — one `request` field carrying the
    mode-discriminated preview body (`create_base` / `create_alias` /
    `create_motion`), identical to the `/v1/prompt/preview` request body."""

    request: PromptPreviewRequest = Field(
        ...,
        description="Mode-discriminated prompt-preview request "
        "(create_base / create_alias / create_motion).",
    )
