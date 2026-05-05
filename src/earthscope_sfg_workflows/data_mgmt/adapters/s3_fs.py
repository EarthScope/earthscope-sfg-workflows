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

    Parameters
    ----------
    client:
        Optional :class:`cloudpathlib.S3Client` for credential / endpoint
        overrides. If ``None``, cloudpathlib's default client is used.
    """

    def __init__(self, client: S3Client | None = None) -> None:
        self._client = client

    def _wrap(self, path: Path) -> Path | S3Path:
        return _to_cloud(path, self._client) if _is_s3(path) else path

    # -- query -------------------------------------------------------------

    def exists(self, path: Path) -> bool:
        return self._wrap(path).exists()

    def is_file(self, path: Path) -> bool:
        return self._wrap(path).is_file()

    def is_dir(self, path: Path) -> bool:
        return self._wrap(path).is_dir()

    def list_files(self, directory: Path, recursive: bool = False) -> list[FileInfo]:
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
        return self._wrap(path).read_bytes()

    def write_bytes(self, path: Path, data: bytes) -> None:
        target = self._wrap(path)
        # S3Path.parent.mkdir is a no-op but harmless; local Path needs it.
        if not _is_s3(path):
            target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)

    def mkdir(self, path: Path, parents: bool = True) -> None:
        if _is_s3(path):
            # S3 has no real directories; cloudpathlib mkdir is a no-op.
            return
        path.mkdir(parents=parents, exist_ok=True)

    def remove(self, path: Path) -> bool:
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
        target = self._wrap(path)
        try:
            return target.stat().st_size if target.is_file() else None
        except Exception:
            return None

    def close(self) -> None:
        return None


__all__ = ["S3FileStore"]
