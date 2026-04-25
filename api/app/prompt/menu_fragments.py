"""Menu key/value → English fragment table (Phase 1 minimal, T-015).

Per ticket: ship the smallest viable subset; UX team fills the full enum
during M5 (STATUS.md). Unknown keys/values pass through verbatim with a
debug log so the reconciler stays resilient when UX adds new options before
backend wires their mapping.

The entries here come from planning/backend/prompt-reconciler.md §6; treat
that doc as authoritative when adding new categories.
"""

from __future__ import annotations

import logging
from typing import Final

_logger = logging.getLogger(__name__)


MENU_FRAGMENTS: Final[dict[str, dict[str, str]]] = {
    "gender": {
        "male": "adult man",
        "female": "adult woman",
        "nonbinary": "androgynous person",
    },
    "age": {
        "child": "child, age 8-12",
        "teen": "teenager, age 14-18",
        "young_adult": "young adult, age 20-30",
        "middle_aged": "middle-aged, age 40-55",
        "elderly": "elderly, age 60+",
    },
    "eye_shape": {
        "large_round": "large round expressive eyes",
        "almond": "almond-shaped eyes",
        "upturned": "upturned cat-eyes",
    },
    "nose": {
        "narrow": "narrow nose",
        "rounded": "rounded nose",
    },
    "hair": {
        "short": "short hair",
        "long": "long hair",
        "ponytail": "tied in a ponytail",
    },
    "skin_tone": {
        "fair": "fair skin",
        "medium": "medium skin tone",
        "tan": "tanned skin",
        "dark": "dark skin",
    },
    "build": {
        "slim": "slim build",
        "average": "average build",
        "athletic": "athletic build",
    },
    "style": {
        "realistic": "photorealistic portrait",
        "anime": "anime style, 2D illustration",
        "ink_wash": "traditional Chinese ink wash painting style",
        "watercolor": "soft watercolor illustration",
    },
}


def resolve_menu_fragments(menu_selections: dict[str, str] | None) -> list[str]:
    """Map a `{category: option}` dict to an ordered list of English fragments.

    Unknown categories or options pass through as the raw `option` string
    (debug-logged), so a new option from UX doesn't break generation — the
    LLM will treat the bare token as descriptive English. Order is
    insertion-order from the input dict, matching how UX serialises the
    selection panel.
    """
    if not menu_selections:
        return []
    fragments: list[str] = []
    for category, option in menu_selections.items():
        category_table = MENU_FRAGMENTS.get(category)
        if category_table is None:
            _logger.debug("menu_fragments: unknown category %s; passing option through", category)
            fragments.append(str(option))
            continue
        fragment = category_table.get(option)
        if fragment is None:
            _logger.debug(
                "menu_fragments: unknown option %s for category %s; passing through",
                option,
                category,
            )
            fragments.append(str(option))
            continue
        fragments.append(fragment)
    return fragments
