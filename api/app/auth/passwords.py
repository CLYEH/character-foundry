"""Argon2id password hashing.

Uses argon2-cffi defaults. We don't tune parameters ourselves — the library's
defaults already target OWASP's recommended minimums and auto-upgrade as
argon2-cffi releases new defaults over time.
"""

from __future__ import annotations

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerifyMismatchError

_hasher = PasswordHasher()

# Real Argon2 hash used as a comparand for unknown-email logins so
# `verify_password` does equivalent work for "no such user" and "wrong
# password". Computed once at import; the plaintext is arbitrary and never
# matches a real password.
PLACEHOLDER_HASH = _hasher.hash("placeholder-never-matches")


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(password: str, password_hash: str) -> bool:
    try:
        _hasher.verify(password_hash, password)
    except (VerifyMismatchError, InvalidHashError):
        return False
    return True
