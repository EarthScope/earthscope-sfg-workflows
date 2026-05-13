"""Workspace — multi-session orchestration object."""

from __future__ import annotations

import os
from pathlib import Path
from typing import TYPE_CHECKING

from earthscope_sfg_workflows.data_mgmt.ports import (
    ArchiveSourcePort,
    AssetCatalogPort,
    FileStorePort,
)
from .session import StationSession

if TYPE_CHECKING:
    from earthscope_sfg_workflows.data_mgmt.model import AssetEntry, AssetKind


class Workspace:
    """Multi-session orchestration object.

    Owns the three infrastructure ports (catalog, files, archive) and manages
    a pool of :class:`StationSession` instances keyed by ``(network, station)``.
    Call :meth:`set_active` to make a session active; :attr:`session` returns it.
    """

    def __init__(
        self,
        root_dir: Path | str | None = None,
        *,
        s3_sync_bucket: str | None = None,
        catalog: AssetCatalogPort | None = None,
        files: FileStorePort | None = None,
        archive: ArchiveSourcePort | None = None,
    ) -> None:
        # Resolve root directory
        if root_dir is not None:
            self._root = Path(root_dir)
        elif os.environ.get("MAIN_DIRECTORY_GEOLAB"):
            self._root = Path(os.environ["MAIN_DIRECTORY_GEOLAB"])
        elif os.environ.get("MAIN_DIRECTORY"):
            self._root = Path(os.environ["MAIN_DIRECTORY"])
        else:
            self._root = Path(".")

        # Resolve remote bucket
        if s3_sync_bucket is not None:
            self._s3_sync_bucket: str | None = s3_sync_bucket
        else:
            raw = os.environ.get("S3_SYNC_BUCKET")
            if raw:
                self._s3_sync_bucket = raw if raw.startswith("s3://") else f"s3://{raw}"
            else:
                self._s3_sync_bucket = None

        # Build production ports when not injected
        if catalog is None or files is None or archive is None:
            _p = _build_ports(self._root)
            self._catalog: AssetCatalogPort = catalog or _p.catalog
            self._files: FileStorePort = files or _p.files
            self._archive: ArchiveSourcePort = archive or _p.archive
        else:
            self._catalog = catalog
            self._files = files
            self._archive = archive

        self._sessions: dict[tuple[str, str], StationSession] = {}
        self._active: StationSession | None = None

    # ------------------------------------------------------------------
    # Properties
    # ------------------------------------------------------------------

    @property
    def root(self) -> Path:
        return self._root

    @property
    def s3_sync_bucket(self) -> str | None:
        return self._s3_sync_bucket

    @property
    def catalog(self) -> AssetCatalogPort:
        return self._catalog

    @property
    def files(self) -> FileStorePort:
        return self._files

    @property
    def archive(self) -> ArchiveSourcePort:
        return self._archive

    @property
    def session(self) -> StationSession:
        """Active session. Raises ``RuntimeError`` if :meth:`set_active` has not been called."""
        if self._active is None:
            raise RuntimeError("No active session. Call set_active() first.")
        return self._active

    # ------------------------------------------------------------------
    # Session management
    # ------------------------------------------------------------------

    def set_active(
        self,
        network: str,
        station: str,
        campaign: str | None = None,
    ) -> StationSession:
        """Get-or-create the session for *(network, station)* and make it active."""
        sess = self._get_or_build_session(network, station)
        if campaign is not None and sess.campaign_name != campaign:
            sess.set_campaign(campaign)
        self._active = sess
        return sess

    def get_session(self, network: str, station: str) -> StationSession:
        """Return the session for *(network, station)* without changing :attr:`session`."""
        return self._get_or_build_session(network, station)

    def _get_or_build_session(self, network: str, station: str) -> StationSession:
        key = (network, station)
        if key not in self._sessions:
            from earthscope_sfg_workflows.data_mgmt.core import FileManager
            from earthscope_sfg_workflows.data_mgmt.model import DirectoryTree

            file_manager = FileManager(DirectoryTree(root=self._root), self._files)
            self._sessions[key] = StationSession(
                network,
                station,
                catalog=self._catalog,
                file_manager=file_manager,
                archive=self._archive,
                remote_root=self._s3_sync_bucket,
            )
        return self._sessions[key]

    # ------------------------------------------------------------------
    # Catalog queries
    # ------------------------------------------------------------------

    def list_networks(self) -> list[str]:
        return self._catalog.distinct_values("network")

    def list_stations(self, network: str) -> list[str]:
        return self._catalog.distinct_values("station", network=network)

    def list_campaigns(self, network: str, station: str) -> list[str]:
        return self._catalog.distinct_values("campaign", network=network, station=station)

    def query_assets(
        self,
        *,
        network: str | None = None,
        station: str | None = None,
        kind: "AssetKind | None" = None,
    ) -> "list[AssetEntry]":
        return self._catalog.assets_for(network=network, station=station, kind=kind)

    # ------------------------------------------------------------------
    # Test factory
    # ------------------------------------------------------------------

    @classmethod
    def for_test(
        cls,
        *,
        root: str | Path | None = None,
        catalog: AssetCatalogPort | None = None,
        files: FileStorePort | None = None,
        archive: ArchiveSourcePort | None = None,
    ) -> "Workspace":
        """Build a ``Workspace`` backed by in-memory adapters (no disk/network)."""
        from earthscope_sfg_workflows.data_mgmt.adapters.memory import (
            FakeArchive,
            InMemoryAssetStore,
            InMemoryFileStore,
        )

        return cls(
            root_dir=root or Path("/ws"),
            catalog=catalog or InMemoryAssetStore(),
            files=files or InMemoryFileStore(),
            archive=archive or FakeArchive(),
        )


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _build_ports(directory: Path | str):
    from dataclasses import dataclass

    from earthscope_sfg_workflows.data_mgmt.filestore.disk_filestore import LocalFileStore
    from earthscope_sfg_workflows.data_mgmt.archives.earthscope_archive import EarthScopeArchive
    from earthscope_sfg_workflows.data_mgmt.catalog.sql_asset_catalog import AssetCatalog

    @dataclass
    class _P:
        root: Path
        catalog: AssetCatalogPort
        files: FileStorePort
        archive: ArchiveSourcePort

    root = Path(directory)
    root.mkdir(parents=True, exist_ok=True)
    return _P(
        root=root,
        catalog=AssetCatalog.sqlite(root / "catalog.sqlite"),
        files=LocalFileStore(root=root),
        archive=EarthScopeArchive(),
    )


__all__ = ["Workspace", "StationSession"]
