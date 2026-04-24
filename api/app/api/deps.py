"""Shared FastAPI dependencies."""

from __future__ import annotations

import os
from pathlib import Path

from app.storage.backend import StorageBackend
from app.storage.local import LocalFilesystemBackend


def get_storage() -> StorageBackend:
    """Resolve the storage backend for the current request.

    Phase 1 always returns `LocalFilesystemBackend` rooted at `STORAGE_ROOT`
    (default `/storage` inside the container). Tests override this via
    `app.dependency_overrides`.
    """
    root = os.environ.get("STORAGE_ROOT", "/storage")
    return LocalFilesystemBackend(Path(root))
