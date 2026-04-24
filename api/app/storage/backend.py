"""StorageBackend abstract interface + StoredObject DTO.

Backends are addressed by opaque `key` strings (POSIX-style paths). Phase 1
ships LocalFilesystemBackend; S3 / MinIO backends slot in without touching
callers. See planning/data/storage-layout.md §3 for the contract.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime
from typing import BinaryIO


@dataclass(frozen=True)
class StoredObject:
    key: str
    size_bytes: int
    content_type: str
    etag: str
    created_at: datetime


class StorageBackend(ABC):
    @abstractmethod
    def put(
        self,
        key: str,
        content: bytes | BinaryIO,
        content_type: str,
    ) -> StoredObject:
        """Upload content at `key`. Overwrites if it already exists."""

    @abstractmethod
    def get(self, key: str) -> bytes:
        """Download full content. Raises `NotFoundError` if missing."""

    @abstractmethod
    def get_stream(self, key: str) -> BinaryIO:
        """Open a read stream for large objects (motion videos, exports)."""

    @abstractmethod
    def get_signed_url(self, key: str, expires_in_seconds: int = 3600) -> str:
        """Return a time-limited URL the client can fetch directly."""

    @abstractmethod
    def delete(self, key: str) -> None:
        """Hard delete. Idempotent — missing key is not an error."""

    @abstractmethod
    def exists(self, key: str) -> bool:
        """True if a file exists at `key`."""

    @abstractmethod
    def list_prefix(self, prefix: str) -> list[StoredObject]:
        """List every file beneath `prefix`. Used by ZIP export enumeration."""

    @abstractmethod
    def copy(self, src_key: str, dst_key: str) -> StoredObject:
        """Server-side copy. No download+reupload round-trip."""
