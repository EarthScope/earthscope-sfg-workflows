"""Unified filesystem :class:`FileStore` adapter backed by :mod:`fsspec`.

A single :class:`FsspecFileStore` handles both local paths and ``s3://`` URLs.
The filesystem implementation is selected automatically from the URL scheme:

- Plain or ``file://`` paths → local filesystem
- ``s3://bucket/key`` → S3 via :mod:`s3fs`

Backward-compatible aliases :data:`LocalFileStore` and :data:`S3FileStore`
both point to :class:`FsspecFileStore`.
"""

from __future__ import annotations

import fsspec
from upath import UPath

from ..model import FileInfo


def _url(path: UPath) -> str:
    """Return a URL string for *path*, restoring ``s3://`` if pathlib normalised it.

    On POSIX systems ``Path("s3://bucket/k")`` collapses to ``"s3:/bucket/k"``.
    This helper undoes that normalisation so fsspec receives a valid scheme URL.
    """
    s = str(path)
    if s.startswith("s3:/") and not s.startswith("s3://"):
        s = "s3://" + s[4:]
    return s


def _is_s3(url: str) -> bool:
    return url.startswith("s3://")


def _open_fs(url: str, storage_options: dict):
    if _is_s3(url):
        fs, _ = fsspec.url_to_fs(url, **storage_options)
        return fs
    fs, _ = fsspec.url_to_fs(url)
    return fs


class FsspecFileStore:
    """Unified :class:`FileStore` adapter using :mod:`fsspec`.

    Works transparently with local paths and ``s3://`` URLs.  Pass
    *storage_options* to configure S3 credentials (``key``, ``secret``,
    ``token``, ``endpoint_url``, …).  Omit to use default AWS credential
    resolution.

    When *root* is omitted the store operates in dispatch mode: each
    operation selects the right filesystem from the path scheme at call
    time.  This is the ``S3FileStore`` use-case where the caller may mix
    local and remote paths.

    The backward-compatible aliases :data:`LocalFileStore` and
    :data:`S3FileStore` refer to this class.
    """

    def __init__(self, root: UPath | str | None = None, storage_options: dict | None = None) -> None:
        self._root = UPath(root) if root is not None else None
        self._storage_options: dict = storage_options or {}
        self._fs = _open_fs(_url(self._root), self._storage_options) if self._root is not None else None

    def _fs_for(self, path: UPath | str):
        """Return the right fsspec filesystem for *path*."""
        if self._fs is not None:
            return self._fs
        url = _url(UPath(path)) if not isinstance(path, str) else path
        return _open_fs(url, self._storage_options)

    def exists(self, path: UPath | str) -> bool:
        return self._fs_for(path).exists(_url(UPath(path)) if not isinstance(path, str) else path)

    def is_file(self, path: UPath | str) -> bool:
        return self._fs_for(path).isfile(_url(UPath(path)) if not isinstance(path, str) else path)

    def is_dir(self, path: UPath | str) -> bool:
        return self._fs_for(path).isdir(_url(UPath(path)) if not isinstance(path, str) else path)

    def list_files(self, directory: UPath | str, recursive: bool = False) -> list[FileInfo]:
        """List files under *directory*; recurse if *recursive*. Skips ``._*`` entries."""
        url = _url(UPath(directory)) if not isinstance(directory, str) else directory
        fs = self._fs_for(directory)
        if not fs.isdir(url):
            return []
        entries = {e["name"]: e for e in fs.ls(url, detail=True, recursive=recursive)}
        out: list[FileInfo] = []
        for fpath, info in entries.items():
            fname = fpath.rstrip("/").rsplit("/", 1)[-1]
            if fname.startswith("._"):
                continue
            if info.get("type") == "directory":
                continue
            size: int | None = info.get("size")
            out.append(FileInfo(path=fpath, size_bytes=size, is_file=True))
        return out

    def read_bytes(self, path: UPath | str) -> bytes:
        """Return the raw bytes of *path*."""
        url = _url(UPath(path)) if not isinstance(path, str) else path
        with self._fs_for(path).open(url, "rb") as fh:
            return fh.read()

    def write_bytes(self, path: UPath | str, data: bytes) -> None:
        """Write *data* to *path*, creating parent directories as needed."""
        url = _url(UPath(path)) if not isinstance(path, str) else path
        fs = self._fs_for(path)
        if not _is_s3(url):
            import os
            os.makedirs(os.path.dirname(url) or ".", exist_ok=True)
        with fs.open(url, "wb") as fh:
            fh.write(data)

    def get_remote(self, source: str, target: UPath) -> None:
        """Download a remote file to a local path."""
        self._fs_for(source).get(source, str(target))

    def put_remote(self, source: UPath, target: str) -> None:
        """Upload a local file to a remote path."""
        self._fs_for(target).put(str(source), target)

    def get_remote_batch(self, sources: list[str], target_dir: UPath) -> None:
        """Download multiple remote files to a local directory."""
        if sources:
            self._fs_for(sources[0]).cp(sources, str(target_dir))

    def put_remote_batch(self, sources: list[UPath], target_dir: str) -> None:
        """Upload multiple local files to a remote directory."""
        for s in sources:
            self.put_remote(s, target=target_dir)

    def mkdir(self, path: UPath | str, parents: bool = True) -> None:
        """Create local directory *path*; no-op on S3 (no real directories)."""
        url = _url(UPath(path)) if not isinstance(path, str) else path
        if _is_s3(url):
            return
        self._fs_for(path).mkdir(url, create_parents=parents, exist_ok=True)

    def remove(self, path: UPath | str) -> bool:
        """Delete the file at *path* (local or S3); return True iff it existed."""
        url = _url(UPath(path)) if not isinstance(path, str) else path
        try:
            self._fs_for(path).rm(url)
            return True
        except FileNotFoundError:
            return False

    def get_size(self, path: UPath | str) -> int | None:
        """Return the size in bytes, or None if *path* is not a file."""
        url = _url(UPath(path)) if not isinstance(path, str) else path
        try:
            return self._fs_for(path).size(url)
        except FileNotFoundError:
            return None

    def close(self) -> None:
        """No-op; fsspec manages its own connection pools."""
        return None


#: Alias kept for backward compatibility — points to :class:`FsspecFileStore`.
LocalFileStore = FsspecFileStore

#: Alias kept for backward compatibility — points to :class:`FsspecFileStore`.
S3FileStore = FsspecFileStore

__all__ = ["FsspecFileStore", "LocalFileStore", "S3FileStore"]
