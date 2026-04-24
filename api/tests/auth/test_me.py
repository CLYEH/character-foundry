from __future__ import annotations

import os

import jwt
import pytest
from fastapi.testclient import TestClient


def _login(client: TestClient, seeded: dict[str, str]) -> dict[str, str]:
    r = client.post(
        "/v1/auth/login", json={"email": seeded["email"], "password": seeded["password"]}
    )
    assert r.status_code == 200
    return r.json()


def test_me_returns_user_with_valid_token(client: TestClient, seeded_user: dict[str, str]) -> None:
    tokens = _login(client, seeded_user)
    resp = client.get("/v1/auth/me", headers={"Authorization": f"Bearer {tokens['access_token']}"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["user"]["email"] == seeded_user["email"]
    assert body["user"]["name"] == seeded_user["name"]


def test_me_missing_header_returns_missing_token(
    client: TestClient, seeded_user: dict[str, str]
) -> None:
    resp = client.get("/v1/auth/me")
    assert resp.status_code == 401
    assert resp.json()["error"]["code"] == "AUTH_MISSING_TOKEN"


def test_me_malformed_auth_header_returns_invalid_token(
    client: TestClient, seeded_user: dict[str, str]
) -> None:
    # Missing the scheme half.
    resp = client.get("/v1/auth/me", headers={"Authorization": "justatoken"})
    assert resp.status_code == 401
    assert resp.json()["error"]["code"] == "AUTH_INVALID_TOKEN"


def test_me_wrong_scheme_returns_invalid_token(
    client: TestClient, seeded_user: dict[str, str]
) -> None:
    resp = client.get("/v1/auth/me", headers={"Authorization": "Basic abcd"})
    assert resp.status_code == 401
    assert resp.json()["error"]["code"] == "AUTH_INVALID_TOKEN"


def test_me_expired_token_returns_auth_expired(
    client: TestClient, seeded_user: dict[str, str]
) -> None:
    tokens = _login(client, seeded_user)
    # Re-sign a token with the same claims but exp in the past.
    payload = jwt.decode(
        tokens["access_token"],
        os.environ["JWT_SECRET"],
        algorithms=["HS256"],
    )
    payload["exp"] = payload["iat"]  # exp == iat → expired as of issue
    expired = jwt.encode(payload, os.environ["JWT_SECRET"], algorithm="HS256")

    resp = client.get("/v1/auth/me", headers={"Authorization": f"Bearer {expired}"})
    assert resp.status_code == 401
    assert resp.json()["error"]["code"] == "AUTH_EXPIRED"


def test_me_tampered_token_returns_invalid_token(
    client: TestClient, seeded_user: dict[str, str]
) -> None:
    tokens = _login(client, seeded_user)
    tampered = tokens["access_token"][:-2] + (
        "AA" if not tokens["access_token"].endswith("AA") else "BB"
    )
    resp = client.get("/v1/auth/me", headers={"Authorization": f"Bearer {tampered}"})
    assert resp.status_code == 401
    assert resp.json()["error"]["code"] == "AUTH_INVALID_TOKEN"


def test_me_token_signed_with_other_secret_rejected(
    client: TestClient, seeded_user: dict[str, str]
) -> None:
    tokens = _login(client, seeded_user)
    payload = jwt.decode(
        tokens["access_token"],
        os.environ["JWT_SECRET"],
        algorithms=["HS256"],
    )
    forged = jwt.encode(payload, "other-secret", algorithm="HS256")
    resp = client.get("/v1/auth/me", headers={"Authorization": f"Bearer {forged}"})
    assert resp.status_code == 401
    assert resp.json()["error"]["code"] == "AUTH_INVALID_TOKEN"


def test_me_respects_access_ttl_env(
    client: TestClient,
    seeded_user: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`JWT_ACCESS_TTL_SECONDS` must change the `expires_in` returned at login."""
    monkeypatch.setenv("JWT_ACCESS_TTL_SECONDS", "42")
    r = client.post(
        "/v1/auth/login",
        json={"email": seeded_user["email"], "password": seeded_user["password"]},
    )
    assert r.status_code == 200
    assert r.json()["expires_in"] == 42
