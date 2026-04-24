from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.api.deps import get_storage
from app.main import app
from app.storage.local import LocalFilesystemBackend
from app.storage.signed_url import sign_token

SECRET = "test-storage-secret"


@pytest.fixture(autouse=True)
def storage_secret(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("STORAGE_SIGNED_URL_SECRET", SECRET)


@pytest.fixture
def backend(tmp_path: Path) -> LocalFilesystemBackend:
    return LocalFilesystemBackend(tmp_path)


@pytest.fixture
def client(backend: LocalFilesystemBackend) -> Iterator[TestClient]:
    app.dependency_overrides[get_storage] = lambda: backend
    try:
        with TestClient(app) as c:
            yield c
    finally:
        app.dependency_overrides.pop(get_storage, None)


def test_serve_file_with_valid_token(client: TestClient, backend: LocalFilesystemBackend) -> None:
    backend.put("characters/abc/base.png", b"PNGBYTES", "image/png")
    token = sign_token(key="characters/abc/base.png", user_id="u1", expires_in_seconds=60)

    resp = client.get(f"/storage/characters/abc/base.png?token={token}")
    assert resp.status_code == 200
    assert resp.content == b"PNGBYTES"
    assert resp.headers["content-type"].startswith("image/png")


def test_serve_file_expired_token_returns_storage_url_expired(
    client: TestClient, backend: LocalFilesystemBackend
) -> None:
    backend.put("k.png", b"x", "image/png")
    token = sign_token(key="k.png", user_id=None, expires_in_seconds=-10)

    resp = client.get(f"/storage/k.png?token={token}")
    assert resp.status_code == 403
    body = resp.json()
    assert body["error"]["code"] == "STORAGE_URL_EXPIRED"
    assert body["error"]["retryable"] is True


def test_serve_file_tampered_token_returns_auth_invalid_token(
    client: TestClient, backend: LocalFilesystemBackend
) -> None:
    backend.put("k.png", b"x", "image/png")
    token = sign_token(key="k.png", user_id=None, expires_in_seconds=60)
    tampered = token[:-2] + ("AA" if not token.endswith("AA") else "BB")

    resp = client.get(f"/storage/k.png?token={tampered}")
    assert resp.status_code == 403
    body = resp.json()
    assert body["error"]["code"] == "AUTH_INVALID_TOKEN"
    assert body["error"]["retryable"] is False


def test_serve_file_token_for_other_key_rejected(
    client: TestClient, backend: LocalFilesystemBackend
) -> None:
    backend.put("a.png", b"a", "image/png")
    backend.put("b.png", b"b", "image/png")
    # Sign for a.png, request b.png — must be rejected as invalid.
    token = sign_token(key="a.png", user_id=None, expires_in_seconds=60)

    resp = client.get(f"/storage/b.png?token={token}")
    assert resp.status_code == 403
    assert resp.json()["error"]["code"] == "AUTH_INVALID_TOKEN"


def test_serve_file_missing_token_returns_422(client: TestClient) -> None:
    resp = client.get("/storage/anything.png")
    assert resp.status_code == 422  # FastAPI Query validation


def test_serve_file_missing_key_returns_404(
    client: TestClient,
) -> None:
    token = sign_token(key="ghost.png", user_id=None, expires_in_seconds=60)
    resp = client.get(f"/storage/ghost.png?token={token}")
    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "STORAGE_NOT_FOUND"


def test_serve_file_directory_key_returns_404_not_500(
    client: TestClient, backend: LocalFilesystemBackend
) -> None:
    # Nested put creates characters/abc/ as a directory. A signed URL for
    # that directory key must surface as STORAGE_NOT_FOUND, not an unstructured
    # 500 from a leaked IsADirectoryError.
    backend.put("characters/abc/base.png", b"x", "image/png")
    token = sign_token(key="characters/abc", user_id=None, expires_in_seconds=60)
    resp = client.get(f"/storage/characters/abc?token={token}")
    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "STORAGE_NOT_FOUND"


def test_get_signed_url_round_trip_through_route(
    client: TestClient, backend: LocalFilesystemBackend
) -> None:
    backend.put("characters/abc/base.png", b"BYTES", "image/png")
    url = backend.get_signed_url("characters/abc/base.png", expires_in_seconds=60)
    assert url.startswith("/storage/characters/abc/base.png?token=")

    resp = client.get(url)
    assert resp.status_code == 200
    assert resp.content == b"BYTES"
