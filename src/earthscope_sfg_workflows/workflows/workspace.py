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
    """Low-level multi-session container.

    Owns the three infrastructure ports (catalog, files, archive) and manages
    a pool of :class:`StationSession` instances keyed by ``(network, station)``.

    **Typical users should prefer** :class:`~earthscope_sfg_workflows.workflows.workflow_handler.WorkflowHandler`,
    which wraps ``Workspace`` with a higher-level API suited for notebooks and
    scripts.  Use ``Workspace`` directly when you need:

    * Fine-grained control over port construction (e.g. injecting test fakes).
    * Simultaneous access to multiple ``(network, station)`` sessions.
    * Building a custom orchestrator on top of the session pool.

    Call :meth:`set_active` to make a session active; :attr:`session` returns it.

    Attributes
    ----------
    root : Path
        Absolute root directory of this workspace.
    s3_sync_bucket : str or None
        S3 bucket name for remote sync, read from the ``S3_SYNC_BUCKET`` env var.
    catalog : AssetCatalogPort
        The asset catalog port backing all sessions in this workspace.
    session : StationSession
        The currently active session.

    Methods
    -------
    set_active(network, station, campaign)
        Get-or-create a session for ``(network, station)`` and make it active.
    get_session(network, station)
        Return a session without changing the active session.
    list_networks()
        Return all distinct network names in the catalog.
    list_stations(network)
        Return all distinct station names for a network in the catalog.
    list_campaigns(network, station)
        Return all distinct campaign names for a network/station pair.
    query_assets(network, station, kind)
        Return catalog entries matching optional filters.
    """

    def __init__(
        self,
        root_dir: Path | str | None = None,
        *,
        catalog: AssetCatalogPort | None = None,
        files: FileStorePort | None = None,
        archive: ArchiveSourcePort | None = None,
    ) -> None:
        """Initialise the workspace, resolving ports and the root directory.

        Parameters
        ----------
        root_dir : Path or str or None, optional
            Root directory for all local data.  Falls back to the
            ``GEOLAB_MAIN_DIRECTORY`` environment variable, then ``"."``.
        catalog : AssetCatalogPort or None, optional
            Asset catalog port to use.  Constructed automatically if omitted.
        files : FileStorePort or None, optional
            File store port to use.  Constructed automatically if omitted.
        archive : ArchiveSourcePort or None, optional
            Archive source port to use.  Constructed automatically if omitted.
        """
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
        """Absolute root directory of this workspace."""
        return self._root

    @property
    def s3_sync_bucket(self) -> str | None:
        """S3 bucket name for remote sync, read from the ``S3_SYNC_BUCKET`` env var."""
        return Environment.s3_sync_bucket()

    @property
    def catalog(self) -> AssetCatalogPort:
        """The asset catalog port backing all sessions in this workspace."""
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
        """Get-or-create the session for *(network, station)*, make it active, and return it.

        If a session for this pair already exists it is reused — TileDB arrays
        are only opened once.  If ``campaign`` is provided and differs from the
        session's current campaign, :meth:`StationSession.set_campaign` is called
        to materialise campaign directories and update scope.

        After this call, :attr:`session` returns the same session.

        Parameters
        ----------
        network : str
            Network identifier.
        station : str
            Station identifier within the network.
        campaign : str or None, optional
            If provided and different from the session's current campaign,
            :meth:`StationSession.set_campaign` is called automatically.

        Returns
        -------
        StationSession
            The active session for the given ``(network, station)`` pair.
        """
        sess = self._get_or_build_session(network, station)
        if campaign is not None and sess.scope.campaign != campaign:
            sess.set_campaign(campaign)
        self._active = sess
        return sess

    def get_session(self, network: str, station: str) -> StationSession:
        """Return the session for *(network, station)* without changing the active session.

        Useful when you need to inspect or configure a non-active session.
        Creates the session on first access.

        Parameters
        ----------
        network : str
            Network identifier.
        station : str
            Station identifier within the network.

        Returns
        -------
        StationSession
            Session for the given ``(network, station)`` pair.
        """
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
        """Return all distinct network names recorded in the catalog.

        Returns
        -------
        list of str
            Distinct network identifiers in the asset catalog.
        """
        return self._catalog.distinct_values("network")

    def list_stations(self, network: str) -> list[str]:
        """Return all distinct station names for *network* in the catalog.

        Parameters
        ----------
        network : str
            Network identifier to filter by.

        Returns
        -------
        list of str
            Distinct station identifiers in the asset catalog for the given network.
        """
        return self._catalog.distinct_values("station", network=network)

    def list_campaigns(self, network: str, station: str) -> list[str]:
        """Return all distinct campaign names for *(network, station)* in the catalog.

        Parameters
        ----------
        network : str
            Network identifier.
        station : str
            Station identifier within the network.

        Returns
        -------
        list of str
            Distinct campaign identifiers in the asset catalog for the given
            network/station pair.
        """
        return self._catalog.distinct_values("campaign", network=network, station=station)

    def query_assets(
        self,
        *,
        network: str | None = None,
        station: str | None = None,
        kind: "AssetKind | None" = None,
    ) -> "list[AssetEntry]":
        """Return catalog entries matching the optional *network*, *station*, and *kind* filters.

        Parameters
        ----------
        network : str or None, optional
            Filter by network identifier.
        station : str or None, optional
            Filter by station identifier.
        kind : AssetKind or None, optional
            Filter by asset kind (e.g. raw, processed).

        Returns
        -------
        list of AssetEntry
            Catalog entries that match all provided filters.
        """
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
