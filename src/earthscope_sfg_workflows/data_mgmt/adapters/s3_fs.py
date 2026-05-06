"""S3 :class:`FileStore` adapter built on :mod:`cloudpathlib`.
Treats ``Path``-shaped arguments whose string representation starts with
``s3://`` as :class:`cloudpathlib.S3Path` objects; everything else falls
through to local-filesystem behavior. This lets the same caller code
target a local working directory or an S3 prefix without branching.
"""

from __future__ import annotations

import os
from pathlib import Path

from cloudpathlib import S3Client, S3Path

from ..model import FileInfo


def _is_s3(path: Path) -> bool:
    return str(path).startswith("s3://")


def _to_cloud(path: Path, client: S3Client | None) -> S3Path:
    return S3Path(str(path), client=client) if client else S3Path(str(path))


class S3FileStore:
    """:class:`FileStore` mirror over S3 via cloudpathlib.

    Args:
        client: Optional pre-configured `S3Client`. If omitted, cloudpathlib
            uses default AWS credential resolution.
    """

    def __init__(self, client: S3Client | None = None) -> None:
        """Store an optional `S3Client` to use for cloud-path operations."""
        self._client = client

    def _wrap(self, path: Path) -> Path | S3Path:
        return _to_cloud(path, self._client) if _is_s3(path) else path

    # -- query -------------------------------------------------------------

    def exists(self, path: Path) -> bool:
        """Return True iff `path` exists (locally or in S3)."""
        return self._wrap(path).exists()

    def is_file(self, path: Path) -> bool:
        """Return True iff `path` is a regular file (locally or in S3)."""
        return self._wrap(path).is_file()

    def is_dir(self, path: Path) -> bool:
        """Return True iff `path` is a directory (or S3 prefix)."""
        return self._wrap(path).is_dir()

    def list_files(self, directory: Path, recursive: bool = False) -> list[FileInfo]:
        """List files under `directory`; recurse if `recursive`. Skips dotfiles."""
        target = self._wrap(directory)
        if not target.is_dir():
            return []
        iterator = target.rglob("*") if recursive else target.iterdir()
        out: list[FileInfo] = []
        for p in iterator:
            if p.name.startswith("._"):
                continue
            if not p.is_file():
                continue
            try:
                size: int | None = p.stat().st_size
            except Exception:
                size = None
            # Round-trip back to Path for consumers that don't depend on
            # cloudpathlib types. ``str(S3Path)`` preserves the s3:// URL.
            out.append(FileInfo(path=Path(str(p)), size_bytes=size, is_file=True))
        out.sort(key=lambda fi: fi.path.as_posix())
        return out

    def read_bytes(self, path: Path) -> bytes:
        """Return the contents of `path` as bytes."""
        return self._wrap(path).read_bytes()

    def write_bytes(self, path: Path, data: bytes) -> None:
        """Write `data` to `path`. Locally creates parents; on S3 it's a no-op."""
        target = self._wrap(path)
        # S3Path.parent.mkdir is a no-op but harmless; local Path needs it.
        if not _is_s3(path):
            target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)

    def mkdir(self, path: Path, parents: bool = True) -> None:
        """Create local dir `path`; no-op on S3 (no real directories)."""
        if _is_s3(path):
            # S3 has no real directories; cloudpathlib mkdir is a no-op.
            return
        path.mkdir(parents=parents, exist_ok=True)

    def remove(self, path: Path) -> bool:
        """Delete the file at `path` (local or S3); return True iff it existed."""
        target = self._wrap(path)
        try:
            if _is_s3(path):
                target.unlink()  # type: ignore[union-attr]
                return True
            os.remove(target)  # type: ignore[arg-type]
            return True
        except FileNotFoundError:
            return False

    def get_size(self, path: Path) -> int | None:
        """Return the size in bytes, or None if `path` is not a file."""
        target = self._wrap(path)
        try:
            return target.stat().st_size if target.is_file() else None
        except Exception:
            return None

    def close(self) -> None:
        """No-op for the S3 store; cloudpathlib manages its own clients."""
        return None


__all__ = ["S3FileStore"]
