from __future__ import annotations

import asyncio
import os
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import jwt as pyjwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from alembic import command

JWT_SECRET = "test-jwt-secret-dont-use-in-prod"


# Tables we clear between tests. Order matters: child → parent so that even if
# a test briefly populated something deeper than users (not expected, but
# cheap), we leave a clean slate for the next test. `teams` is untouched —
# migration 002 seeds the "default" team and callers rely on it.
_TABLES_TO_CLEAN = (
    "refresh_tokens",
    "tasks",
    # generation_logs has FK to users(RESTRICT) — clean before users so
    # rows left by adjacent suites (T-017 worker) don't block the
    # user delete.
    "generation_logs",
    "motions",
    "aliases",
    "bases",
    "reference_images",
    "checkpoints",
    "creation_sessions",
    "characters",
    "users",
)


async def _delete_all(database_url: str) -> None:
    engine = create_async_engine(database_url, future=True, isolation_level="AUTOCOMMIT")
    try:
        async with engine.connect() as conn:
            for table in _TABLES_TO_CLEAN:
                await conn.execute(text(f"DELETE FROM {table}"))
    finally:
        await engine.dispose()


@pytest.fixture(scope="module", autouse=True)
def _migrate_once(alembic_config, database_url: str) -> Iterator[None]:
    """Upgrade to head once per auth-test module.

    Uses `alembic_config` purely for its side-effect of setting
    `DATABASE_URL` so the app's engine picks it up.
    """
    os.environ["JWT_SECRET"] = JWT_SECRET
    command.upgrade(alembic_config, "head")
    yield


@pytest.fixture
def clean_auth_tables(database_url: str) -> None:
    asyncio.run(_delete_all(database_url))


class _FakeRedis:
    """Minimal async Redis stand-in for auth tests.

    Auth tests don't touch Redis directly, but `/health` does (via the
    middleware tests). Overriding `get_redis` keeps the suite hermetic — no
    dependence on a live Redis in CI for the non-Redis tests.
    """

    async def ping(self) -> bool:
        return True


@pytest.fixture
def client(clean_auth_tables: None, tmp_path: Path) -> Iterator[TestClient]:
    # Import lazily so env vars (JWT_SECRET, DATABASE_URL) are set first.
    from app.api.deps import get_storage
    from app.core.redis_client import get_redis
    from app.db.session import async_session_factory, get_engine
    from app.main import app
    from app.storage.local import LocalFilesystemBackend

    # Engine/factory are lru_cached; clear them so this test process picks up
    # whatever DATABASE_URL the current run is using, even if a prior test run
    # in the same process warmed the cache against a different URL.
    get_engine.cache_clear()
    async_session_factory.cache_clear()

    # Override storage + redis so `/health` (now a dependency of the request-id
    # middleware tests) works regardless of the CI runner's filesystem / Redis
    # availability. The default `get_storage` targets `/storage`, which is
    # unwritable on a Linux CI runner.
    storage_backend = LocalFilesystemBackend(tmp_path / "storage")
    fake_redis = _FakeRedis()

    async def _redis_override() -> _FakeRedis:
        return fake_redis

    app.dependency_overrides[get_storage] = lambda: storage_backend
    app.dependency_overrides[get_redis] = _redis_override

    try:
        with TestClient(app) as c:
            yield c
    finally:
        app.dependency_overrides.pop(get_storage, None)
        app.dependency_overrides.pop(get_redis, None)


async def _insert_user(database_url: str, *, email: str, name: str, password_hash: str) -> None:
    engine = create_async_engine(database_url, future=True, isolation_level="AUTOCOMMIT")
    try:
        async with engine.connect() as conn:
            team_id = (
                await conn.execute(text("SELECT id FROM teams WHERE name='default'"))
            ).scalar_one()
            await conn.execute(
                text(
                    "INSERT INTO users (team_id, name, email, password_hash) "
                    "VALUES (:t, :n, :e, :h)"
                ),
                {"t": team_id, "n": name, "e": email, "h": password_hash},
            )
    finally:
        await engine.dispose()


@pytest.fixture
def seeded_user(database_url: str, clean_auth_tables: None) -> dict[str, str]:
    """Insert one user into the default team and return its credentials."""
    from app.auth.passwords import hash_password

    email = "alice@example.com"
    password = "correct-horse-battery-staple"
    name = "Alice"
    asyncio.run(
        _insert_user(database_url, email=email, name=name, password_hash=hash_password(password))
    )
    return {"email": email, "password": password, "name": name}


