"""refresh_tokens.token_source

Adds an enum column distinguishing refresh tokens minted by the legacy JWT
login path (`'jwt'`) from those minted by the OAuth Authorization Code +
PKCE flow (`'oauth'`). Per T-055 / auth Q6 we keep one table rather than
splitting into `oauth_refresh_token` — the only thing that diverges between
sources is the refresh endpoint that consumes the token, which T-3.5b will
gate on this column.

Backfill: every existing row predates the dual-stack work, so they are
all `'jwt'`. The column is added nullable, backfilled in the same
migration, then locked to NOT NULL.

Revision ID: 20260513_016
Revises: 20260430_015
Create Date: 2026-05-13

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260513_016"
down_revision: str | Sequence[str] | None = "20260430_015"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


_ENUM_NAME = "refresh_token_source"
_ENUM_VALUES = ("jwt", "oauth")


def upgrade() -> None:
    # Create the postgres native enum first. `sa.Enum` with explicit
    # `create_type=False` on the column would otherwise try to create the
    # type inline during add_column, which sequences ambiguously next to
    # the backfill UPDATE below.
    token_source = sa.Enum(*_ENUM_VALUES, name=_ENUM_NAME)
    token_source.create(op.get_bind(), checkfirst=True)

    op.add_column(
        "refresh_tokens",
        sa.Column(
            "token_source",
            sa.Enum(*_ENUM_VALUES, name=_ENUM_NAME, create_type=False),
            nullable=True,
        ),
    )

    # Phase 1 ship-before state: every row was minted by the JWT path.
    op.execute("UPDATE refresh_tokens SET token_source = 'jwt' WHERE token_source IS NULL")

    op.alter_column("refresh_tokens", "token_source", nullable=False)


def downgrade() -> None:
    op.drop_column("refresh_tokens", "token_source")
    sa.Enum(*_ENUM_VALUES, name=_ENUM_NAME).drop(op.get_bind(), checkfirst=True)
