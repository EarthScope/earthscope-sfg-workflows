from typing import runtime_checkable, Protocol
from upath import UPath
from ..model import AssetEntry, AssetKind, SFGScope, FileInfo

@runtime_checkable
class AssetCatalogPort(Protocol):
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

    def by_local_path(self, path: UPath) -> list[AssetEntry]:
        """Return all assets with ``local_path == path``."""
        ...

    def assets_for(
        self,
        network: str | None = None,
        station: str | None = None,
        campaign: str | None = None,
        kind: AssetKind | None = None,
    ) -> list[AssetEntry]:
        """Query assets in ``scope``, optionally filtered by ``kind``."""
        ...

    def assets_to_process(
        self,
        network: str | None = None,
        station: str | None = None,
        campaign: str | None = None,
        kind: AssetKind | None = None,
        parent_kind: AssetKind | None = None,
        override: bool = False,
    ) -> list[AssetEntry]:
        """Return assets matching the scope and kind that need processing.
        If *parent_kind* is given, only return assets with no existing children of that kind.
        If *override* is True, ignore existing children and return all candidates.
        """
        ...
        
    def delete(
        self,
        network: str | None = None,
        station: str | None = None,
        campaign: str | None = None,
        kind: AssetKind | None = None,
    ) -> int:
        """Delete assets matching the scope (and optional kind). Return count."""
        ...

    def delete_by_id(self, asset_id: int) -> bool:
        """Delete one asset by id. Return True if deleted."""
        ...

    def count_by_kind(
        self,
        network: str | None = None,
        station: str | None = None,
        campaign: str | None = None,
    ) -> dict[AssetKind, int]:
        """Aggregate count of assets per kind in the specified scope."""
        ...

    # -- merge job tracking (carried over from legacy MergeJobs table) ----

    def add_merge_job(
        self,
        parent_type: str,
        child_type: str,
        parent_ids: list[int] | list[str],
    ) -> None:
        """Record a merge job (deterministic by sorted, dash-joined parent ids)."""
        ...

    def is_merge_complete(
        self,
        parent_type: str,
        child_type: str,
        parent_ids: list[int] | list[str],
    ) -> bool:
        """Check whether a previously recorded merge job exists for this signature."""
        ...

    def close(self) -> None:
        """Release resources (DB connections, etc.)."""
        ...
