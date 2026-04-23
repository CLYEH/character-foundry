from __future__ import annotations

import os
from pathlib import Path

import pytest

_API_DIR = Path(__file__).resolve().parent.parent


def _test_database_url() -> str | None:
    """Prefer TEST_DATABASE_URL; fall back to DATABASE_URL for local dev runs.

    If neither is set we return None — individual tests skip themselves so the
    test suite still imports and reports cleanly on a workstation without a
    Postgres instance to hand.
    """
    return os.environ.get("TEST_DATABASE_URL") or os.environ.get("DATABASE_URL")


@pytest.fixture(scope="session")
def database_url() -> str:
    url = _test_database_url()
    if not url:
        pytest.skip(
            "TEST_DATABASE_URL / DATABASE_URL not set; skipping DB-backed tests"
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
