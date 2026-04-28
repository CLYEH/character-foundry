"""Fixtures for the T-018 fork suite.

Re-exports the shared fixture set from `tests/select_base/conftest.py`
so all three T-018 suites use identical setup. Pytest discovers
@pytest.fixture-decorated functions imported into a conftest by
their module-qualified name, so the star-import below registers
them at this directory's scope.
"""

from __future__ import annotations

# noqa: F401 — pytest needs the names visible at module top level so
# its fixture finder picks them up; star-import preserves that.
from tests.select_base.conftest import (  # noqa: F401
    _migrate_once,
    _reset_session_cache,
    access_token,
    auth_headers,
    clean_tables,
    client,
    db_session,
    default_team_id,
    fake_arq_pool,
    fake_redis,
    fake_redis_server,
    second_access_token,
    second_user,
    seed_committed_checkpoint,
    seeded_user,
    storage_root,
)
