"""Pydantic DTOs for the Character resource.

Mirrors planning/backend/api-shape.md §5.1 + §6.1 + §6.2.
"""

from __future__ import annotations

import re
import uuid
from datetime import datetime
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

from app.schemas.creation_session import CreationSessionDTO

# Mirrors the DB CHECK constraint (`chk_characters_name_chars`) byte-for-byte:
# CJK Unified Ideographs U+4E00–U+9FFF + ASCII alphanumerics + `_-`. Kept as a
# Python regex so we can surface a friendly 400 instead of letting the
# constraint trip a generic IntegrityError. If you change one side, change
# the other — drift means a name passes the API check but trips a 500 at
# INSERT (Codex review caught `〇` U+3007 here previously).
_NAME_RE = re.compile(r"^[一-鿿a-zA-Z0-9_\-]+$")
NameStr = Annotated[
    str,
    StringConstraints(min_length=1, max_length=50, strip_whitespace=True),
]

InputMode = Literal["template", "reference"]


def name_pattern_ok(value: str) -> bool:
    """Single source of truth for the name regex check.

    Hand-rolled rather than a Pydantic `pattern=` because we want a
    domain-specific 400 with the structured AgentError envelope, not
    the default 422 Pydantic emits for pattern mismatches.
    """
    return bool(_NAME_RE.match(value))


class OwnerSummary(BaseModel):
    """Embedded owner — just enough for "Created by @alice"."""

    id: uuid.UUID
    name: str


class CopiedFromSummary(BaseModel):
    character_id: uuid.UUID
    name: str


class MotionsSummaryBase(BaseModel):
    preset_generated: int = 0
    custom_count: int = 0


class MotionsSummaryAlias(MotionsSummaryBase):
    alias_id: uuid.UUID


class MotionsSummary(BaseModel):
    base: MotionsSummaryBase = Field(default_factory=MotionsSummaryBase)
    aliases: list[MotionsSummaryAlias] = Field(default_factory=list)


class CharacterDetailCreationSessionRef(BaseModel):
    """Embedded session ref on `CharacterDetail` (api-shape §6.2).

    Status is restricted to `in_progress | abandoned` — `completed`
    sessions always coincide with `base != null`, in which case the
    serializer emits `creation_session = null` instead of this ref.
    """

    id: uuid.UUID
    status: Literal["in_progress", "abandoned"]


class CharacterDTO(BaseModel):
    """List-card shape (api-shape §6.1)."""

    model_config = ConfigDict(from_attributes=False)

    id: uuid.UUID
    name: str
    slug: str
    owner: OwnerSummary
    base_thumbnail_url: str | None = None
    alias_count: int = 0
    motion_count: int = 0
    created_at: datetime
    updated_at: datetime


class CharacterDetailDTO(BaseModel):
    """Detail-page shape (api-shape §6.2). Sprint 2 leaves aliases and
    motion counts at their zero values until later tickets backfill them.

    `creation_session` is populated only when `base` is null — `base`
    being set means a Base was confirmed and the session is logically
    closed (the serializer skips the session lookup in that case to
    keep the contract crisp and the payload small).
    """

    id: uuid.UUID
    name: str
    slug: str
    owner: OwnerSummary
    base: dict[str, object] | None = None
    aliases: list[dict[str, object]] = Field(default_factory=list)
    motions_summary: MotionsSummary = Field(default_factory=MotionsSummary)
    creation_session: CharacterDetailCreationSessionRef | None = None
    copied_from: CopiedFromSummary | None = None
    created_at: datetime
    updated_at: datetime


class CreateCharacterRequest(BaseModel):
    name: NameStr
    input_mode: InputMode


class PatchCharacterRequest(BaseModel):
    name: NameStr


class CreateCharacterResponse(BaseModel):
    """Returned by `POST /v1/characters` — bundles the freshly-created
    character with the bootstrapped session so the caller doesn't need a
    second round-trip to start the checkpoint flow."""

    character: CharacterDTO
    creation_session: CreationSessionDTO


class CharacterResponse(BaseModel):
    character: CharacterDTO


class CharacterDetailResponse(BaseModel):
    character: CharacterDetailDTO


class CharacterListResponse(BaseModel):
    items: list[CharacterDTO]
    next_cursor: str | None = None
