"""Per-mode platform constraints surface for the prompt reconciler (T-015).

Wraps `app.core.platform_constraints.load_platform_constraints` so the
reconciler doesn't have to know that:

  - `alias_creation` is documented to "inherit base_creation rules" in the
    YAML — we flatten that here so callers see the actual constraint list,
    not the inheritance marker (which would otherwise leak into the LLM
    prompt and the final image prompt).
  - The set of legal modes is enumerated in `ReconcileMode`; an unknown
    mode is a programming error, not a runtime issue, so we raise.
"""

from __future__ import annotations

from enum import StrEnum

from app.core.platform_constraints import (
    PlatformConstraints,
    load_platform_constraints,
)


class ReconcileMode(StrEnum):
    CREATE_BASE = "create_base"
    CREATE_BASE_WITH_REF = "create_base_with_ref"
    CREATE_ALIAS = "create_alias"
    CREATE_MOTION = "create_motion"


def get_constraints_for_mode(
    mode: ReconcileMode,
    *,
    source: PlatformConstraints | None = None,
) -> tuple[str, ...]:
    cs = source or load_platform_constraints()
    if mode in (ReconcileMode.CREATE_BASE, ReconcileMode.CREATE_BASE_WITH_REF):
        return cs.base_creation
    if mode is ReconcileMode.CREATE_ALIAS:
        # alias_creation YAML contains an `inherits base_creation rules` marker;
        # flatten to the real base entries plus alias-specific items so the
        # LLM never sees a meta-instruction in the constraint list.
        extra = tuple(c for c in cs.alias_creation if "inherits" not in c.lower())
        return cs.base_creation + extra
    if mode is ReconcileMode.CREATE_MOTION:
        return cs.motion_creation
    raise ValueError(f"unknown reconcile mode: {mode!r}")


def get_constraints_version(*, source: PlatformConstraints | None = None) -> str:
    """Current constraints version. Used in the cache key so a YAML bump
    auto-invalidates entries without callers having to flush Redis."""
    return (source or load_platform_constraints()).version
