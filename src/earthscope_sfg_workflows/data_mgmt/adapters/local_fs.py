"""Local filesystem :class:`FileStore` adapter."""

from __future__ import annotations

import os
from pathlib import Path

from ..model import FileInfo


class LocalFileStore:
    """Thin wrapper around :mod:`pathlib` implementing :class:`FileStore`."""

    def exists(self, path: Path) -> bool:
        """Return True iff `path` exists on disk."""
        return path.exists()

    def is_file(self, path: Path) -> bool:
        """Return True iff `path` is a regular file."""
        return path.is_file()

    def is_dir(self, path: Path) -> bool:
        """Return True iff `path` is a directory."""
        return path.is_dir()

    def list_files(self, directory: Path, recursive: bool = False) -> list[FileInfo]:
        """List files under `directory`; recurse if `recursive`. Skips dotfiles."""
        if not directory.is_dir():
            return []
        iterator = directory.rglob("*") if recursive else directory.iterdir()
        out: list[FileInfo] = []
        for p in iterator:
            if p.name.startswith("._"):
                continue
            if not p.is_file():
                continue
            try:
                size = p.stat().st_size
            except OSError:
                size = None
            out.append(FileInfo(path=p, size_bytes=size, is_file=True))
        out.sort(key=lambda fi: fi.path.as_posix())
        return out

    def read_bytes(self, path: Path) -> bytes:
        """Return the contents of `path` as bytes."""
        return path.read_bytes()

    def write_bytes(self, path: Path, data: bytes) -> None:
        """Write `data` to `path`, creating parent directories as needed."""
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)

    def mkdir(self, path: Path, parents: bool = True) -> None:
        """Create directory `path`; idempotent. Creates parents iff `parents`."""
        path.mkdir(parents=parents, exist_ok=True)

    def remove(self, path: Path) -> bool:
        """Delete the file at `path`; return True iff it existed."""
        try:
            os.remove(path)
            return True
        except FileNotFoundError:
            return False

    def get_size(self, path: Path) -> int | None:
        """Return the file size in bytes, or None if `path` is not a file."""
        try:
            return path.stat().st_size if path.is_file() else None
        except OSError:
            return None

    def close(self) -> None:
        """No-op for the local filesystem store."""
        return None


__all__ = ["LocalFileStore"]
