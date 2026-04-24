from __future__ import annotations

import io
import sys
from pathlib import Path

import pytest

from app.storage.errors import NotFoundError, StorageError
from app.storage.local import LocalFilesystemBackend


@pytest.fixture
def backend(tmp_path: Path) -> LocalFilesystemBackend:
    return LocalFilesystemBackend(tmp_path)


def test_put_and_get_roundtrip_bytes(backend: LocalFilesystemBackend) -> None:
    obj = backend.put("characters/abc/base.png", b"PNGDATA", "image/png")
    assert obj.key == "characters/abc/base.png"
    assert obj.size_bytes == len(b"PNGDATA")
    assert obj.content_type == "image/png"
    assert obj.etag  # sha256 hex
    assert backend.get("characters/abc/base.png") == b"PNGDATA"


def test_put_overwrites_existing(backend: LocalFilesystemBackend) -> None:
    backend.put("k.txt", b"first", "text/plain")
    backend.put("k.txt", b"second", "text/plain")
    assert backend.get("k.txt") == b"second"


def test_put_streamed_content(backend: LocalFilesystemBackend) -> None:
    payload = b"0123456789" * 5000  # 50KB, larger than one chunk read
    obj = backend.put("big.bin", io.BytesIO(payload), "application/octet-stream")
    assert obj.size_bytes == len(payload)
    assert backend.get("big.bin") == payload


