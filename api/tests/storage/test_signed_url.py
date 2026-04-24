from __future__ import annotations

import time

import jwt
import pytest

from app.storage.signed_url import (
    SignedURLExpired,
    SignedURLInvalid,
    build_signed_url,
    sign_token,
    verify_token,
)

SECRET = "test-storage-secret"


def test_sign_then_verify_roundtrip() -> None:
    token = sign_token(
        key="characters/abc/base.png",
        user_id="user-1",
        expires_in_seconds=60,
        secret=SECRET,
    )
    payload = verify_token(token, expected_key="characters/abc/base.png", secret=SECRET)
    assert payload["key"] == "characters/abc/base.png"
    assert payload["user_id"] == "user-1"


def test_expired_token_raises_expired() -> None:
    token = sign_token(
        key="k.png",
        user_id=None,
        expires_in_seconds=-10,  # already expired
        secret=SECRET,
    )
    with pytest.raises(SignedURLExpired):
        verify_token(token, expected_key="k.png", secret=SECRET)


def test_tampered_token_raises_invalid() -> None:
    token = sign_token(key="k.png", user_id=None, expires_in_seconds=60, secret=SECRET)
    tampered = token[:-2] + ("AA" if not token.endswith("AA") else "BB")
    with pytest.raises(SignedURLInvalid):
        verify_token(tampered, expected_key="k.png", secret=SECRET)


def test_wrong_secret_raises_invalid() -> None:
    token = sign_token(key="k.png", user_id=None, expires_in_seconds=60, secret=SECRET)
    with pytest.raises(SignedURLInvalid):
        verify_token(token, expected_key="k.png", secret="other-secret")


def test_key_mismatch_raises_invalid() -> None:
    token = sign_token(key="k1.png", user_id=None, expires_in_seconds=60, secret=SECRET)
    with pytest.raises(SignedURLInvalid):
        verify_token(token, expected_key="k2.png", secret=SECRET)


def test_unsigned_token_rejected() -> None:
    # Crafted payload signed with `none` algorithm — must be rejected.
    forged = jwt.encode(
        {"key": "k.png", "exp": int(time.time()) + 60},
        key="",
        algorithm="none",
    )
    with pytest.raises(SignedURLInvalid):
        verify_token(forged, expected_key="k.png", secret=SECRET)


def test_build_signed_url_format() -> None:
    url = build_signed_url(key="characters/abc/base.png", token="TOK")
    assert url == "/storage/characters/abc/base.png?token=TOK"


def test_build_signed_url_escapes_unsafe_chars() -> None:
    url = build_signed_url(key="weird name?.png", token="TOK")
    # Spaces and `?` in the key get percent-encoded; path separators stay.
    assert "weird%20name" in url
    assert url.endswith("?token=TOK")


def test_missing_secret_env_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("STORAGE_SIGNED_URL_SECRET", raising=False)
    with pytest.raises(RuntimeError):
        sign_token(key="k.png", user_id=None, expires_in_seconds=60)
