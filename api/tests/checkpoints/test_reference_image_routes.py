"""Route-level tests for `POST /v1/creation-sessions/{id}/reference-images`."""

from __future__ import annotations

import io
import uuid
from typing import Any

from fastapi.testclient import TestClient
from PIL import Image

from tests.checkpoints.conftest import auth_headers


def _png_bytes(*, size: int = 32) -> bytes:
    """Create a small valid PNG."""
    im = Image.new("RGBA", (size, size), (255, 0, 0, 255))
    buf = io.BytesIO()
    im.save(buf, format="PNG")
    return buf.getvalue()


def _create_character(client: TestClient, token: str) -> dict[str, Any]:
    resp = client.post(
        "/v1/characters",
        json={"name": "RefSession", "input_mode": "reference"},
        headers=auth_headers(token),
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


def test_upload_reference_image_happy_path(client: TestClient, access_token: str) -> None:
    body = _create_character(client, access_token)
    session_id = body["creation_session"]["id"]

    files = {"file": ("ref.png", _png_bytes(), "image/png")}
    resp = client.post(
        f"/v1/creation-sessions/{session_id}/reference-images",
        files=files,
        headers=auth_headers(access_token),
    )
    assert resp.status_code == 201, resp.text
    payload = resp.json()
    assert "reference_image_id" in payload
    assert payload["url"].startswith("/storage/")
    # The id should be a parseable UUID.
    uuid.UUID(payload["reference_image_id"])


def test_upload_reference_image_rejects_bad_mime(client: TestClient, access_token: str) -> None:
    body = _create_character(client, access_token)
    session_id = body["creation_session"]["id"]

    files = {"file": ("ref.txt", b"not an image", "text/plain")}
    resp = client.post(
        f"/v1/creation-sessions/{session_id}/reference-images",
        files=files,
        headers=auth_headers(access_token),
    )
    assert resp.status_code == 400
    assert resp.json()["error"]["code"] == "VALIDATION_REFERENCE_IMAGE_TYPE"


def test_upload_reference_image_rejects_oversized(client: TestClient, access_token: str) -> None:
    body = _create_character(client, access_token)
    session_id = body["creation_session"]["id"]

    # 11 MB of zeroes — payload doesn't have to be a real image because
    # the size check fires before the storage write / DB insert.
    oversized = b"\x00" * (11 * 1024 * 1024)
    files = {"file": ("big.png", oversized, "image/png")}
    resp = client.post(
        f"/v1/creation-sessions/{session_id}/reference-images",
        files=files,
        headers=auth_headers(access_token),
    )
    assert resp.status_code == 400
    assert resp.json()["error"]["code"] == "VALIDATION_REFERENCE_IMAGE_TOO_LARGE"


def test_upload_reference_image_rejects_non_initiator(
    client: TestClient,
    access_token: str,
    second_user: dict[str, Any],
    second_access_token: str,
) -> None:
    """Bob is on the same team but didn't start the session — write
    access is initiator-only per storage-layout.md §5.2."""
    body = _create_character(client, access_token)
    session_id = body["creation_session"]["id"]

    files = {"file": ("ref.png", _png_bytes(), "image/png")}
    resp = client.post(
        f"/v1/creation-sessions/{session_id}/reference-images",
        files=files,
        headers=auth_headers(second_access_token),
    )
    assert resp.status_code == 403
    assert resp.json()["error"]["code"] == "AUTH_INSUFFICIENT_PERMISSION"
