"""Exception hierarchy for the storage layer.

Backends raise these so route handlers can map to the right `AgentError`
code without coupling to backend specifics (filesystem vs S3).
"""

from __future__ import annotations


class StorageError(Exception):
    """Base for storage-related failures."""


class NotFoundError(StorageError):
    """Requested key does not exist."""


class AccessDeniedError(StorageError):
    """Caller lacks permission for the requested key."""


class StorageBackendUnavailableError(StorageError):
    """Backend (FS / S3 / MinIO) is unreachable or returned a 5xx."""
