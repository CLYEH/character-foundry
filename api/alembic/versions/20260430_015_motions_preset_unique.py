"""motions preset-slot uniqueness (T-033 Codex P2 fix)

Add partial UNIQUE indexes so the F-20 "5 fixed preset slots per parent"
invariant is enforced at DB write time, not just by the service-layer
TOCTOU pre-check in `motion_service.find_active_preset_for_parent`.

Without these indexes, two concurrent `preset_wave` enqueues under the
same Base both pass the application-level read and both worker INSERTs
commit (the only existing UNIQUE on motions is per-parent name, not
per-parent motion_type). That violates F-20 + duplicates the Veo cost.

The worker's IntegrityError handler in `run_create_motion` already maps
`uq_motions_base_motion_type` → `CONFLICT_PRESET_ALREADY_EXISTS`, so this
migration just plugs in the durable guard that branch was already
prepared for.

Custom motions are deliberately excluded via the `motion_type LIKE
'preset_%'` predicate — multiple custom motions per parent are allowed
(they each have a user-supplied name, deduped by `uq_motions_*_name`).

Revision ID: 20260430_015
Revises: 20260429_014
Create Date: 2026-04-30

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260430_015"
down_revision: str | Sequence[str] | None = "20260429_014"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_index(
        "uq_motions_base_motion_type",
        "motions",
        ["base_id", "motion_type"],
        unique=True,
        postgresql_where=sa.text(
            "base_id IS NOT NULL "
            "AND deleted_at IS NULL "
            "AND motion_type LIKE 'preset_%'"
        ),
    )
    op.create_index(
        "uq_motions_alias_motion_type",
        "motions",
        ["alias_id", "motion_type"],
        unique=True,
        postgresql_where=sa.text(
            "alias_id IS NOT NULL "
            "AND deleted_at IS NULL "
            "AND motion_type LIKE 'preset_%'"
        ),
    )


def downgrade() -> None:
    op.drop_index("uq_motions_alias_motion_type", table_name="motions")
    op.drop_index("uq_motions_base_motion_type", table_name="motions")
