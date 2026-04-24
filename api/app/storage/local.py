"""LocalFilesystemBackend — Phase 1 storage on local disk.

Atomic writes via `.tmp.<uuid>` + `os.replace` (POSIX atomic rename).
Copy prefers `os.link` (hardlink, inode-shared) and falls back to
`shutil.copy2` on filesystems that don't support hardlinks (e.g.,
cross-device, FAT, some Windows configurations).
"""

from __future__ import annotations

import hashlib
import mimetypes
import os
import shutil
from datetime import UTC, datetime
from pathlib import Path
from typing import BinaryIO
from uuid import uuid4

from app.storage.backend import StorageBackend, StoredObject
from app.storage.errors import NotFoundError, StorageError
from app.storage.signed_url import build_signed_url, sign_token

_CHUNK_SIZE = 64 * 1024


class LocalFilesystemBackend(StorageBackend):
    def __init__(self, root: Path | str, *, default_user_id: str | None = None) -> None:
        self._root = Path(root).resolve()
        self._root.mkdir(parents=True, exist_ok=True)
        # Placeholder identity until T-006 wires real auth into URL signing.
        self._default_user_id = default_user_id

    def _resolve(self, key: str) -> Path:
        if not key or key.startswith("/") or "\\" in key or "\x00" in key:
            raise StorageError(f"Invalid key: {key!r}")
        candidate = (self._root / key).resolve()
        # Reject keys that resolve to the root itself (e.g. "." or "a/..") —
        # file operations against the root directory would surface as
        # IsADirectoryError 500s instead of a clean validation error.
        if candidate == self._root or self._root not in candidate.parents:
            raise StorageError(f"Key escapes storage root or targets root: {key!r}")
        return candidate

    def put(
        self,
        key: str,
        content: bytes | BinaryIO,
        content_type: str,
    ) -> StoredObject:
        final_path = self._resolve(key)
        final_path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = final_path.with_name(f"{final_path.name}.tmp.{uuid4().hex}")

        hasher = hashlib.sha256()
        size = 0
        try:
            with open(tmp_path, "wb") as f:
                if isinstance(content, bytes | bytearray):
                    data = bytes(content)
                    f.write(data)
                    hasher.update(data)
                    size = len(data)
                else:
                    while True:
                        chunk = content.read(_CHUNK_SIZE)
                        if not chunk:
                            break
                        f.write(chunk)
                        hasher.update(chunk)
                        size += len(chunk)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp_path, final_path)
        except BaseException:
            # Roll back the partial temp file so a failed put leaves no trace.
            try:
                tmp_path.unlink()
            except FileNotFoundError:
                pass
            raise

        return StoredObject(
            key=key,
            size_bytes=size,
            content_type=content_type,
            etag=hasher.hexdigest(),
            created_at=datetime.now(UTC),
        )

    def get(self, key: str) -> bytes:
        path = self._resolve(key)
        # Preflight is_file() so a key pointing at a directory surfaces as
        # NotFoundError (inside the StorageError hierarchy) instead of leaking
        # IsADirectoryError past the route's `except StorageError` mapping.
        if not path.is_file():
            raise NotFoundError(key)
        return path.read_bytes()

    def get_stream(self, key: str) -> BinaryIO:
        path = self._resolve(key)
        if not path.is_file():
            raise NotFoundError(key)
        return open(path, "rb")

    def get_signed_url(self, key: str, expires_in_seconds: int = 3600) -> str:
        token = sign_token(
            key=key,
            user_id=self._default_user_id,
            expires_in_seconds=expires_in_seconds,
        )
        return build_signed_url(key=key, token=token)

    def delete(self, key: str) -> None:
        path = self._resolve(key)
        # Delete is idempotent; a missing file or a non-file path (e.g. a
        # nested directory created by earlier puts) is a no-op, not an error.
        if not path.is_file():
            return
        path.unlink()

    def exists(self, key: str) -> bool:
        try:
            return self._resolve(key).is_file()
        except StorageError:
            return False

    def list_prefix(self, prefix: str) -> list[StoredObject]:
        if prefix.startswith("/") or "\\" in prefix or "\x00" in prefix:
            raise StorageError(f"Invalid prefix: {prefix!r}")
        prefix_path = (self._root / prefix).resolve() if prefix else self._root
        if prefix_path != self._root and self._root not in prefix_path.parents:
            raise StorageError(f"Prefix escapes storage root: {prefix!r}")
        if not prefix_path.exists():
            return []
        if prefix_path.is_file():
            return [self._stat_to_object(prefix_path)]
        return [
            self._stat_to_object(p)
            for p in sorted(prefix_path.rglob("*"))
            if p.is_file() and ".tmp." not in p.name
        ]

    def copy(self, src_key: str, dst_key: str) -> StoredObject:
        src = self._resolve(src_key)
        dst = self._resolve(dst_key)
        if not src.is_file():
            raise NotFoundError(src_key)
        # copy(k, k) would otherwise delete the source when we unlink dst.
        if src == dst:
            return self._stat_to_object(src, key_override=dst_key)
        dst.parent.mkdir(parents=True, exist_ok=True)
        # Stage into a .tmp sibling so an overwrite is atomic: on the
        # shutil.copy2 fallback path a mid-copy failure would otherwise
        # leave dst deleted or half-written. os.replace swaps the new
        # content in only after it's fully staged.
        tmp_path = dst.with_name(f"{dst.name}.tmp.{uuid4().hex}")
        try:
            try:
                os.link(src, tmp_path)
            except OSError:
                shutil.copy2(src, tmp_path)
            os.replace(tmp_path, dst)
        except BaseException:
            try:
                tmp_path.unlink()
            except FileNotFoundError:
                pass
            raise
        return self._stat_to_object(dst, key_override=dst_key)

    def _stat_to_object(self, path: Path, *, key_override: str | None = None) -> StoredObject:
        rel = key_override if key_override is not None else path.relative_to(self._root).as_posix()
        st = path.stat()
        content_type, _ = mimetypes.guess_type(path.name)
        return StoredObject(
            key=rel,
            size_bytes=st.st_size,
            content_type=content_type or "application/octet-stream",
            # mtime+size is a cheap stat-based etag; content hashes are
            # only computed on `put` where we already touch every byte.
            etag=f"{st.st_mtime_ns}-{st.st_size}",
            created_at=datetime.fromtimestamp(st.st_mtime, tz=UTC),
        )
