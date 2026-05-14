"""Port (Protocol) definitions for the data_mgmt package.
Three explicit ports define the boundary between the deep domain core and
swappable infrastructure:

* ``AssetStore`` — catalog persistence (SQLite locally, Postgres/RDS in cloud,
  in-memory in tests).
* ``FileStore`` — filesystem / object-store I/O (local, S3, in-memory).
* ``ArchiveSource`` — external archive discovery & download (EarthScope SDK in
  prod, fake in tests).

The domain core depends only on these Protocols. All adapters live under
``data_mgmt.adapters``.

See ``plans/rfc-a-data-mgmt-ports-and-adapters.md``.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable
from upath import UPath

from .model import ArchiveFile, AssetEntry, AssetKind, FileInfo


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------


class ArchiveError(Exception):
    """Base for all archive-related errors."""


class ArchiveAuthError(ArchiveError):
    """Authentication or authorization failure against the archive."""


class ArchiveNotFoundError(ArchiveError):
    """The requested archive resource does not exist."""


# ---------------------------------------------------------------------------
# AssetStore — catalog persistence
# ---------------------------------------------------------------------------


@runtime_checkable
class AssetCatalogPort(Protocol):
    """Persistence port for the asset catalog.

    Guarantees:

    * Idempotent reads.
    * Transactional writes (commit-or-rollback per call).
    * Returns immutable :class:`AssetEntry` instances. Callers never see ORM
      rows, sessions, or connections.

    Methods
    -------
    add(asset)
        Insert *asset* and return a copy with ``id`` populated.
    update(asset)
        Update an existing row by ``asset.id``.
    mark_processed_bulk(asset_ids)
        Mark multiple assets as processed; returns count of updated rows.
    by_id(asset_id)
        Look up an asset by primary key.
    by_local_path(path)
        Return all assets with ``local_path == path``.
    assets_for(kind, *, network, station, campaign)
        Return assets matching the given scope fields.
    delete(kind, *, network, station, campaign)
        Delete assets matching the given scope fields.
    delete_by_id(asset_id)
        Delete one asset by id.
    count_by_kind(*, network, station, campaign)
        Aggregate count of assets per kind in the specified scope.
    distinct_values(field, **filters)
        Return sorted distinct non-null values of *field*.
    add_merge_job(parent_type, child_type, parent_ids)
        Record a merge job.
    is_merge_complete(parent_type, child_type, parent_ids)
        Check whether a previously recorded merge job exists.
    close()
        Release resources (DB connections, etc.).
    """

    def add(self, asset: AssetEntry) -> AssetEntry:
        """Insert ``asset`` and return a copy with ``id`` populated.

        Parameters
        ----------
        asset : AssetEntry
            The asset to insert.

        Returns
        -------
        AssetEntry
            A copy of *asset* with ``id`` set to the new primary key.
        """
        ...

    def update(self, asset: AssetEntry) -> bool:
        """Update an existing row by ``asset.id``. Return True if a row changed.

        Parameters
        ----------
        asset : AssetEntry
            Asset with updated fields; must have ``id`` set.

        Returns
        -------
        bool
            ``True`` if exactly one row was updated.
        """
        ...

    def mark_processed_bulk(self, asset_ids: list[int]) -> int:
        """Mark multiple assets as processed by id. Returns count of updated rows.

        Parameters
        ----------
        asset_ids : list[int]
            Primary keys of assets to mark as processed.

        Returns
        -------
        int
            Number of rows that were updated.
        """
        ...

    def by_id(self, asset_id: int) -> AssetEntry | None:
        """Look up an asset by primary key. Return ``None`` if missing.

        Parameters
        ----------
        asset_id : int
            Primary key of the asset to retrieve.

        Returns
        -------
        AssetEntry or None
            The matching asset, or ``None`` if no row exists.
        """
        ...

    def by_local_path(self, path: UPath) -> list[AssetEntry]:
        """Return all assets with ``local_path == path``.

        Parameters
        ----------
        path : UPath
            Local filesystem path to match.

        Returns
        -------
        list[AssetEntry]
            All assets whose ``local_path`` equals *path*.
        """
        ...

    def assets_for(
        self,
        kind: AssetKind | None = None,
        *,
        network: str | None = None,
        station: str | None = None,
        campaign: str | None = None,
    ) -> list[AssetEntry]:
        """Return assets matching the given scope fields, optionally filtered by ``kind``.

        Parameters
        ----------
        kind : AssetKind or None, optional
            Asset type filter, by default ``None`` (all kinds).
        network : str or None, optional
            Network identifier filter, by default ``None``.
        station : str or None, optional
            Station identifier filter, by default ``None``.
        campaign : str or None, optional
            Campaign identifier filter, by default ``None``.

        Returns
        -------
        list[AssetEntry]
            Matching assets.
        """
        ...

    def delete(
        self,
        kind: AssetKind | None = None,
        *,
        network: str | None = None,
        station: str | None = None,
        campaign: str | None = None,
    ) -> int:
        """Delete assets matching the given scope fields (and optional kind). Return count.

        Parameters
        ----------
        kind : AssetKind or None, optional
            Asset type filter, by default ``None`` (all kinds).
        network : str or None, optional
            Network identifier filter, by default ``None``.
        station : str or None, optional
            Station identifier filter, by default ``None``.
        campaign : str or None, optional
            Campaign identifier filter, by default ``None``.

        Returns
        -------
        int
            Number of rows deleted.
        """
        ...

    def delete_by_id(self, asset_id: int) -> bool:
        """Delete one asset by id. Return True if deleted.

        Parameters
        ----------
        asset_id : int
            Primary key of the asset to delete.

        Returns
        -------
        bool
            ``True`` if a row was found and deleted.
        """
        ...

    def count_by_kind(
        self,
        *,
        network: str | None = None,
        station: str | None = None,
        campaign: str | None = None,
    ) -> dict[AssetKind, int]:
        """Aggregate count of assets per kind in the specified scope.

        Parameters
        ----------
        network : str or None, optional
            Network identifier filter, by default ``None``.
        station : str or None, optional
            Station identifier filter, by default ``None``.
        campaign : str or None, optional
            Campaign identifier filter, by default ``None``.

        Returns
        -------
        dict[AssetKind, int]
            Mapping from each :class:`AssetKind` to its row count.
        """
        ...

    def distinct_values(self, field: str, **filters: str | None) -> list[str]:
        """Return sorted distinct non-null values of *field* matching *filters*.

        Parameters
        ----------
        field : str
            Column name to aggregate. Supported values: ``"network"``,
            ``"station"``, ``"campaign"``.
        **filters : str or None
            Keyword filters narrowing the scope. Supported keys: ``network``,
            ``station``, ``campaign``.

        Returns
        -------
        list of str
            Sorted list of distinct non-null values.
        """
        ...

    # -- merge job tracking (carried over from legacy MergeJobs table) ----

    def add_merge_job(
        self,
        parent_type: str,
        child_type: str,
        parent_ids: list[int] | list[str],
    ) -> None:
        """Record a merge job (deterministic by sorted, dash-joined parent ids).

        Parameters
        ----------
        parent_type : str
            Asset type label of the parent assets.
        child_type : str
            Asset type label of the merged child asset.
        parent_ids : list[int] or list[str]
            Identifiers of the parent assets involved in the merge.
        """
        ...

    def is_merge_complete(
        self,
        parent_type: str,
        child_type: str,
        parent_ids: list[int] | list[str],
    ) -> bool:
        """Check whether a previously recorded merge job exists for this signature.

        Parameters
        ----------
        parent_type : str
            Asset type label of the parent assets.
        child_type : str
            Asset type label of the merged child asset.
        parent_ids : list[int] or list[str]
            Identifiers of the parent assets.

        Returns
        -------
        bool
            ``True`` if a matching merge job record exists.
        """
        ...

    def close(self) -> None:
        """Release resources (DB connections, etc.)."""
        ...


# ---------------------------------------------------------------------------
# FileStore — filesystem / object-store
# ---------------------------------------------------------------------------


@runtime_checkable
class FileStorePort(Protocol):
    """Filesystem abstraction. Implementations: local, S3, in-memory.

    Methods
    -------
    exists(path)
        Return ``True`` if *path* exists.
    is_file(path)
        Return ``True`` if *path* is an existing regular file.
    is_dir(path)
        Return ``True`` if *path* is an existing directory.
    list_files(directory, recursive)
        List files under *directory*.
    get_remote(source, target)
        Copy a remote file to a local target.
    put_remote(source, target)
        Copy a local file to a remote target.
    mkdir(path, parents)
        Create a directory (and parents). Idempotent.
    remove(path)
        Delete a file.
    get_size(path)
        Return size in bytes, or ``None`` if not a file.
    close()
        Release any held resources.
    """

    def exists(self, path: UPath) -> bool:
        """Return True if *path* exists (file or directory).

        Parameters
        ----------
        path : UPath
            Filesystem path to test.

        Returns
        -------
        bool
            ``True`` if the path exists.
        """
        ...

    def is_file(self, path: UPath) -> bool:
        """Return True if *path* is an existing regular file.

        Parameters
        ----------
        path : UPath
            Filesystem path to test.

        Returns
        -------
        bool
            ``True`` if the path exists and is a regular file.
        """
        ...

    def is_dir(self, path: UPath) -> bool:
        """Return True if *path* is an existing directory.

        Parameters
        ----------
        path : UPath
            Filesystem path to test.

        Returns
        -------
        bool
            ``True`` if the path exists and is a directory.
        """
        ...

    def list_files(
        self,
        directory: UPath,
        recursive: bool = False,
    ) -> list[FileInfo]:
        """List files under ``directory``. Hidden ``._*`` entries are excluded.

        Parameters
        ----------
        directory : UPath
            Directory to list.
        recursive : bool, optional
            If ``True``, descend into subdirectories, by default ``False``.

        Returns
        -------
        list[FileInfo]
            :class:`FileInfo` entries for each file found.
        """
        ...

    def get_remote(self, source: str, target: UPath) -> None:
        """Copy the file at ``source`` URL/path to the local ``target``.

        Parameters
        ----------
        source : str
            Remote URL or path of the file to download.
        target : UPath
            Local destination path.
        """
        ...

    def put_remote(self, source: UPath, target: str) -> None:
        """Copy the local ``source`` file to the remote ``target`` URL/path.

        Parameters
        ----------
        source : UPath
            Local file to upload.
        target : str
            Remote URL or path to upload to.
        """
        ...

    def mkdir(self, path: UPath, parents: bool = True) -> None:
        """Create ``path`` (and parents). Idempotent.

        Parameters
        ----------
        path : UPath
            Directory to create.
        parents : bool, optional
            If ``True``, create intermediate directories as needed,
            by default ``True``.
        """
        ...

    def remove(self, path: UPath) -> bool:
        """Delete a file. Return True if a file was deleted.

        Parameters
        ----------
        path : UPath
            Path of the file to delete.

        Returns
        -------
        bool
            ``True`` if the file existed and was deleted.
        """
        ...

    def get_size(self, path: UPath) -> int | None:
        """Size in bytes, or ``None`` if not a file.

        Parameters
        ----------
        path : UPath
            Filesystem path to measure.

        Returns
        -------
        int or None
            File size in bytes, or ``None`` if *path* is not a file.
        """
        ...

    def close(self) -> None:
        """Release any held resources (connections, file handles)."""
        ...


# ---------------------------------------------------------------------------
# ArchiveSource — external archive
# ---------------------------------------------------------------------------


@runtime_checkable
class ArchiveSourcePort(Protocol):
    """External archive discovery & download (EarthScope, S3, fake).

    All I/O is explicit. Callers manage authentication lifecycle via
    ``authenticate``.

    Methods
    -------
    list_files(directory_url)
        Enumerate files under *directory_url*.
    download_file(file_url, dest_path)
        Download *file_url* to *dest_path*.
    authenticate(profile)
        Refresh or acquire credentials.
    close()
        Release any held resources.
    """

    def list_files(self, directory_url: str) -> list[ArchiveFile]:
        """Enumerate files under ``directory_url``.

        Parameters
        ----------
        directory_url : str
            URL of the remote directory to list.

        Returns
        -------
        list[ArchiveFile]
            :class:`ArchiveFile` descriptors for each file found.
        """
        ...

    def download_file(self, file_url: str, dest_path: Path) -> None:
        """Download ``file_url`` to ``dest_path``. Parent dirs created as needed.

        Parameters
        ----------
        file_url : str
            URL of the remote file to download.
        dest_path : Path
            Local destination path; parent directories are created as needed.
        """
        ...

    def authenticate(self, profile: str | None = None) -> bool:
        """Refresh / acquire credentials. Return True on success.

        Parameters
        ----------
        profile : str or None, optional
            Named credential profile to use, by default ``None``.

        Returns
        -------
        bool
            ``True`` if credentials were successfully acquired or refreshed.
        """
        ...

    def close(self) -> None:
        """Release any held resources (tokens, connections)."""
        ...


__all__ = [
    "ArchiveError",
    "ArchiveAuthError",
    "ArchiveNotFoundError",
    "AssetCatalogPort",
    "FileStorePort",
    "ArchiveSourcePort",
]
