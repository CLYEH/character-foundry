"""Exercise the initial migration chain up/down/up against a real Postgres.

Requires TEST_DATABASE_URL (or DATABASE_URL) to point at a Postgres instance
with the necessary extensions available. Skipped otherwise so contributors
without a local DB still get a green `pytest` run.
"""

from __future__ import annotations

import asyncio
import importlib.util
from datetime import UTC, datetime
from pathlib import Path

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from alembic import command

REQUIRED_EXTENSIONS = {"uuid-ossp", "pgcrypto", "vector", "pg_trgm"}


def _load_migration_010_helper():
    """Re-use migration 010's _month_ranges helper so the test matches
    whatever month the suite runs in. The migration file has a leading digit
    in its name so it isn't importable as a module — load by path.
    """
    api_dir = Path(__file__).resolve().parents[2]
    migration_path = api_dir / "alembic" / "versions" / "20260423_010_generation_logs.py"
    spec = importlib.util.spec_from_file_location("migration_010", migration_path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module._month_ranges


_gen_log_ranges = _load_migration_010_helper()

EXPECTED_TABLES = {
    "teams",
    "users",
    "characters",
    "creation_sessions",
    "checkpoints",
    "reference_images",
    "bases",
    "aliases",
    "masks",
    "motions",
    "generation_logs",
    "tasks",
    "refresh_tokens",
}


def _expected_gen_log_partitions() -> set[str]:
    """Named + default partitions that migration 010 creates at runtime."""
    named = {
        f"generation_logs_{suffix}" for suffix, _, _ in _gen_log_ranges(datetime.now(UTC), count=3)
    }
    return named | {"generation_logs_default"}


# Tables we DROP in _reset to force each test run back to a clean slate. Order
# matters: drop leaves before roots so FK cascades don't block us. CASCADE on
# each drop is belt-and-braces for when a prior failure leaves partial state.
RESET_DROP_TABLES = (
    "refresh_tokens",
    "tasks",
    "generation_logs",
    "motions",
    "aliases",
    "masks",
    "bases",
    "reference_images",
    "checkpoints",
    "creation_sessions",
    "characters",
    "users",
    "teams",
    "alembic_version",
)


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
            for table in RESET_DROP_TABLES:
                await conn.execute(text(f"DROP TABLE IF EXISTS {table} CASCADE"))
            await conn.execute(text("DROP FUNCTION IF EXISTS update_updated_at_column() CASCADE"))
    finally:
        await engine.dispose()


@pytest.fixture
def clean_db(database_url: str):
    _run(_reset(database_url))
    yield
    _run(_reset(database_url))


def _insert_user(database_url: str) -> tuple[str, str]:
    """Insert a team+user fixture and return (team_id, user_id)."""
    team_id = _run(_fetch(database_url, "SELECT id FROM teams WHERE name='default'"))[0][0]
    _run(
        _exec(
            database_url,
            "INSERT INTO users (id, team_id, name, email, password_hash) "
            "VALUES (gen_random_uuid(), :team_id, 'Tester', "
            "'tester@example.com', 'x')",
            {"team_id": team_id},
        )
    )
    user_id = _run(_fetch(database_url, "SELECT id FROM users WHERE email='tester@example.com'"))[
        0
    ][0]
    return team_id, user_id


def test_upgrade_head_creates_schema(alembic_config, database_url, clean_db):
    command.upgrade(alembic_config, "head")

    extensions = {row[0] for row in _run(_fetch(database_url, "SELECT extname FROM pg_extension"))}
    assert REQUIRED_EXTENSIONS.issubset(extensions), (
        f"expected extensions {REQUIRED_EXTENSIONS} but got {extensions}"
    )

    teams = _run(_fetch(database_url, "SELECT name FROM teams"))
    assert ("default",) in teams, f"default team missing; got {teams}"

    # Every expected table exists (either as a regular relation or as a
    # partitioned parent — both show up in pg_tables, but partitioned parents
    # only appear in pg_class, so check the catalog instead).
    actual_tables = {
        row[0]
        for row in _run(
            _fetch(
                database_url,
                """
                SELECT c.relname
                FROM pg_class c
                JOIN pg_namespace n ON n.oid = c.relnamespace
                WHERE n.nspname = 'public'
                  AND c.relkind IN ('r', 'p')
                """,
            )
        )
    }
    assert EXPECTED_TABLES.issubset(actual_tables), (
        f"missing tables: {EXPECTED_TABLES - actual_tables}"
    )

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

    # All user tables (regular + partitioned) should be gone after downgrade.
    names_literal = ", ".join(f"'{t}'" for t in EXPECTED_TABLES)
    remaining = _run(
        _fetch(
            database_url,
            f"""
            SELECT c.relname
            FROM pg_class c
            JOIN pg_namespace n ON n.oid = c.relnamespace
            WHERE n.nspname = 'public'
              AND c.relkind IN ('r', 'p')
              AND c.relname IN ({names_literal})
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

    team_id, user_id = _insert_user(database_url)

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
    assert (
        "chk_characters_name_chars" in str(exc.value)
        or "check constraint" in str(exc.value).lower()
    )


def test_motions_exactly_one_parent_check(alembic_config, database_url, clean_db):
    """A motion must hang off exactly one of base_id / alias_id — neither or both is an error."""
    command.upgrade(alembic_config, "head")

    # Both NULL — must fail.
    with pytest.raises(Exception) as exc_none:
        _run(
            _exec(
                database_url,
                "INSERT INTO motions (base_id, alias_id, motion_type, name, video_key) "
                "VALUES (NULL, NULL, 'preset_wave', 'hi', 'k')",
            )
        )
    assert (
        "chk_motions_exactly_one_parent" in str(exc_none.value)
        or "check constraint" in str(exc_none.value).lower()
    )


def test_month_ranges_emits_utc_qualified_bounds():
    """Regression: partition bounds must carry explicit `+00` so they're
    interpreted as UTC instants regardless of the DB server's session
    TimeZone. Bare-date bounds on a non-UTC DB would shift month
    boundaries and route edge rows to the wrong partition.
    """
    ranges = _gen_log_ranges(datetime(2026, 4, 24, tzinfo=UTC), count=2)
    assert ranges == [
        ("2026_04", "2026-04-01 00:00:00+00", "2026-05-01 00:00:00+00"),
        ("2026_05", "2026-05-01 00:00:00+00", "2026-06-01 00:00:00+00"),
    ]


def test_generation_log_partitions_exist(alembic_config, database_url, clean_db):
    """Bootstrap migration 010 creates the current month + next two named
    partitions (derived from execution time) plus a DEFAULT partition.
    """
    command.upgrade(alembic_config, "head")

    partitions = {
        row[0]
        for row in _run(
            _fetch(
                database_url,
                """
                SELECT c.relname
                FROM pg_inherits i
                JOIN pg_class c ON c.oid = i.inhrelid
                JOIN pg_class parent ON parent.oid = i.inhparent
                WHERE parent.relname = 'generation_logs'
                """,
            )
        )
    }
    expected = _expected_gen_log_partitions()
    assert expected.issubset(partitions), f"missing partitions: {expected - partitions}"


def test_generation_log_default_partition_catches_far_future_row(
    alembic_config, database_url, clean_db
):
    """The DEFAULT partition must absorb rows whose `started_at` falls outside
    the three named monthly partitions — otherwise a fresh environment run
    long after upgrade would fail to insert (the exact bug Codex flagged).
    """
    command.upgrade(alembic_config, "head")
    _, user_id = _insert_user(database_url)

    # Pick a date well past the 3-month bootstrap window.
    _run(
        _exec(
            database_url,
            """
            INSERT INTO generation_logs
                (user_id, entity_type, model_name, final_prompt,
                 cost_units, status, started_at)
            VALUES
                (:user_id, 'checkpoint', 'gpt-image-2', 'x',
                 0, 'success', '2030-01-15T00:00:00Z')
            """,
            {"user_id": user_id},
        )
    )

    # Row must have landed in the default partition.
    count_in_default = _run(_fetch(database_url, "SELECT COUNT(*) FROM generation_logs_default"))[
        0
    ][0]
    assert count_in_default == 1, (
        f"expected far-future row to land in default partition; got count={count_in_default}"
    )


def test_tasks_indexes_and_constraints(alembic_config, database_url, clean_db):
    """tasks must carry its 4 indexes and the terminal/mutex CHECKs must bite."""
    command.upgrade(alembic_config, "head")

    indexes = {
        row[0]
        for row in _run(
            _fetch(
                database_url,
                "SELECT indexname FROM pg_indexes WHERE tablename = 'tasks'",
            )
        )
    }
    required_tasks_indexes = {
        "idx_tasks_user_status_created",
        "idx_tasks_active",
        "idx_tasks_entity",
        "idx_tasks_cancel_pending",
    }
    assert required_tasks_indexes.issubset(indexes), (
        f"missing indexes on tasks: {required_tasks_indexes - indexes}"
    )

    _, user_id = _insert_user(database_url)

    # status='completed' with NULL completed_at must fail the terminal CHECK.
    with pytest.raises(Exception) as exc_terminal:
        _run(
            _exec(
                database_url,
                "INSERT INTO tasks (user_id, task_type, status, input_payload) "
                "VALUES (:user_id, 'create_checkpoint', 'completed', '{}'::jsonb)",
                {"user_id": user_id},
            )
        )
    assert (
        "chk_tasks_terminal_completed_at" in str(exc_terminal.value)
        or "check constraint" in str(exc_terminal.value).lower()
    )

    # result + error both non-null must fail the mutex CHECK.
    with pytest.raises(Exception) as exc_mutex:
        _run(
            _exec(
                database_url,
                "INSERT INTO tasks (user_id, task_type, status, input_payload, "
                "result, error, completed_at) "
                "VALUES (:user_id, 'create_checkpoint', 'failed', '{}'::jsonb, "
                "'{}'::jsonb, '{}'::jsonb, NOW())",
                {"user_id": user_id},
            )
        )
    assert (
        "chk_tasks_result_error_mutex" in str(exc_mutex.value)
        or "check constraint" in str(exc_mutex.value).lower()
    )
