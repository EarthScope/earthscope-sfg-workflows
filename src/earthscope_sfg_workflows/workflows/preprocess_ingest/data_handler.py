"""DataHandler — preprocess ingestion / catalog / download workflow class.
Migrated to :class:`WorkflowBase` + :class:`Workspace` (RFC-A Phase 4).

The constructor still accepts the legacy ``directory`` / ``s3_sync_bucket``
arguments; they are used to build a :class:`Workspace` internally. This
keeps :class:`WorkflowHandler` (composes ``DataHandler``) working until it
is migrated in the next session, after which the legacy entry-point will
be removed.

A small set of legacy attribute aliases (``current_network_name`` /
``current_station_name`` / ``current_station_metadata``) are exposed as
backwards-compat properties for the same reason. These are migration
bridges, not architectural shims; they are scheduled for removal in the
next session.
"""

from __future__ import annotations

import os
import warnings
from pathlib import Path

from earthscope_sfg_tools.datamodels.metadata import Site

from earthscope_sfg_workflows.logging import ProcessLogger as logger
from earthscope_sfg_workflows.logging import change_all_logger_dirs

from ...config.file_config import (
    DEFAULT_FILE_TYPES_TO_DOWNLOAD,
    REMOTE_TYPE,
    AssetType,
)
from ...data_mgmt.archives.earthscope_archive import EarthScopeArchive
from ...data_mgmt.filestore.disk_filestore import LocalFileStore
from ...data_mgmt.filestore.disk_filestore import S3FileStore
from ...data_mgmt.catalog.sql_asset_catalog import AssetCatalog
from ...data_mgmt.model import AssetEntry, AssetKind

from ..base import (
    WorkflowBase,
    validate_network_station,
    validate_network_station_campaign,
)
from ..workspace import TileDBRegistry, Workspace


def _to_asset_kind(t: AssetType | str) -> AssetKind:
    """Translate the user-facing :class:`AssetType` enum/str to :class:`AssetKind`."""
    if isinstance(t, AssetType):
        return AssetKind(t.value)
    return AssetKind(str(t).lower())


def _build_default_workspace(directory: Path | str) -> Workspace:
    """Construct a production Workspace rooted at ``directory``.
    Picks the file store based on the directory scheme: ``s3://`` URIs use
    :class:`S3FileStore`; everything else uses :class:`LocalFileStore`.
    """
    is_s3 = str(directory).startswith("s3://")
    if is_s3:
        files = S3FileStore()
        # Catalog DB always local even when data root is S3.
        catalog_db = Path(os.environ.get("MAIN_DIRECTORY", ".")) / "catalog.sqlite"
        root: Path | str = directory
    else:
        files = LocalFileStore()
        root = Path(directory)
        root.mkdir(parents=True, exist_ok=True)
        catalog_db = root / "catalog.sqlite"

    catalog = AssetCatalog.sqlite(catalog_db)
    archive = EarthScopeArchive()
    return Workspace(root_dir=root, catalog=catalog, files=files, archive=archive)


