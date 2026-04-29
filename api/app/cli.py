"""Admin CLI.

Usage:
    python -m app.cli create-user --email a@b.com --password ... --name Alice
    python -m app.cli seed-e2e

Phase 1 has no self-serve registration (DECISIONS §6 B4), so user accounts
are bootstrapped via this CLI. Uses argparse to avoid adding a dependency —
the command surface is small enough to not warrant click/typer.
"""

from __future__ import annotations

import argparse
import asyncio
import sys

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.auth.passwords import hash_password
from app.db.session import async_session_factory
from app.models.team import Team
from app.models.user import User

# Fixed identities for E2E. Kept stable so Playwright fixtures can hard-code
# them; password is non-secret because it only ever exists in CI / dev DBs.
# Domain is `example.com` (RFC 2606 reserved-for-documentation) rather than
# `.local` — pydantic's `EmailStr` (via email-validator) rejects `.local` as
# a special-use TLD, which would 422 every login.
E2E_TEAM_NAME = "default"
E2E_PASSWORD = "TestPassword123!"  # noqa: S105 — fixture password, not a secret
E2E_USERS: tuple[tuple[str, str], ...] = (
    ("test+alice@example.com", "Alice"),
    ("test+bob@example.com", "Bob"),
    # Sprint 2 character-creation E2E uses its own identity so checkpoint
    # / character pollution from those specs can't bleed into the auth-only
    # specs that anchor on Alice (T-026).
    ("test+sprint2@example.com", "Sprint2"),
)


async def _create_user(email: str, password: str, name: str, team_name: str) -> User:
    session_factory = async_session_factory()
    async with session_factory() as db:
        team = (await db.execute(select(Team).where(Team.name == team_name))).scalar_one_or_none()
        if team is None:
            raise SystemExit(
                f"team {team_name!r} not found — run alembic migrations first "
                "(default team is created by the teams migration)"
            )

        user = User(
            team_id=team.id,
            name=name,
            email=email,
            password_hash=hash_password(password),
        )
        db.add(user)
        try:
            await db.commit()
        except IntegrityError as exc:
            raise SystemExit(f"email {email!r} already exists") from exc
        await db.refresh(user)
        return user


def _run_create_user(args: argparse.Namespace) -> int:
    user = asyncio.run(
        _create_user(
            email=args.email,
            password=args.password,
            name=args.name,
            team_name=args.team,
        )
    )
    print(f"created user {user.id} ({user.email}) in team {args.team}")
    return 0


async def _seed_e2e() -> list[tuple[str, str]]:
    """Create the E2E test users if they don't exist. Returns (email, action) pairs."""
    session_factory = async_session_factory()
    async with session_factory() as db:
        team = (
            await db.execute(select(Team).where(Team.name == E2E_TEAM_NAME))
        ).scalar_one_or_none()
        if team is None:
            raise SystemExit(
                f"team {E2E_TEAM_NAME!r} not found — run alembic migrations first "
                "(default team is created by the teams migration)"
            )

        results: list[tuple[str, str]] = []
        for email, name in E2E_USERS:
            existing = (
                await db.execute(select(User).where(User.email == email))
            ).scalar_one_or_none()
            if existing is not None:
                results.append((email, "skipped"))
                continue
            db.add(
                User(
                    team_id=team.id,
                    name=name,
                    email=email,
                    password_hash=hash_password(E2E_PASSWORD),
                )
            )
            results.append((email, "created"))
        await db.commit()
        return results


def _run_seed_e2e(_args: argparse.Namespace) -> int:
    results = asyncio.run(_seed_e2e())
    for email, action in results:
        print(f"{action}: {email}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="app.cli", description="Admin CLI")
    subparsers = parser.add_subparsers(dest="command", required=True)

    create_user = subparsers.add_parser("create-user", help="Create a user")
    create_user.add_argument("--email", required=True)
    create_user.add_argument("--password", required=True)
    create_user.add_argument("--name", required=True)
    create_user.add_argument("--team", default="default", help="Team name (default: 'default')")
    create_user.set_defaults(func=_run_create_user)

    seed_e2e = subparsers.add_parser(
        "seed-e2e",
        help="Create the fixed E2E test users in the default team (idempotent)",
    )
    seed_e2e.set_defaults(func=_run_seed_e2e)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    sys.exit(main())