def test_put_atomic_no_partial_file_on_failure(
    backend: LocalFilesystemBackend, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    class Boom(io.RawIOBase):
        def readable(self) -> bool:
            return True

        def read(self, _size: int = -1) -> bytes:
            raise RuntimeError("upstream failed mid-stream")

    with pytest.raises(RuntimeError, match="upstream failed"):
        backend.put("characters/x/base.png", Boom(), "image/png")

    # Final file must not exist; no leftover .tmp.* siblings either.
    target = tmp_path / "characters" / "x" / "base.png"
    assert not target.exists()
    leftover = list((tmp_path / "characters" / "x").glob("*"))
    assert leftover == [], f"unexpected leftover files: {leftover}"


def test_get_missing_raises_not_found(backend: LocalFilesystemBackend) -> None:
    with pytest.raises(NotFoundError):
        backend.get("missing.png")


def test_get_on_directory_key_raises_not_found(
    backend: LocalFilesystemBackend,
) -> None:
    # Nested put creates characters/abc/ as a directory.
    backend.put("characters/abc/base.png", b"x", "image/png")
    # Key targeting the directory itself must surface as NotFoundError, not
    # IsADirectoryError leaking past the storage exception contract.
    with pytest.raises(NotFoundError):
        backend.get("characters/abc")


def test_get_stream_on_directory_key_raises_not_found(
    backend: LocalFilesystemBackend,
) -> None:
    backend.put("characters/abc/base.png", b"x", "image/png")
    with pytest.raises(NotFoundError):
        backend.get_stream("characters/abc")


def test_delete_on_directory_key_is_noop(
    backend: LocalFilesystemBackend,
) -> None:
    backend.put("characters/abc/base.png", b"x", "image/png")
    # Deleting a key that resolves to a directory must not raise and must not
    # remove the directory or its contents.
    backend.delete("characters/abc")
    assert backend.get("characters/abc/base.png") == b"x"


def test_get_stream_yields_bytes(backend: LocalFilesystemBackend) -> None:
    backend.put("a/b.txt", b"hello", "text/plain")
    with backend.get_stream("a/b.txt") as fh:
        assert fh.read() == b"hello"


def test_delete_is_idempotent(backend: LocalFilesystemBackend) -> None:
    backend.put("k.txt", b"x", "text/plain")
    backend.delete("k.txt")
    backend.delete("k.txt")  # second call must not raise
    assert not backend.exists("k.txt")


def test_exists(backend: LocalFilesystemBackend) -> None:
    assert not backend.exists("nope.txt")
    backend.put("here.txt", b"x", "text/plain")
    assert backend.exists("here.txt")


def test_list_prefix_returns_all_files(backend: LocalFilesystemBackend) -> None:
    backend.put("characters/c1/base.png", b"a", "image/png")
    backend.put("characters/c1/aliases/a1.png", b"b", "image/png")
    backend.put("characters/c1/motions/m1.mp4", b"c", "video/mp4")
    backend.put("characters/c2/base.png", b"d", "image/png")

    listed = backend.list_prefix("characters/c1")
    keys = sorted(o.key for o in listed)
    assert keys == [
        "characters/c1/aliases/a1.png",
        "characters/c1/base.png",
        "characters/c1/motions/m1.mp4",
    ]
    # Sanity: c2 not picked up
    assert all("c2" not in k for k in keys)


def test_list_prefix_missing_returns_empty(backend: LocalFilesystemBackend) -> None:
    assert backend.list_prefix("nothing/here") == []


def test_list_prefix_includes_user_filenames_with_tmp_substring(
    backend: LocalFilesystemBackend,
) -> None:
    """`.tmp.` alone in a filename must not hide a legitimate user key —
    only the exact `.tmp.<32-hex>` sibling pattern is a backend temp artifact."""
    backend.put("characters/c1/avatar.tmp.v2.png", b"user-file", "image/png")
    backend.put("characters/c1/base.png", b"base", "image/png")

    listed = sorted(o.key for o in backend.list_prefix("characters/c1"))
    assert listed == [
        "characters/c1/avatar.tmp.v2.png",
        "characters/c1/base.png",
    ]


def test_list_prefix_excludes_real_tmp_uuid_siblings(
    backend: LocalFilesystemBackend, tmp_path: Path
) -> None:
    """A real `.tmp.<32-hex>` sibling left over (e.g., from a crashed put on
    another process) must NOT show up in list_prefix results."""
    backend.put("characters/c1/base.png", b"x", "image/png")
    # Simulate a leftover temp sibling matching the exact pattern.
    leftover = tmp_path / "characters" / "c1" / ("base.png.tmp." + "a" * 32)
    leftover.write_bytes(b"partial")

    listed = sorted(o.key for o in backend.list_prefix("characters/c1"))
    assert listed == ["characters/c1/base.png"]


def test_copy_creates_hardlink_when_supported(
    backend: LocalFilesystemBackend, tmp_path: Path
) -> None:
    backend.put("src.png", b"DATA", "image/png")
    backend.copy("src.png", "dst.png")

    src = tmp_path / "src.png"
    dst = tmp_path / "dst.png"
    assert src.read_bytes() == dst.read_bytes() == b"DATA"

    # Where hardlinks are supported (POSIX, NTFS), st_nlink reflects the
    # second link. On filesystems that fell back to shutil.copy2 (e.g.
    # cross-device, FAT, some Windows configs) we accept independent files.
    if sys.platform != "win32":
        assert src.stat().st_nlink >= 2, "expected hardlink (st_nlink >= 2)"


def test_copy_missing_source_raises_not_found(backend: LocalFilesystemBackend) -> None:
    with pytest.raises(NotFoundError):
        backend.copy("ghost.png", "anywhere.png")


def test_copy_same_src_and_dst_is_nondestructive(
    backend: LocalFilesystemBackend,
) -> None:
    backend.put("same.png", b"DATA", "image/png")
    obj = backend.copy("same.png", "same.png")
    # File must still exist with original content — copy(k, k) is a no-op.
    assert backend.get("same.png") == b"DATA"
    assert obj.key == "same.png"
    assert obj.size_bytes == len(b"DATA")


@pytest.mark.parametrize(
    "bad_key",
    ["", "/abs/path", "back\\slash", "ok\x00null"],
)
def test_resolve_rejects_bad_keys(backend: LocalFilesystemBackend, bad_key: str) -> None:
    with pytest.raises(StorageError):
        backend.put(bad_key, b"x", "text/plain")


def test_resolve_blocks_path_traversal(backend: LocalFilesystemBackend) -> None:
    with pytest.raises(StorageError):
        backend.put("../escape.txt", b"x", "text/plain")


@pytest.mark.parametrize("root_key", [".", "a/..", "./"])
def test_resolve_rejects_keys_targeting_root(
    backend: LocalFilesystemBackend, root_key: str
) -> None:
    with pytest.raises(StorageError):
        backend.put(root_key, b"x", "text/plain")


def test_copy_overwrite_is_atomic_on_failure(
    backend: LocalFilesystemBackend,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """If the fallback copy path fails mid-stage, the existing dst survives."""
    backend.put("src.png", b"NEW_DATA", "image/png")
    backend.put("dst.png", b"OLD_DATA", "image/png")

    # Force the hardlink path to fail so we take the shutil.copy2 branch,
    # and make copy2 blow up to simulate a mid-copy I/O failure.
    def boom_link(_src: object, _dst: object) -> None:
        raise OSError("hardlink not supported")

    def boom_copy2(_src: object, _dst: object) -> None:
        raise OSError("disk full mid-copy")

    monkeypatch.setattr("app.storage.local.os.link", boom_link)
    monkeypatch.setattr("app.storage.local.shutil.copy2", boom_copy2)

    with pytest.raises(OSError, match="disk full"):
        backend.copy("src.png", "dst.png")

    # Original dst.png must still be intact; no leftover .tmp.* files.
    assert backend.get("dst.png") == b"OLD_DATA"
    leftover = [p for p in tmp_path.rglob("*.tmp.*") if p.is_file()]
    assert leftover == [], f"unexpected leftover temp files: {leftover}"
