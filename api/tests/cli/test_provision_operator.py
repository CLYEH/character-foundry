"""Tests for `python -m app.cli provision-operator` (T-069)."""

from __future__ import annotations

import asyncio
import os
from collections.abc import Iterator
from unittest.mock import patch

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

    Mirrors `test_seed_e2e._purge_users` — adjacent suites leave RESTRICT-FK
    rows in `characters` / `generation_logs` / etc. that block a bare
    `DELETE FROM users`. `teams` is left intact (the `default` team comes
    from the teams migration and `provision-operator` looks it up).
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


@pytest.fixture
def clean_users(database_url: str) -> None:
    asyncio.run(_purge_users(database_url))


def _run_cli(args: list[str]) -> int:
    """Invoke the CLI with a fresh engine + session factory per call.

    Same rationale as `test_seed_e2e._run_cli`: `get_engine` /
    `async_session_factory` are lru_cached, and each `main()` runs its own
    `asyncio.run()` which closes the loop on exit. Clearing the caches before
    every invocation rebuilds the engine inside the live loop.
    """
    from app.cli import main
    from app.db.session import async_session_factory, get_engine

    get_engine.cache_clear()
    async_session_factory.cache_clear()
    return main(args)


async def _fetch_operator(database_url: str, email: str) -> tuple[str, str, str] | None:
    """Return `(name, team_name, password_hash)` for the operator, or None."""
    engine = create_async_engine(database_url, future=True, isolation_level="AUTOCOMMIT")
    try:
        async with engine.connect() as conn:
            row = (
                await conn.execute(
                    text(
                        "SELECT u.name, t.name, u.password_hash "
                        "FROM users u JOIN teams t ON t.id = u.team_id "
                        "WHERE u.email = :email"
                    ),
                    {"email": email},
                )
            ).first()
            return None if row is None else (row[0], row[1], row[2])
    finally:
        await engine.dispose()


def test_provision_operator_creates_oauth_only_user(clean_users: None, database_url: str) -> None:
    """A provisioned operator gets a backend `User` row keyed by email so
    `deps.py::_resolve_oauth` can resolve their Authentik token — and the row
    carries NO usable password, so the legacy JWT-login path is structurally
    dead for them (they are OAuth-only). Without this row every login path
    401s at `/api/v1/auth/me`."""
    os.environ["DATABASE_URL"] = database_url

    assert (
        _run_cli(["provision-operator", "--email", "operator@example.com", "--name", "Operator"])
        == 0
    )

    row = asyncio.run(_fetch_operator(database_url, "operator@example.com"))
    assert row is not None
    name, team_name, password_hash = row
    assert name == "Operator"
    assert team_name == "default"
    # NOT NULL is satisfied with a real Argon2 hash, but no guessable string
    # verifies against it — the plaintext was a discarded CSPRNG token. This
    # is the property the command exists to guarantee: the JWT-login path is
    # unusable for this operator. A regression to a hardcoded sentinel
    # password would be caught here.
    from app.auth.passwords import verify_password

    for guess in ("", "operator@example.com", "Operator", "operator", "oauth-only", "password"):
        assert not verify_password(guess, password_hash)


def test_provision_operator_password_is_random_csprng_token(
    clean_users: None, database_url: str
) -> None:
    """The operator's password is a fresh CSPRNG token, never a constant.

    Hash comparison alone can't prove this — Argon2 salts per call, so even a
    hardcoded sentinel password would produce distinct hashes across rows.
    Spy on the token source instead: if someone swaps `secrets.token_urlsafe`
    for a literal, it stops being called and this fails."""
    os.environ["DATABASE_URL"] = database_url

    import app.cli

    with patch.object(app.cli.secrets, "token_urlsafe", wraps=app.cli.secrets.token_urlsafe) as spy:
        assert _run_cli(["provision-operator", "--email", "csprng@example.com", "--name", "C"]) == 0

    spy.assert_called_once_with(32)


def test_provision_operator_duplicate_email_fails(clean_users: None, database_url: str) -> None:
    """Re-provisioning an existing email fails loud (mirrors `create-user`) —
    no silent overwrite of an operator's existing row."""
    os.environ["DATABASE_URL"] = database_url

    assert _run_cli(["provision-operator", "--email", "dup@example.com", "--name", "Dup"]) == 0
    with pytest.raises(SystemExit, match="already exists"):
        _run_cli(["provision-operator", "--email", "dup@example.com", "--name", "Dup"])


def test_provision_operator_prints_group_membership_reminder(
    clean_users: None, database_url: str, capsys: pytest.CaptureFixture[str]
) -> None:
    """T-077: backend row alone is insufficient — the SPA application
    PolicyBinding gates on `cf-agent-default`. The CLI cannot add the
    Authentik group itself (see authentik-stack.md §5.7.3.c), so its
    contract is to remind the operator. A regression that drops this
    reminder silently re-creates wall 5: operators get through CLI + Google
    login but die at the authorize endpoint with no actionable signal."""
    os.environ["DATABASE_URL"] = database_url

    assert _run_cli(["provision-operator", "--email", "t77@example.com", "--name", "T77"]) == 0

    out = capsys.readouterr().out
    assert "cf-agent-default" in out
    assert "§5.7.3" in out
