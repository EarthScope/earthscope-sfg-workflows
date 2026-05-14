"""Workspace — multi-session orchestration object."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from earthscope_sfg_workflows.config.env_config import Environment
from earthscope_sfg_workflows.data_mgmt.core import FileManager
from earthscope_sfg_workflows.data_mgmt.model import DirectoryTree
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
        catalog: AssetCatalogPort | None = None,
        files: FileStorePort | None = None,
        archive: ArchiveSourcePort | None = None,
    ) -> None:
        # Ensure Environment singleton has loaded current env vars.
        Environment.load_working_environment()

        # Resolve root directory
        if root_dir is not None:
            self._root = Path(root_dir)
        elif Environment.main_directory_GEOLAB():
            self._root = Path(Environment.main_directory_GEOLAB())  # type: ignore[arg-type]
        else:
            self._root = Path(".")

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
        return Environment.s3_sync_bucket()

    @property
    def catalog(self) -> AssetCatalogPort:
        return self._catalog

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
        if campaign is not None and sess.scope.campaign != campaign:
            sess.set_campaign(campaign)
        self._active = sess
        return sess

    def get_session(self, network: str, station: str) -> StationSession:
        """Return the session for *(network, station)* without changing :attr:`session`."""
        return self._get_or_build_session(network, station)

    def _get_or_build_session(self, network: str, station: str) -> StationSession:
        key = (network, station)
        if key not in self._sessions:
            file_manager = FileManager(DirectoryTree(root=self._root), self._files)
            self._sessions[key] = StationSession(
                network,
                station,
                catalog=self._catalog,
                file_manager=file_manager,
                archive=self._archive,
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


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _build_ports(directory: Path | str):
    from dataclasses import dataclass

    from earthscope_sfg_workflows.data_mgmt.filestore.disk_filestore import FsspecFileStore
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
        files=FsspecFileStore(root=str(root)),
        archive=EarthScopeArchive(),
    )


__all__ = ["Workspace", "StationSession"]
