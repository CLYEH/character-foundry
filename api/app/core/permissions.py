"""Authorization helpers shared across resource services.

Phase 1 has a single team (B5) so all checks reduce to ownership: any
authenticated user can read team-scoped resources, but only the owner
may PATCH / DELETE / RESTORE. Encapsulated here so the rule lives in one
place — services and route guards both call into this module rather
than re-implementing the comparison.
"""

from __future__ import annotations

import uuid

from app.core.errors import auth_insufficient_permission, not_found_character
from app.models.character import Character
from app.models.user import User


def assert_can_read_character(character: Character, user: User) -> None:
    """Read access: same team. Cross-team requests collapse to 404 so the
    response doesn't reveal whether a character exists outside the
    caller's team."""
    if character.team_id != user.team_id:
        raise not_found_character()


def assert_can_modify_character(character: Character, user: User) -> None:
    """Write access: must be the owner.

    Cross-team callers see 404 (same rationale as `assert_can_read_character`);
    same-team-but-not-owner callers see 403 so a frontend can render
    "you can view but not edit" affordances. The order matters — collapse
    cross-team to 404 BEFORE leaking the 403.
    """
    assert_can_read_character(character, user)
    if character.owner_id != user.id:
        raise auth_insufficient_permission()


def is_owner(character: Character, user_id: uuid.UUID) -> bool:
    return character.owner_id == user_id
