"""Alembic environment — async mode against asyncpg.

Reads DATABASE_URL from the process environment rather than alembic.ini so the
same migrations run in docker compose, CI, and tests without config edits.
"""

from __future__ import annotations

import asyncio
import os
from logging.config import fileConfig

from alembic import context
from sqlalchemy import pool
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import async_engine_from_config

# Import models so their metadata is registered on Base.metadata.
from app.db.base import Base
from app.models import (  # noqa: F401  (register metadata)
    Alias,
    BaseAsset,
    Character,
    Checkpoint,
    CreationSession,
    GenerationLog,
    Motion,
    Task,
    Team,
    User,
)

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def _resolve_url() -> str:
    url = os.environ.get("DATABASE_URL")
    if not url:
        raise RuntimeError(
            "DATABASE_URL is not set. Set it in your .env or environment before "
            "running alembic."
        )
    return url


def _autogenerate_filter(object, name, type_, reflected, compare_to):
    """Autogenerate filter for objects that can't (or shouldn't) live in ORM
    metadata.

    Skipped:
      - IVFFlat indexes created via raw SQL. SQLAlchemy's Index() can't
        describe pgvector's access method with `vector_cosine_ops`, so these
        indexes live only in migrations (created via op.execute). Without
        this filter, autogenerate would emit drop_index diffs for them.
      - `generation_logs` partition children and their per-partition indexes.
        The parent table is modeled; individual monthly partitions are a
        runtime concern (created by migration 010 and the scheduled rotation
        job) and don't belong in ORM metadata.
    """
    if type_ == "index" and name and name.endswith("_embedding"):
        return False
    if type_ == "table" and name and name.startswith("generation_logs_"):
        return False
    if (
        type_ == "index"
        and name
        and name.startswith("idx_gen_logs_")
    ):
        return False
    return True


def run_migrations_offline() -> None:
    """Emit SQL without a live DB connection (for `alembic upgrade --sql`)."""
    context.configure(
        url=_resolve_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
        include_object=_autogenerate_filter,
    )

    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection: Connection) -> None:
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        compare_type=True,
        include_object=_autogenerate_filter,
    )

    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    configuration = config.get_section(config.config_ini_section) or {}
    configuration["sqlalchemy.url"] = _resolve_url()

    connectable = async_engine_from_config(
        configuration,
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)

    await connectable.dispose()


def run_migrations_online() -> None:
    asyncio.run(run_async_migrations())


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
