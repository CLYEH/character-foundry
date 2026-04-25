"""Slug generation per planning/data/db-schema.md §4.

The deterministic part (pinyin → kebab → 60-char trim) is in `slugify`.
The collision part (`-2`, `-3`, ..., or UUID prefix fallback) lives in
`generate_unique_slug` so callers can pass an async predicate that hits
their own DB scope (per-owner uniqueness, soft-delete-aware).
"""

from __future__ import annotations

import re
import uuid
from collections.abc import Awaitable, Callable

from pypinyin import Style, lazy_pinyin

_MAX_LEN = 60
_MAX_NUMERIC_SUFFIX_TRIES = 100
_UUID_PREFIX_LEN = 4

_INVALID_CHARS_RE = re.compile(r"[^a-z0-9-]+")
_REPEATED_DASH_RE = re.compile(r"-{2,}")


def slugify(name: str) -> str:
    """Deterministic slug step (pre-collision check).

    Empty result is possible if `name` is all punctuation; callers should
    fall back to a random uuid prefix in that pathological case.
    """
    # `lazy_pinyin` leaves ASCII alone and transliterates CJK to space-
    # separated syllables. Style.NORMAL is tone-free per spec.
    pieces = lazy_pinyin(name, style=Style.NORMAL)
    raw = " ".join(p for p in pieces if p).lower()
    # Collapse all whitespace / underscores into hyphens before stripping
    # invalid chars so multi-word inputs like "古風 導覽員_小雅" survive.
    raw = re.sub(r"[\s_]+", "-", raw)
    raw = _INVALID_CHARS_RE.sub("-", raw)
    raw = _REPEATED_DASH_RE.sub("-", raw)
    raw = raw.strip("-")
    return raw[:_MAX_LEN].rstrip("-")


async def generate_unique_slug(
    name: str,
    *,
    is_taken: Callable[[str], Awaitable[bool]],
) -> str:
    """Return a slug that's unique per the supplied `is_taken` predicate.

    Steps:
      1. `slugify(name)` → base
      2. If `base` is empty (name was all punctuation / non-CJK symbols),
         start from a random uuid4 prefix so we always emit something
         valid against the DB CHECK pattern.
      3. Probe `base`, `base-2`, ..., `base-100` in order.
      4. Still taken → prepend a 4-char uuid4 prefix to the original
         `base` (per db-schema §4 "加 UUID prefix 4 碼").
    """
    base = slugify(name) or uuid.uuid4().hex[:_UUID_PREFIX_LEN]
    if not await is_taken(base):
        return base

    for n in range(2, _MAX_NUMERIC_SUFFIX_TRIES + 1):
        suffix = f"-{n}"
        # Trim base so total length stays within the 60-char column limit
        # imposed by the `characters.slug VARCHAR(60)` definition. Strip
        # any dangling hyphens left by truncation so we don't double up
        # on `-` between base and the numeric suffix.
        truncated = base[: _MAX_LEN - len(suffix)].rstrip("-")
        candidate = f"{truncated}{suffix}"
        if not await is_taken(candidate):
            return candidate

    # 100 collisions and counting — fall back to a uuid prefix. Re-slug
    # in case the prefixed base would otherwise overflow 60 chars.
    prefix = uuid.uuid4().hex[:_UUID_PREFIX_LEN]
    prefixed = f"{prefix}-{base}"[:_MAX_LEN].rstrip("-")
    return prefixed
