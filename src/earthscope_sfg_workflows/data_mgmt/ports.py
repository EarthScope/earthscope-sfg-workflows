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

from pathlib import Path
from typing import Protocol, runtime_checkable

from .model import ArchiveFile, AssetEntry, AssetKind, CampaignScope, FileInfo


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
class AssetStore(Protocol):
    """Persistence port for the asset catalog.

    Guarantees:

    * Idempotent reads.
    * Transactional writes (commit-or-rollback per call).
    * Returns immutable :class:`AssetEntry` instances. Callers never see ORM
      rows, sessions, or connections.
    """

    def add(self, asset: AssetEntry) -> AssetEntry:
        """Insert ``asset`` and return a copy with ``id`` populated."""
        ...

    def update(self, asset: AssetEntry) -> bool:
        """Update an existing row by ``asset.id``. Return True if a row changed."""
        ...

    def by_id(self, asset_id: int) -> AssetEntry | None:
        """Look up an asset by primary key. Return ``None`` if missing."""
        ...

    def by_local_path(self, path: Path) -> list[AssetEntry]:
        """Return all assets with ``local_path == path``."""
        ...

    def assets_for(
        self,
        scope: CampaignScope,
        kind: AssetKind | None = None,
    ) -> list[AssetEntry]:
        """Query assets in ``scope``, optionally filtered by ``kind``."""
        ...

    def delete(
        self,
        scope: CampaignScope,
        kind: AssetKind | None = None,
    ) -> int:
        """Delete assets matching the scope (and optional kind). Return count."""
        ...

    def count_by_kind(self, scope: CampaignScope) -> dict[AssetKind, int]:
        """Aggregate count of assets per kind in ``scope``."""
        ...

    def close(self) -> None:
        """Release resources (DB connections, etc.)."""
        ...


# ---------------------------------------------------------------------------
# FileStore — filesystem / object-store
# ---------------------------------------------------------------------------


@runtime_checkable
class FileStore(Protocol):
    """Filesystem abstraction. Implementations: local, S3, in-memory."""

    def exists(self, path: Path) -> bool: ...
    def is_file(self, path: Path) -> bool: ...
    def is_dir(self, path: Path) -> bool: ...

    def list_files(
        self,
        directory: Path,
        recursive: bool = False,
    ) -> list[FileInfo]:
        """List files under ``directory``. Hidden ``._*`` entries are excluded."""
        ...

    def read_bytes(self, path: Path) -> bytes: ...
    def write_bytes(self, path: Path, data: bytes) -> None:
        """Write ``data`` to ``path``, creating parent dirs as needed."""
        ...

    def mkdir(self, path: Path, parents: bool = True) -> None:
        """Create ``path`` (and parents). Idempotent."""
        ...

    def remove(self, path: Path) -> bool:
        """Delete a file. Return True if a file was deleted."""
        ...

    def get_size(self, path: Path) -> int | None:
        """Size in bytes, or ``None`` if not a file."""
        ...

    def close(self) -> None: ...


# ---------------------------------------------------------------------------
# ArchiveSource — external archive
# ---------------------------------------------------------------------------


@runtime_checkable
class ArchiveSource(Protocol):
    """External archive discovery & download (EarthScope, S3, fake).

    All I/O is explicit. Callers manage authentication lifecycle via
    ``authenticate``.
    """

    def list_files(self, directory_url: str) -> list[ArchiveFile]:
        """Enumerate files under ``directory_url``."""
        ...

    def download_file(self, file_url: str, dest_path: Path) -> None:
        """Download ``file_url`` to ``dest_path``. Parent dirs created as needed."""
        ...

    def authenticate(self, profile: str | None = None) -> bool:
        """Refresh / acquire credentials. Return True on success."""
        ...

    def close(self) -> None: ...


__all__ = [
    "ArchiveError",
    "ArchiveAuthError",
    "ArchiveNotFoundError",
    "AssetStore",
    "FileStore",
    "ArchiveSource",
]