class DataHandler(WorkflowBase):
    """Preprocess ingestion + catalog + download workflow class.
    Composes a :class:`Workspace` and reaches the data layer only via
    ``self.workspace.{layout,assets,ingest,metadata}``.
    """

    mid_process_workflow: bool = False

    def __init__(
        self,
        directory: Path | str | None = None,
        s3_sync_bucket: str | None = None,
        *,
        workspace: Workspace | None = None,
    ) -> None:
        """Initialize the handler.
        Args:
        """
        if workspace is None:
            if directory is None:
                directory = os.environ.get("MAIN_DIRECTORY", ".")
            workspace = _build_default_workspace(directory)

        super().__init__(workspace)

        self.s3_sync_bucket: str | None = s3_sync_bucket

        # TileDB array registry (populated by set_campaign via workspace.build_tiledb).
        self.tiledb: TileDBRegistry | None = None

        # Materialize the workspace root.
        self.workspace.bootstrap()

    # ------------------------------------------------------------------
    # Scope mutators (forward to workspace + side effects)
    # ------------------------------------------------------------------

    def set_network(self, network_id: str) -> None:
        self.workspace.set_network(network_id)
        self.workspace.bootstrap()
        self.workspace.ensure_network_dir(network_id)

    def set_station(self, station_id: str) -> None:
        self.workspace.set_station(station_id)
        # Materialize station dir; load site metadata if present on disk.
        self.workspace.ensure_station_dir(
            self.workspace.network_name,
            station_id,  # type: ignore[arg-type]
        )
        self.workspace.try_load_site_metadata_from_disk()

        if self.mid_process_workflow:
            assert self.workspace.metadata.site is not None, (
                f"Site metadata file not found for station {station_id}; "
                "cannot proceed with mid-process workflow."
            )

    def set_campaign(self, campaign_id: str) -> None:
        from_metadata = self.mid_process_workflow and self.workspace.metadata.site is not None
        layout = self.workspace.activate_campaign(campaign_id, from_metadata=from_metadata)
        change_all_logger_dirs(layout.logs)
        os.environ["LOG_FILE_PATH"] = str(layout.logs)
        logger.info(
            f"Built directory structure for "
            f"{self.workspace.network_name} {self.workspace.station_name} {campaign_id}"
        )
        if isinstance(self.workspace.root, Path):
            self.tiledb = self.workspace.build_tiledb()

    def set_network_station_campaign(
        self,
        network_id: str,
        station_id: str | None = None,
        campaign_id: str | None = None,
    ) -> None:
        if network_id != self.workspace.network_name:
            self.set_network(network_id)
        if station_id is not None and station_id != self.workspace.station_name:
            self.set_station(station_id)
        if campaign_id is not None and campaign_id != self.workspace.campaign_name:
            self.set_campaign(campaign_id)
        logger.info(f"Changed working station to {network_id} {station_id} {campaign_id}")

    def set_network_station_campaign_with_metadata(
        self,
        network_id: str,
        station_id: str,
        campaign_id: str,
        site_metadata: Site | Path | str | None = None,
    ) -> None:
        """Set context, then load specific site metadata (if provided / discoverable)."""
        self.set_network_station_campaign(network_id, station_id, campaign_id)

        if site_metadata is not None or self.workspace.metadata.site is None:
            site = self.get_site_metadata(site_metadata=site_metadata)
            if site is not None:
                self.workspace.load_site_metadata(site)

    # ------------------------------------------------------------------
    # Catalog operations
    # ------------------------------------------------------------------

    @validate_network_station_campaign
    def get_dtype_counts(self) -> dict[str, int]:
        """Return counts of cataloged data types for the active scope."""
        return self.workspace.assets.dtype_counts()

    @validate_network_station_campaign
    def discover_data_and_add_files(self, directory_path: Path) -> None:
        """Scan ``directory_path`` and catalog every recognized file."""
        if not Path(directory_path).is_dir():
            logger.error(f"No files found in {directory_path}, ensure the directory is correct.")
            return
        report = self.workspace.ingest.local(Path(directory_path))
        logger.info(
            f"Cataloged {report.cataloged} files (skipped {report.skipped}) from {directory_path}"
        )
        for err in report.errors:
            logger.error(err)

    @validate_network_station_campaign
    def add_data_to_catalog(self, local_filepaths: list[Path]) -> None:
        """Catalog an explicit list of local files. Symlinks them to ``raw/`` if needed."""
        campaign = self.workspace.layout.ensure_campaign()

        added = 0
        scope = self.workspace.scope
        for file_path in local_filepaths:
            file_path = Path(file_path)
            if not file_path.exists():
                logger.error(f"File {file_path} does not exist")
                continue
            kind = self.workspace._detector.detect(file_path.name)
            if kind is None:
                continue
            # Preserve legacy symlink-into-raw behavior (the FileExistsError
            # path is the common case in practice).
            if file_path.parent != campaign.raw:
                symlinked_path = campaign.raw / file_path.name
                if symlinked_path != file_path:
                    try:
                        file_path.symlink_to(symlinked_path, target_is_directory=False)
                    except FileExistsError:
                        pass
            entry = AssetEntry(
                kind=kind,
                network=scope.network.name if scope.network else None,
                station=scope.station.name if scope.station else None,
                campaign=scope.campaign.name if scope.campaign else None,
                local_path=file_path,
            )
            if self.workspace.assets.add_or_update(entry) is not None:
                added += 1

        logger.info(f"Added {added} out of {len(local_filepaths)} files to the catalog")

    @validate_network_station_campaign
    def add_data_remote(
        self,
        remote_filepaths: list[str],
        remote_type: REMOTE_TYPE | str = REMOTE_TYPE.HTTP,
    ) -> None:
        """Catalog remote URLs after type detection. Skips already-downloaded entries."""
        if isinstance(remote_type, str):
            try:
                remote_type = REMOTE_TYPE(remote_type)
            except Exception as e:
                raise ValueError(
                    f"Remote type {remote_type} must be one of {REMOTE_TYPE.__members__.keys()}"
                ) from e

        scope = self.workspace.scope
        not_recognized = 0
        already_local = 0
        added = 0

        for url in remote_filepaths:
            kind = self.workspace._detector.detect(Path(url).name)
            if kind is None:
                logger.debug(f"File type not recognized for {url}")
                not_recognized += 1
                continue
            if self.workspace.assets.remote_file_exists_locally(kind, url):
                already_local += 1
                continue
            entry = AssetEntry(
                kind=kind,
                network=scope.network.name if scope.network else None,
                station=scope.station.name if scope.station else None,
                campaign=scope.campaign.name if scope.campaign else None,
                remote_path=url,
                remote_type=remote_type.value,
            )
            if self.workspace.assets.add_or_update(entry) is not None:
                added += 1

        logger.info(f"{not_recognized} files not recognized and skipped")
        logger.info(f"{already_local} files already exist in the catalog")
        logger.info(f"Added {added} out of {len(remote_filepaths)} files to the catalog")

    # ------------------------------------------------------------------
    # Download
    # ------------------------------------------------------------------

    def download_data(
        self,
        file_types: list[AssetType] | list[str] | str = DEFAULT_FILE_TYPES_TO_DOWNLOAD,
        override: bool = False,
        rinex_1Hz: bool = False,
    ) -> None:
        """Download cataloged remote files of the given types."""
        if not isinstance(file_types, list):
            file_types = [file_types]

        kinds: list[AssetKind] = []
        seen: set[AssetKind] = set()
        for ft in file_types:
            if isinstance(ft, str):
                try:
                    ft = AssetType(ft.lower())
                except Exception as e:
                    raise ValueError(
                        f"File type {ft!r} must be one of {AssetType.__members__.keys()}"
                    ) from e
            kind = _to_asset_kind(ft)
            if kind not in seen:
                seen.add(kind)
                kinds.append(kind)

        report = self.workspace.ingest.download(kinds=kinds, override=override, rinex_1hz=rinex_1Hz)
        logger.info(f"Downloaded {report.downloaded} files (skipped {report.skipped})")
        for err in report.errors:
            logger.error(err)

    # ------------------------------------------------------------------
    # Archive interaction
    # ------------------------------------------------------------------

    @validate_network_station_campaign
    def update_catalog_from_archive(self) -> None:
        """Discover canonical campaign URLs and catalog them."""
        logger.info(
            f"Updating catalog with remote paths of available data for "
            f"{self.workspace.network_name} {self.workspace.station_name} "
            f"{self.workspace.campaign_name}"
        )
        report = self.workspace.ingest.discover_campaign()
        logger.info(f"Cataloged {report.cataloged} remote URLs (skipped {report.skipped})")
        for err in report.errors:
            logger.error(err)

    @validate_network_station
    def get_site_metadata(
        self,
        site_metadata: Site | Path | str | None = None,
    ) -> Site | None:
        """Load or persist site metadata for the active station.
        Order of precedence:

        1. Explicit ``site_metadata`` argument (Site, Path, or path string).
        2. Existing ``site_metadata.json`` file in the station directory.
        3. EarthScope archive (network/station lookup).
        """
        site = self.workspace.load_or_fetch_site_metadata(explicit=site_metadata)
        if site is not None:
            logger.info(
                f"Site metadata loaded for "
                f"{self.workspace.network_name} {self.workspace.station_name}"
            )
        else:
            msg = (
                f"No site metadata found for "
                f"{self.workspace.network_name} {self.workspace.station_name}. "
                "Some functionality may be limited."
            )
            warnings.warn(msg)
            logger.warning(msg)
        return site

    # ------------------------------------------------------------------
    # S3 sync
    # ------------------------------------------------------------------

    def sync_from_s3(self, overwrite: bool = False) -> None:
        """Mirror seafloor-geodesy data from a remote S3 prefix into the local workspace.
        Copies the active station's directory tree (campaigns + their files
        + the TileDB arrays) from ``self.s3_sync_bucket`` into the local
        workspace root.
        """
        if self.s3_sync_bucket is None:
            raise RuntimeError("sync_from_s3 requires s3_sync_bucket to be configured.")
        if self.workspace.network_name is None or self.workspace.station_name is None:
            raise ValueError("sync_from_s3 requires network and station to be set.")

        s3_root = self.s3_sync_bucket
        if not s3_root.startswith("s3://"):
            s3_root = f"s3://{s3_root}"

        s3_files = S3FileStore()
        local_files = self.workspace._files

        s3_station = Path(s3_root) / self.workspace.network_name / self.workspace.station_name
        if not s3_files.is_dir(s3_station):
            logger.error(f"S3 station path not found: {s3_station}")
            return

        for info in s3_files.list_files(s3_station, recursive=True):
            if not info.is_file:
                continue
            relative = Path(str(info.path)).relative_to(s3_station)
            local_dest = (
                self.workspace.root
                / self.workspace.network_name
                / self.workspace.station_name
                / relative
            )
            if local_dest.exists() and not overwrite:
                continue
            try:
                local_dest.parent.mkdir(parents=True, exist_ok=True)
                local_files.write_bytes(local_dest, s3_files.read_bytes(info.path))
            except Exception as e:
                logger.error(f"Failed to download {info.path} to {local_dest}: {e}")
