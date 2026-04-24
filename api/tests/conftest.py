from __future__ import annotations

import os
from pathlib import Path

import pytest

_API_DIR = Path(__file__).resolve().parent.parent


def _test_database_url() -> str | None:
    """Return the URL for tests, or None to signal "skip".

    Only `TEST_DATABASE_URL` is consulted — we intentionally do NOT fall back
    to `DATABASE_URL`. The migration tests drop tables via AUTOCOMMIT DDL, and
    `DATABASE_URL` commonly points at the primary dev/app database. Silently
    honoring it would wipe developer data the first time someone ran `pytest`.
    Require the developer to name a throwaway DB explicitly.
    """
    return os.environ.get("TEST_DATABASE_URL")


@pytest.fixture(scope="session")
def database_url() -> str:
    url = _test_database_url()
    if not url:
        pytest.skip(
            "TEST_DATABASE_URL not set; skipping destructive DB-backed tests. "
            "Point it at a throwaway database — these tests DROP tables."
        )
    return url


@pytest.fixture(scope="session")
def alembic_config(database_url: str):
    """Return an Alembic Config anchored at api/alembic.ini with DATABASE_URL set.

    env.py reads DATABASE_URL from the process env, so we inject there rather
    than mutating alembic.ini.
    """
    from alembic.config import Config

    os.environ["DATABASE_URL"] = database_url

    cfg = Config(str(_API_DIR / "alembic.ini"))
    cfg.set_main_option("script_location", str(_API_DIR / "alembic"))
    return cfg
