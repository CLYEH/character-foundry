"""Compose `Checkpoint.prompt_summary` (UI quick-glance label).

Format per planning/ux/user-flows.md §6 row 4:
- Join menu selection values with the CJK middle-dot separator (`・`).
- Append the user's freeform note, truncated to 80 codepoints + `...`.

Both pieces are optional. If both are missing, return an empty string.
The string is purely cosmetic — full prompt is on the checkpoint row,
the preview endpoint surfaces it in detail (T-019).
"""

from __future__ import annotations

from typing import Any

_MIDDLE_DOT = "・"
_FREEFORM_LIMIT = 80


def build_prompt_summary(
    *,
    menu_selections: dict[str, Any] | None,
    freeform_note: str | None,
) -> str:
    parts: list[str] = []

    if menu_selections:
        # Insertion-ordered iteration matches the order the user picked
        # the values in the UI — reading the summary back to the user
        # should preserve their narrative ("女性・大眼・黑長髮"), not
        # alphabetise it.
        values = [str(v).strip() for v in menu_selections.values() if v not in (None, "")]
        if values:
            parts.append(_MIDDLE_DOT.join(values))

    note = (freeform_note or "").strip()
    if note:
        if len(note) > _FREEFORM_LIMIT:
            parts.append(note[:_FREEFORM_LIMIT] + "...")
        else:
            parts.append(note)

    return _MIDDLE_DOT.join(parts) if parts else ""
