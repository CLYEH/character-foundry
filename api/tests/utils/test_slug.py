"""Slug generation unit tests (no DB).

Exercises planning/data/db-schema.md §4 step-by-step. The DB-collision
fallback path is covered through stub `is_taken` predicates so we
don't need a real characters table.
"""

from __future__ import annotations

import re

import pytest

from app.utils import slug as slug_module
from app.utils.slug import generate_unique_slug, slugify


def test_chinese_to_pinyin_kebab() -> None:
    # The canonical example from db-schema.md §4 — same input, same
    # tone-free pinyin transliteration.
    assert slugify("古風導覽員-小雅") == "gu-feng-dao-lan-yuan-xiao-ya"


def test_strips_dangling_dashes() -> None:
    assert slugify("--hello---world--") == "hello-world"


def test_collapses_underscores_and_spaces() -> None:
    assert slugify("Foo Bar_Baz") == "foo-bar-baz"


def test_drops_invalid_chars_before_truncation() -> None:
    assert slugify("Foo!@#Bar?Baz.Quux") == "foo-bar-baz-quux"


def test_truncates_at_60_chars() -> None:
    base = "a" * 70
    out = slugify(base)
    assert len(out) <= 60
    assert out == "a" * 60


@pytest.mark.asyncio
async def test_generate_unique_slug_returns_base_when_free() -> None:
    async def is_taken(_: str) -> bool:
        return False

    out = await generate_unique_slug("阿雅", is_taken=is_taken)
    assert out == slugify("阿雅")


@pytest.mark.asyncio
async def test_generate_unique_slug_appends_numeric_suffix() -> None:
    base = slugify("阿雅")
    taken = {base}

    async def is_taken(s: str) -> bool:
        return s in taken

    out = await generate_unique_slug("阿雅", is_taken=is_taken)
    assert out == f"{base}-2"


@pytest.mark.asyncio
async def test_generate_unique_slug_walks_through_collisions() -> None:
    base = slugify("阿雅")
    # Fill `base` and `base-2` through `base-9` so the algorithm has to
    # walk the suffix chain before finding free space.
    taken = {base} | {f"{base}-{n}" for n in range(2, 10)}

    async def is_taken(s: str) -> bool:
        return s in taken

    out = await generate_unique_slug("阿雅", is_taken=is_taken)
    assert out == f"{base}-10"


@pytest.mark.asyncio
async def test_generate_unique_slug_falls_back_to_uuid_prefix() -> None:
    """100 numeric suffixes exhausted → 4-char uuid prefix."""
    base = slugify("阿雅")
    taken = {base} | {f"{base}-{n}" for n in range(2, 101)}

    async def is_taken(s: str) -> bool:
        return s in taken

    out = await generate_unique_slug("阿雅", is_taken=is_taken)
    assert out != base
    assert not out.endswith("-101")
    # Format: "<4-hex>-<base>" — match the 4-char prefix shape.
    assert re.match(r"^[0-9a-f]{4}-", out) is not None
    # Total length still respects the column limit.
    assert len(out) <= 60


@pytest.mark.asyncio
async def test_generate_unique_slug_handles_empty_slugify_result(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Pathological input (all punctuation) → should still emit a
    non-empty slug starting from a uuid prefix."""

    # Patch the module-level slugify symbol so generate_unique_slug
    # actually picks up the empty result. Patching the imported alias
    # in this test file would not affect the call inside slug.py.
    monkeypatch.setattr(slug_module, "slugify", lambda _name: "")

    async def is_taken(_: str) -> bool:
        return False

    out = await generate_unique_slug("???", is_taken=is_taken)
    assert out
    assert re.match(r"^[0-9a-f]{4}$", out)