@pytest.fixture
def second_user(database_url: str, seeded_user: dict[str, str]) -> dict[str, str]:
    """Insert a second user so tests can exercise cross-account isolation."""
    from app.auth.passwords import hash_password

    email = "bob@example.com"
    password = "also-not-guessable"
    name = "Bob"
    asyncio.run(
        _insert_user(database_url, email=email, name=name, password_hash=hash_password(password))
    )
    return {"email": email, "password": password, "name": name}


# ---------------------------------------------------------------------------
# Shared OAuth fixtures (T-054 + T-071). Moved here from test_dual_stack.py
# so additional OAuth-path test files (auto-provisioning, future MCP tests)
# can reuse the same synthetic Authentik JWT factory without duplicating
# the RSA keypair, JWKS doc, or env-var wiring.
# ---------------------------------------------------------------------------

OAUTH_TEST_ISSUER = "https://auth.test.example/application/o/character-foundry-test/"
OAUTH_TEST_AUDIENCE = "character-foundry-test"
OAUTH_TEST_JWKS_URI = "https://auth.test.example/application/o/character-foundry-test/jwks/"
OAUTH_TEST_KID = "cf-test-rsa-key-1"


@pytest.fixture(scope="module")
def _rsa_keypair() -> tuple[rsa.RSAPrivateKey, rsa.RSAPublicKey]:
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    return private_key, private_key.public_key()


@pytest.fixture(scope="module")
def _private_key_pem(_rsa_keypair: tuple[rsa.RSAPrivateKey, rsa.RSAPublicKey]) -> bytes:
    priv, _pub = _rsa_keypair
    return priv.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )


@pytest.fixture(scope="module")
def _jwks_document(_rsa_keypair: tuple[rsa.RSAPrivateKey, rsa.RSAPublicKey]) -> dict[str, Any]:
    """A minimal JWKS doc describing our single test signing key."""
    _priv, pub = _rsa_keypair
    numbers = pub.public_numbers()

    def _b64url_uint(n: int) -> str:
        import base64

        byte_len = (n.bit_length() + 7) // 8
        return base64.urlsafe_b64encode(n.to_bytes(byte_len, "big")).rstrip(b"=").decode("ascii")

    return {
        "keys": [
            {
                "kty": "RSA",
                "use": "sig",
                "alg": "RS256",
                "kid": OAUTH_TEST_KID,
                "n": _b64url_uint(numbers.n),
                "e": _b64url_uint(numbers.e),
            }
        ]
    }


@pytest.fixture
def _oauth_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AUTHENTIK_ISSUER_URL", OAUTH_TEST_ISSUER)
    monkeypatch.setenv("AUTHENTIK_AUDIENCE", OAUTH_TEST_AUDIENCE)
    monkeypatch.setenv("AUTHENTIK_JWKS_URL", OAUTH_TEST_JWKS_URI)


@pytest.fixture
def _preload_jwks_cache(
    _oauth_env: None,
    _jwks_document: dict[str, Any],
) -> Iterator[Any]:
    """Install a JWKSCache pre-populated from `_jwks_document` so tests that
    don't care about HTTP fetch behaviour avoid wiring respx for every case.
    """
    from app.auth.oauth import (
        JWKSCache,
        reset_jwks_cache_for_test,
        set_jwks_cache_for_test,
    )

    cache = JWKSCache(OAUTH_TEST_JWKS_URI)
    keys = {key_data["kid"]: pyjwt.PyJWK(key_data) for key_data in _jwks_document["keys"]}
    cache.seed_keys_for_test(keys)
    set_jwks_cache_for_test(cache)
    try:
        yield cache
    finally:
        reset_jwks_cache_for_test()


@pytest.fixture
def make_oauth_token(_private_key_pem: bytes) -> Any:
    def _make(
        *,
        scopes: list[str] | None = None,
        client_id: str = "claude-code",
        email: str | None = "alice@example.com",
        sub: str | None = None,
        issuer: str = OAUTH_TEST_ISSUER,
        audience: str = OAUTH_TEST_AUDIENCE,
        expires_in: int = 3600,
        kid: str = OAUTH_TEST_KID,
        extra: dict[str, Any] | None = None,
    ) -> str:
        now = int(time.time())
        payload: dict[str, Any] = {
            "iss": issuer,
            "aud": audience,
            "iat": now,
            "exp": now + expires_in,
            "azp": client_id,
            "sub": sub if sub is not None else (email or client_id),
            "scope": " ".join(scopes) if scopes else "",
        }
        if email is not None:
            payload["email"] = email
        if extra:
            payload.update(extra)
        return pyjwt.encode(payload, _private_key_pem, algorithm="RS256", headers={"kid": kid})

    return _make
