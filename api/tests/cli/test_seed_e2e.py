"""Tests for `python -m app.cli seed-e2e` (T-012)."""

from __future__ import annotations

import asyncio
import os
from collections.abc import Iterator

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from alembic import command


@pytest.fixture(scope="module", autouse=True)
def _migrate_once(alembic_config, database_url: str) -> Iterator[None]:
    command.upgrade(alembic_config, "head")
    yield


async def _purge_users(database_url: str) -> None:
    """Clear users plus everything that references them, in FK order.

    Adjacent test suites (T-017 worker, character routes, etc.) leave
    rows in `characters` / `generation_logs` / `reference_images` that
    have RESTRICT FKs onto `users`. A bare `DELETE FROM users` would
    fail with a ForeignKeyViolationError; clean the descendants
    first so the seed-e2e fixture stays robust to test ordering.
    """
    engine = create_async_engine(database_url, future=True, isolation_level="AUTOCOMMIT")
    try:
        async with engine.connect() as conn:
            for table in (
                "refresh_tokens",
                "tasks",
                "generation_logs",
                "motions",
                "aliases",
                "bases",
                "reference_images",
                "checkpoints",
                "creation_sessions",
                "characters",
                "users",
            ):
                await conn.execute(text(f"DELETE FROM {table}"))
    finally:
        await engine.dispose()


async def _user_count(database_url: str) -> int:
    engine = create_async_engine(database_url, future=True, isolation_level="AUTOCOMMIT")
    try:
        async with engine.connect() as conn:
            return (
                await conn.execute(
                    text("SELECT COUNT(*) FROM users WHERE email LIKE 'test+%@example.com'")
                )
            ).scalar_one()
    finally:
        await engine.dispose()


@pytest.fixture
def clean_users(database_url: str) -> None:
    asyncio.run(_purge_users(database_url))


def _run_cli(args: list[str]) -> int:
    """Invoke the CLI with a fresh engine + session factory.

    `app.db.session.get_engine` / `async_session_factory` are lru_cached, and
    each `main()` call goes through its own `asyncio.run()` which closes the
    loop on exit. If we left the cache warm between calls the engine's
    connection pool would still be bound to the closed loop, producing
    "attached to a different loop" errors on the next call. Clearing before
    every invocation guarantees the engine is built inside the live loop.
    """
    from app.cli import main
    from app.db.session import async_session_factory, get_engine

    get_engine.cache_clear()
    async_session_factory.cache_clear()
    return main(args)


def test_seed_e2e_creates_users_and_is_idempotent(clean_users: None, database_url: str) -> None:
    os.environ["DATABASE_URL"] = database_url

    from app.cli import E2E_USERS

    assert _run_cli(["seed-e2e"]) == 0
    assert asyncio.run(_user_count(database_url)) == len(E2E_USERS)

    # Re-running must not error and must not duplicate.
    assert _run_cli(["seed-e2e"]) == 0
    assert asyncio.run(_user_count(database_url)) == len(E2E_USERS)


def test_seed_e2e_emails_pass_pydantic_validation() -> None:
    """Regression for the `.local` 422 — every seeded email must pass `LoginRequest`.

    pydantic's `EmailStr` (email-validator backend) rejects special-use TLDs
    like `.local` / `.localhost`. If someone reintroduces such a domain in
    `E2E_USERS`, the seed will succeed but E2E login will 422 across the
    board. This test fails fast with a clear message instead.
    """
    from app.auth.schemas import LoginRequest
    from app.cli import E2E_PASSWORD, E2E_USERS

    for email, _ in E2E_USERS:
        LoginRequest(email=email, password=E2E_PASSWORD)


def test_seed_e2e_users_can_login(clean_users: None, database_url: str) -> None:
    os.environ["DATABASE_URL"] = database_url

    from app.auth.passwords import verify_password
    from app.cli import E2E_PASSWORD, E2E_USERS

    assert _run_cli(["seed-e2e"]) == 0

    async def _password_hashes() -> list[str]:
        engine = create_async_engine(database_url, future=True, isolation_level="AUTOCOMMIT")
        try:
            async with engine.connect() as conn:
                rows = (
                    await conn.execute(
                        text(
                            "SELECT password_hash FROM users "
                            "WHERE email LIKE 'test+%@example.com' "
                            "ORDER BY email"
                        )
                    )
                ).all()
                return [row[0] for row in rows]
        finally:
            await engine.dispose()

    hashes = asyncio.run(_password_hashes())
    assert len(hashes) == len(E2E_USERS)
    for h in hashes:
        assert verify_password(E2E_PASSWORD, h)
