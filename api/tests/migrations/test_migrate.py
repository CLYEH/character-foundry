"""Exercise the initial migration chain up/down/up against a real Postgres.

Requires TEST_DATABASE_URL (or DATABASE_URL) to point at a Postgres instance
with the necessary extensions available. Skipped otherwise so contributors
without a local DB still get a green `pytest` run.
"""
from __future__ import annotations

import asyncio

import pytest
from alembic import command
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine


REQUIRED_EXTENSIONS = {"uuid-ossp", "pgcrypto", "vector", "pg_trgm"}


def _run(coro):
    return asyncio.run(coro)


async def _fetch(database_url: str, sql: str) -> list[tuple]:
    engine = create_async_engine(database_url, future=True)
    try:
        async with engine.connect() as conn:
            result = await conn.execute(text(sql))
            return list(result.fetchall())
    finally:
        await engine.dispose()


async def _exec(database_url: str, sql: str, params: dict | None = None) -> None:
    engine = create_async_engine(database_url, future=True, isolation_level="AUTOCOMMIT")
    try:
        async with engine.connect() as conn:
            await conn.execute(text(sql), params or {})
    finally:
        await engine.dispose()


async def _reset(database_url: str) -> None:
    """Drop alembic_version + any tables left behind, so each test run is clean."""
    engine = create_async_engine(database_url, future=True, isolation_level="AUTOCOMMIT")
    try:
        async with engine.connect() as conn:
            await conn.execute(text("DROP TABLE IF EXISTS characters CASCADE"))
            await conn.execute(text("DROP TABLE IF EXISTS users CASCADE"))
            await conn.execute(text("DROP TABLE IF EXISTS teams CASCADE"))
            await conn.execute(text("DROP TABLE IF EXISTS alembic_version CASCADE"))
            await conn.execute(
                text("DROP FUNCTION IF EXISTS update_updated_at_column() CASCADE")
            )
    finally:
        await engine.dispose()


@pytest.fixture
def clean_db(database_url: str):
    _run(_reset(database_url))
    yield
    _run(_reset(database_url))


def test_upgrade_head_creates_schema(alembic_config, database_url, clean_db):
    command.upgrade(alembic_config, "head")

    extensions = {row[0] for row in _run(_fetch(database_url, "SELECT extname FROM pg_extension"))}
    assert REQUIRED_EXTENSIONS.issubset(extensions), (
        f"expected extensions {REQUIRED_EXTENSIONS} but got {extensions}"
    )

    teams = _run(_fetch(database_url, "SELECT name FROM teams"))
    assert ("default",) in teams, f"default team missing; got {teams}"

    # Smoke check schema exists — any column error would blow up here.
    _run(_fetch(database_url, "SELECT id, email, team_id FROM users WHERE 1=0"))
    _run(
        _fetch(
            database_url,
            "SELECT id, team_id, owner_id, name, slug, base_id, creation_session_id, "
            "copied_from_character_id, created_at, updated_at, deleted_at "
            "FROM characters WHERE 1=0",
        )
    )


def test_downgrade_to_base_is_clean(alembic_config, database_url, clean_db):
    command.upgrade(alembic_config, "head")
    command.downgrade(alembic_config, "base")

    # All user tables should be gone; alembic_version is kept but empty.
    remaining = _run(
        _fetch(
            database_url,
            """
            SELECT tablename FROM pg_tables
            WHERE schemaname = 'public'
              AND tablename IN ('teams', 'users', 'characters')
            """,
        )
    )
    assert remaining == [], f"downgrade left tables behind: {remaining}"


def test_up_down_up_cycle(alembic_config, database_url, clean_db):
    command.upgrade(alembic_config, "head")
    command.downgrade(alembic_config, "base")
    command.upgrade(alembic_config, "head")

    teams = _run(_fetch(database_url, "SELECT name FROM teams"))
    assert ("default",) in teams


def test_characters_name_check_constraint(alembic_config, database_url, clean_db):
    """Guard against regressing to \\p{Han} (PostgreSQL ARE doesn't support it).

    The CHECK constraint text is accepted at CREATE time regardless of whether
    the regex is valid, so the only way to catch a bad pattern is to exercise
    it with a real INSERT. We insert a Chinese-named row (must succeed) and a
    row with an illegal character (must fail).
    """
    command.upgrade(alembic_config, "head")

    # Insert fixtures: a team + user so characters has valid FKs.
    team_id = _run(_fetch(database_url, "SELECT id FROM teams WHERE name='default'"))[0][0]
    _run(
        _exec(
            database_url,
            "INSERT INTO users (id, team_id, name, email, password_hash) "
            "VALUES (gen_random_uuid(), :team_id, 'Tester', 'tester@example.com', 'x')",
            {"team_id": team_id},
        )
    )
    user_id = _run(_fetch(database_url, "SELECT id FROM users WHERE email='tester@example.com'"))[0][0]

    # Chinese name must be accepted.
    _run(
        _exec(
            database_url,
            "INSERT INTO characters (team_id, owner_id, name, slug) "
            "VALUES (:team_id, :owner_id, '小雅', 'xiao-ya')",
            {"team_id": team_id, "owner_id": user_id},
        )
    )

    # Illegal character must be rejected by the CHECK constraint.
    with pytest.raises(Exception) as exc:
        _run(
            _exec(
                database_url,
                "INSERT INTO characters (team_id, owner_id, name, slug) "
                "VALUES (:team_id, :owner_id, 'bad name!', 'bad-name')",
                {"team_id": team_id, "owner_id": user_id},
            )
        )
    # Either CheckViolation or a transport-wrapped version of it — both are fine.
    assert "chk_characters_name_chars" in str(exc.value) or "check constraint" in str(exc.value).lower()
