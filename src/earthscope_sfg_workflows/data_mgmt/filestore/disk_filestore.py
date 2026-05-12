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




def _is_s3(url: str) -> bool:
    return url.startswith("s3://")

def _open_fs(url: str, storage_options: dict):
    if _is_s3(str(url)):
        fs, _ = fsspec.url_to_fs(str(url), **storage_options)
        return fs
    fs, _ = fsspec.url_to_fs(str(url))
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
        self._fs: fsspec.AbstractFileSystem | None = _open_fs(self._root, self._storage_options) if self._root is not None else None

 
    def exists(self, path: UPath | str) -> bool:
        return self._fs.exists(str(UPath(path)))

    def is_file(self, path: UPath | str) -> bool:
        return self._fs.isfile(str(UPath(path)))

    def is_dir(self, path: UPath | str) -> bool:
        return self._fs.isdir(str(UPath(path)))

    def list_files(self, directory: UPath | str, recursive: bool = False) -> list[FileInfo]:
        """List files under *directory*; recurse if *recursive*. Skips ``._*`` entries."""
        url = UPath(directory)
        if not self._fs.isdir(url):
            return []
        entries = {e["name"]: e for e in self._fs.ls(url, detail=True, recursive=recursive)}
        out: list[FileInfo] = []
        for fpath, info in entries.items():
            fname = fpath.rstrip("/").rsplit("/", 1)[-1]
            if fname.startswith("._"):
                continue
            if info.get("type") == "directory":
                continue
            size: int | None = info.get("size")
            out.append(FileInfo(path=UPath(fpath), size_bytes=size, is_file=True))
        return out

    def get_remote(self, source: str, target: UPath) -> None:
        """Download a remote file to a local path."""
        self._fs.get(source, str(target))

    def put_remote(self, source: UPath, target: str) -> None:
        """Upload a local file to a remote path."""
        self._fs.put(str(source), target)

    def get_remote_batch(self, sources: list[str], target_dir: UPath) -> None:
        """Download multiple remote files to a local directory."""

        self._fs.cp(sources, str(target_dir))

    def put_remote_batch(self, sources: list[UPath], target_dir: str) -> None:
        """Upload multiple local files to a remote directory."""
        for s in sources:
            self.put_remote(s, target=target_dir)

    def mkdir(self, path: UPath | str, parents: bool = True) -> None:
        """Create directory *path*; no-op if it already exists or on S3."""
        url = str(UPath(path) if not isinstance(path, str) else path)
        try:
            self._fs.mkdir(url, create_parents=parents, exist_ok=True)
        except FileExistsError:
            pass

    def remove(self, path: UPath | str) -> bool:
        """Delete the file at *path* (local or S3); return True iff it existed."""
        url = UPath(path) if not isinstance(path, str) else path
        try:
            self._fs.rm(url)
            return True
        except FileNotFoundError:
            return False

    def get_size(self, path: UPath | str) -> int | None:
        """Return the size in bytes, or None if *path* is not a file."""
        url = UPath(path) if not isinstance(path, str) else path
        try:
            return self._fs.size(url)
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
