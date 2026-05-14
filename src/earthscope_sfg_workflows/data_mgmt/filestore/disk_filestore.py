"""Unified filesystem :class:`FileStore` adapter backed by :mod:`fsspec`.

A single :class:`FsspecFileStore` handles both local paths and ``s3://`` URLs.
The filesystem implementation is selected automatically from the URL scheme:

- Plain or ``file://`` paths → local filesystem
- ``s3://bucket/key`` → S3 via :mod:`s3fs`
"""

from __future__ import annotations

import fsspec
from upath import UPath

from ..model import FileInfo




def _is_s3(url: str) -> bool:
    """Return True for any ``s3:`` URI, including POSIX-normalized ``s3:/`` variants."""
    return url.startswith("s3:")


def _normalize_url(url: str) -> str:
    """Un-normalize ``s3:/bucket`` → ``s3://bucket`` (POSIX collapses double-slash)."""
    if url.startswith("s3:/") and not url.startswith("s3://"):
        return "s3://" + url[4:]
    return url

def _open_fs(url: str, storage_options: dict) -> fsspec.AbstractFileSystem:
    url = _normalize_url(url)
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
    time.

    Methods
    -------
    exists(path)
        Return ``True`` if *path* exists on the filesystem.
    is_file(path)
        Return ``True`` if *path* is an existing regular file.
    is_dir(path)
        Return ``True`` if *path* is an existing directory.
    list_files(directory, recursive)
        List files under *directory*; recurse if *recursive*.
    write_bytes(path, data)
        Write *data* to *path*, creating parent directories as needed.
    read_bytes(path)
        Read and return the raw bytes of *path*.
    get_remote(source, target)
        Download a remote file to a local path.
    put_remote(source, target)
        Upload a local file to a remote path.
    get_remote_batch(sources, target_dir)
        Download multiple remote files to a local directory.
    put_remote_batch(sources, target_dir)
        Upload multiple local files to a remote directory.
    mkdir(path, parents)
        Create directory *path*; no-op if it already exists or on S3.
    remove(path)
        Delete the file at *path*; return ``True`` iff it existed.
    get_size(path)
        Return the size in bytes, or ``None`` if *path* is not a file.
    close()
        No-op; fsspec manages its own connection pools.
    """

    def __init__(self, root: UPath | str | None = None, storage_options: dict | None = None) -> None:
        """Initialize the store, optionally pinning it to a fixed *root*.

        Parameters
        ----------
        root : UPath or str or None, optional
            Root path or URL for the store. When given, all operations use
            the same pre-opened filesystem. Omit for dispatch mode.
        storage_options : dict or None, optional
            Extra keyword arguments forwarded to :func:`fsspec.url_to_fs`
            for S3 authentication (e.g. ``key``, ``secret``, ``endpoint_url``).
        """
        self._root = UPath(root) if root is not None else None
        self._storage_options: dict = storage_options or {}
        self._fs: fsspec.AbstractFileSystem | None = _open_fs(str(self._root), self._storage_options) if self._root is not None else None

    def _get_fs(self, path: UPath | str) -> fsspec.AbstractFileSystem:
        """Return the filesystem for *path*.

        When a *root* was given at construction, the fixed filesystem is
        reused.  In dispatch mode (no root) the filesystem is selected from
        the URL scheme of *path* at call time.
        """
        if self._fs is not None:
            return self._fs
        url = str(UPath(path) if not isinstance(path, str) else path)
        return _open_fs(url, self._storage_options)

    def exists(self, path: UPath | str) -> bool:
        """Return ``True`` if *path* exists on the filesystem (file or directory).

        Parameters
        ----------
        path : UPath or str
            The path to test.

        Returns
        -------
        bool
            ``True`` if *path* refers to an existing file or directory.
        """
        return self._get_fs(path).exists(str(UPath(path)))

    def is_file(self, path: UPath | str) -> bool:
        """Return ``True`` if *path* is an existing regular file.

        Parameters
        ----------
        path : UPath or str
            The path to test.

        Returns
        -------
        bool
            ``True`` if *path* is a regular file.
        """
        return self._get_fs(path).isfile(str(UPath(path)))

    def is_dir(self, path: UPath | str) -> bool:
        """Return ``True`` if *path* is an existing directory.

        Parameters
        ----------
        path : UPath or str
            The path to test.

        Returns
        -------
        bool
            ``True`` if *path* is a directory.
        """
        return self._get_fs(path).isdir(str(UPath(path)))

    def list_files(self, directory: UPath | str, recursive: bool = False) -> list[FileInfo]:
        """List files under *directory*; recurse if *recursive*. Skips ``._*`` entries.

        Parameters
        ----------
        directory : UPath or str
            The directory to list.
        recursive : bool, optional
            When ``True``, descend into sub-directories. Default ``False``.

        Returns
        -------
        list[FileInfo]
            :class:`FileInfo` objects for each matching file (directories excluded).
        """
        url = UPath(directory)
        fs = self._get_fs(directory)
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
            out.append(FileInfo(path=UPath(fpath), size_bytes=size, is_file=True))
        return out

    def write_bytes(self, path: UPath | str, data: bytes) -> None:
        """Write *data* to *path*, creating parent directories as needed.

        Parameters
        ----------
        path : UPath or str
            Destination path.
        data : bytes
            Raw bytes to write.
        """
        url = str(UPath(path) if not isinstance(path, str) else path)
        if not _is_s3(url):
            self.mkdir(str(UPath(url).parent))
        with self._get_fs(path).open(url, "wb") as fh:
            fh.write(data)

    def read_bytes(self, path: UPath | str) -> bytes:
        """Read and return the raw bytes of *path*.

        Parameters
        ----------
        path : UPath or str
            Path of the file to read.

        Returns
        -------
        bytes
            Raw file contents.
        """
        url = str(UPath(path) if not isinstance(path, str) else path)
        with self._get_fs(path).open(url, "rb") as fh:
            return fh.read()

    def get_remote(self, source: str, target: UPath) -> None:
        """Download a remote file to a local path.

        Parameters
        ----------
        source : str
            Remote URL of the file to download.
        target : UPath
            Local path to write the downloaded file to.
        """
        self._get_fs(source).get(source, str(target))

    def put_remote(self, source: UPath, target: str) -> None:
        """Upload a local file to a remote path.

        Parameters
        ----------
        source : UPath
            Local path of the file to upload.
        target : str
            Remote URL to upload the file to.
        """
        self._get_fs(source).put(str(source), target)

    def get_remote_batch(self, sources: list[str], target_dir: UPath) -> None:
        """Download multiple remote files to a local directory.

        Parameters
        ----------
        sources : list[str]
            Remote URLs of the files to download.
        target_dir : UPath
            Local directory to download the files into.
        """
        if sources:
            self._get_fs(sources[0]).cp(sources, str(target_dir))

    def put_remote_batch(self, sources: list[UPath], target_dir: str) -> None:
        """Upload multiple local files to a remote directory.

        Parameters
        ----------
        sources : list[UPath]
            Local paths of the files to upload.
        target_dir : str
            Remote URL of the destination directory.
        """
        for s in sources:
            self.put_remote(s, target=target_dir)

    def mkdir(self, path: UPath | str, parents: bool = True) -> None:
        """Create directory *path*; no-op if it already exists or on S3.

        Parameters
        ----------
        path : UPath or str
            Directory path to create.
        parents : bool, optional
            When ``True`` (default), create all missing parent directories.
        """
        url = str(UPath(path) if not isinstance(path, str) else path)
        if _is_s3(url):
            return  # S3 has no real directories; mkdir is always a no-op
        try:
            self._get_fs(path).mkdir(url, create_parents=parents, exist_ok=True)
        except FileExistsError:
            pass

    def remove(self, path: UPath | str) -> bool:
        """Delete the file at *path* (local or S3).

        Parameters
        ----------
        path : UPath or str
            Path of the file to delete.

        Returns
        -------
        bool
            ``True`` if the file existed and was deleted; ``False`` otherwise.
        """
        url = UPath(path) if not isinstance(path, str) else path
        try:
            self._get_fs(path).rm(url)
            return True
        except FileNotFoundError:
            return False

    def get_size(self, path: UPath | str) -> int | None:
        """Return the size in bytes, or ``None`` if *path* is not a file.

        Parameters
        ----------
        path : UPath or str
            Path of the file to size.

        Returns
        -------
        int or None
            Size in bytes, or ``None`` if *path* does not exist or is not a file.
        """
        url = UPath(path) if not isinstance(path, str) else path
        try:
            return self._get_fs(path).size(url)
        except FileNotFoundError:
            return None

    def close(self) -> None:
        """No-op; fsspec manages its own connection pools."""
        return None


__all__ = ["FsspecFileStore"]
