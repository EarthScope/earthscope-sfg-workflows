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

import concurrent.futures
import json
import os
import threading
import warnings
from pathlib import Path

import boto3
from tqdm.auto import tqdm

from earthscope_sfg_tools.tiledb_integration import (
    TDBAcousticArray,
    TDBGNSSObsArray,
    TDBIMUPositionArray,
    TDBKinPositionArray,
    TDBShotDataArray,
)
from earthscope_sfg_tools.datamodels.metadata import Site

from earthscope_sfg_workflows.logging import ProcessLogger as logger
from earthscope_sfg_workflows.logging import change_all_logger_dirs

from ...config.file_config import (
    DEFAULT_FILE_TYPES_TO_DOWNLOAD,
    REMOTE_TYPE,
    AssetType,
)
from ...data_mgmt.adapters.earthscope_archive import EarthScopeArchive
from ...data_mgmt.adapters.local_fs import LocalFileStore
from ...data_mgmt.adapters.s3_fs import S3FileStore
from ...data_mgmt.adapters.sql import SqlAssetStore
from ...data_mgmt.core import FileTypeDetector
from ...data_mgmt.model import AssetEntry, AssetKind

from ..base import (
    WorkflowBase,
    validate_network_station,
    validate_network_station_campaign,
)
from ..workspace import Workspace


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

    catalog = SqlAssetStore.sqlite(catalog_db)
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

        Parameters
        ----------
        directory:
            Root path of the data tree. Auto-detected from
            ``$MAIN_DIRECTORY`` when omitted. **Legacy entry-point** — pass
            ``workspace=`` directly when possible.
        s3_sync_bucket:
            S3 bucket name/URI for :meth:`sync_from_s3`. Optional.
        workspace:
            Pre-constructed :class:`Workspace`. Preferred over ``directory``.
        """
        if workspace is None:
            if directory is None:
                directory = os.environ.get("MAIN_DIRECTORY", ".")
            workspace = _build_default_workspace(directory)

        super().__init__(workspace)

        self.s3_sync_bucket: str | None = s3_sync_bucket
        self._detector = FileTypeDetector()

        # TileDB array slots (lazily populated by ``_build_tileDB_arrays``).
        self.acoustic_tdb: TDBAcousticArray | None = None
        self.kin_position_tdb: TDBKinPositionArray | None = None
        self.imu_position_tdb: TDBIMUPositionArray | None = None
        self.shotdata_tdb: TDBShotDataArray | None = None
        self.shotdata_tdb_pre: TDBShotDataArray | None = None
        self.gnss_obs_tdb: TDBGNSSObsArray | None = None
        self.gnss_obs_secondary_tdb: TDBGNSSObsArray | None = None

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
            self.workspace.network_name, station_id  # type: ignore[arg-type]
        )
        self.workspace.try_load_site_metadata_from_disk()

        if self.mid_process_workflow:
            assert self.workspace.metadata.site is not None, (
                f"Site metadata file not found for station {station_id}; "
                "cannot proceed with mid-process workflow."
            )

    def set_campaign(self, campaign_id: str) -> None:
        if self.mid_process_workflow and self.workspace.metadata.site is not None:
            self.workspace.select_campaign_from_metadata(campaign_id)
        else:
            self.workspace.set_campaign(campaign_id)

        # Materialize campaign dirs and rotate logs.
        layout = self.workspace.layout.ensure_campaign()
        change_all_logger_dirs(layout.logs)
        os.environ["LOG_FILE_PATH"] = str(layout.logs)
        logger.loginfo(
            f"Built directory structure for "
            f"{self.workspace.network_name} {self.workspace.station_name} {campaign_id}"
        )

        if isinstance(self.workspace.root, Path):
            self._build_tileDB_arrays()

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
        logger.loginfo(
            f"Changed working station to {network_id} {station_id} {campaign_id}"
        )

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
    # TileDB helpers
    # ------------------------------------------------------------------

    def _ensure_tiledb_array(
        self,
        array_attr_name: str,
        array_class: type,
        uri_path: Path,
    ) -> None:
        current_array = getattr(self, array_attr_name, None)
        if current_array is None or uri_path != current_array.uri:
            setattr(self, array_attr_name, array_class(uri_path))

    def _build_tileDB_arrays(self) -> None:
        """Initialize and consolidate TileDB arrays for the active station."""
        logger.loginfo(f"Creating TileDB arrays for {self.workspace.station_name}")

        tiledb = self.workspace.layout.ensure_station()

        self._ensure_tiledb_array("acoustic_tdb", TDBAcousticArray, tiledb.acoustic)
        self._ensure_tiledb_array(
            "kin_position_tdb", TDBKinPositionArray, tiledb.kin_position
        )
        self._ensure_tiledb_array(
            "imu_position_tdb", TDBIMUPositionArray, tiledb.imu_position
        )
        self._ensure_tiledb_array("shotdata_tdb", TDBShotDataArray, tiledb.shotdata)
        self._ensure_tiledb_array(
            "shotdata_tdb_pre", TDBShotDataArray, tiledb.shotdata_pre
        )
        self._ensure_tiledb_array("gnss_obs_tdb", TDBGNSSObsArray, tiledb.gnss_obs)
        self._ensure_tiledb_array(
            "gnss_obs_secondary_tdb", TDBGNSSObsArray, tiledb.gnss_obs_secondary
        )
        logger.loginfo(
            f"Consolidating existing TileDB arrays for {self.workspace.station_name}"
        )

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
            logger.logerr(
                f"No files found in {directory_path}, ensure the directory is correct."
            )
            return
        report = self.workspace.ingest.local(Path(directory_path))
        logger.loginfo(
            f"Cataloged {report.cataloged} files (skipped {report.skipped}) "
            f"from {directory_path}"
        )
        for err in report.errors:
            logger.logerr(err)

    @validate_network_station_campaign
    def add_data_to_catalog(self, local_filepaths: list[Path]) -> None:
        """Catalog an explicit list of local files. Symlinks them to ``raw/`` if needed."""
        campaign = self.workspace.layout.ensure_campaign()

        added = 0
        scope = self.workspace.scope
        for file_path in local_filepaths:
            file_path = Path(file_path)
            if not file_path.exists():
                logger.logerr(f"File {file_path} does not exist")
                continue
            kind = self._detector.detect(file_path.name)
            if kind is None:
                continue
            # Preserve legacy symlink-into-raw behavior (the FileExistsError
            # path is the common case in practice).
            if file_path.parent != campaign.raw:
                symlinked_path = campaign.raw / file_path.name
                if symlinked_path != file_path:
                    try:
                        file_path.symlink_to(
                            symlinked_path, target_is_directory=False
                        )
                    except FileExistsError:
                        pass
            entry = AssetEntry(kind=kind, scope=scope, local_path=file_path)
            if self.workspace.assets.add_or_update(entry) is not None:
                added += 1

        logger.loginfo(
            f"Added {added} out of {len(local_filepaths)} files to the catalog"
        )

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
                    f"Remote type {remote_type} must be one of "
                    f"{REMOTE_TYPE.__members__.keys()}"
                ) from e

        scope = self.workspace.scope
        not_recognized = 0
        already_local = 0
        added = 0

        for url in remote_filepaths:
            kind = self._detector.detect(Path(url).name)
            if kind is None:
                logger.logdebug(f"File type not recognized for {url}")
                not_recognized += 1
                continue
            if self.workspace.assets.remote_file_exists_locally(kind, url):
                already_local += 1
                continue
            entry = AssetEntry(
                kind=kind,
                scope=scope,
                remote_path=url,
                remote_type=remote_type.value,
            )
            if self.workspace.assets.add_or_update(entry) is not None:
                added += 1

        logger.loginfo(f"{not_recognized} files not recognized and skipped")
        logger.loginfo(f"{already_local} files already exist in the catalog")
        logger.loginfo(
            f"Added {added} out of {len(remote_filepaths)} files to the catalog"
        )

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
        file_types = [ft.lower() if isinstance(ft, str) else ft for ft in file_types]
        file_types = list(set(file_types))

        for i, ft in enumerate(file_types):
            if isinstance(ft, str):
                try:
                    file_types[i] = AssetType(ft)
                except Exception as e:
                    raise ValueError(
                        f"File type {ft} must be one of {AssetType.__members__.keys()}"
                    ) from e

        for ft in file_types:
            kind = _to_asset_kind(ft)
            logger.loginfo(f"Processing download for file type: {kind.value}")
            assets = self.workspace.assets.all(kind)
            if not assets:
                logger.logerr(f"No matching data of type {kind.value} found in catalog")
                continue
            logger.loginfo(
                f"Found {len(assets)} files of type {kind.value} in the catalog"
            )

            if override:
                to_download = list(assets)
            else:
                to_download = [
                    a
                    for a in assets
                    if a.local_path is None or not Path(a.local_path).exists()
                ]

            if kind is AssetKind.RINEX2:
                if rinex_1Hz:
                    logger.loginfo("Filtering for 1Hz RINEX files")
                    to_download = [
                        a
                        for a in to_download
                        if a.remote_path and "1hz" in a.remote_path.lower()
                    ]
                else:
                    logger.loginfo("Filtering for higher rate RINEX files")
                    to_download = [
                        a
                        for a in to_download
                        if a.remote_path and "1hz" not in a.remote_path.lower()
                    ]

            if not to_download:
                logger.loginfo(f"No new {kind.value} files to download")
                continue
            logger.loginfo(f"{len(to_download)} {kind.value} files to download")

            s3_assets = [a for a in to_download if a.remote_type == REMOTE_TYPE.S3.value]
            http_assets = [
                a for a in to_download if a.remote_type == REMOTE_TYPE.HTTP.value
            ]

            if s3_assets:
                with threading.Lock():
                    boto3.client("s3")
                self._download_S3_files(s3_assets=s3_assets)

            if http_assets:
                self.download_HTTP_files(http_assets=http_assets, kind=kind)

    def _download_S3_files(self, s3_assets: list[AssetEntry]) -> None:
        """Download S3 assets in parallel and update the catalog with local paths."""
        campaign = self.workspace.layout.ensure_campaign()

        plan: list[dict] = []
        for asset in s3_assets:
            assert asset.remote_path is not None
            _path = Path(asset.remote_path)
            local_dir = (
                campaign.intermediate
                if asset.kind is AssetKind.RINEX2
                else campaign.raw
            )
            bucket = _path.root
            plan.append(
                {
                    "bucket": bucket,
                    "prefix": str(_path.relative_to(bucket)),
                    "local_dir": local_dir,
                }
            )

        with concurrent.futures.ThreadPoolExecutor(max_workers=10) as executor:
            local_results = executor.map(self._S3_download_file, plan)
            for local_path, asset in zip(local_results, s3_assets, strict=False):
                if local_path is not None and asset.id is not None:
                    self.workspace.assets.update_local_path(asset.id, local_path)

    def _S3_download_file(self, plan: dict) -> Path | None:
        """Download one S3 object using a fresh boto3 client."""
        bucket = plan["bucket"]
        prefix = plan["prefix"]
        local_dir: Path = plan["local_dir"]
        local_path = local_dir / Path(prefix).name
        try:
            client = boto3.client("s3")
            logger.logdebug(f"Downloading {prefix} to {local_path}")
            client.download_file(
                Bucket=bucket, Key=str(prefix), Filename=str(local_path)
            )
            logger.logdebug(f"Downloaded {prefix} to {local_path}")
            return local_path
        except Exception as e:
            logger.logerr(
                f"Error downloading {prefix} from {bucket}\n {e} \n HINT: $ aws sso login"
            )
            return None

    def download_HTTP_files(
        self,
        http_assets: list[AssetEntry],
        kind: AssetKind | None = None,
        # Legacy alias.
        file_type: AssetType | None = None,
    ) -> None:
        """Download HTTP assets sequentially and update the catalog."""
        if kind is None and file_type is not None:
            kind = _to_asset_kind(file_type)
        campaign = self.workspace.layout.ensure_campaign()
        label = kind.value if kind is not None else "files"

        for asset in tqdm(http_assets, desc=f"Downloading {label} files"):
            local_dir = (
                campaign.intermediate
                if asset.kind is AssetKind.RINEX2
                else campaign.raw
            )
            assert asset.remote_path is not None
            local_path = self._HTTP_download_file(
                remote_url=asset.remote_path, local_dir=local_dir
            )
            if local_path is not None and asset.id is not None:
                self.workspace.assets.update_local_path(asset.id, local_path)

    def _HTTP_download_file(self, remote_url: str, local_dir: Path) -> Path | None:
        try:
            local_path = local_dir / Path(remote_url).name
            self.workspace.archive.download_to_dir(remote_url, local_path.parent)
            if not local_path.exists():
                raise Exception
            logger.logdebug(f"Downloaded {remote_url} to {local_path}")
            return local_path
        except Exception as e:
            logger.logerr(
                f"Error downloading {remote_url} \n {e}\n "
                "HINT: Check authentication credentials"
            )
            return None

    # ------------------------------------------------------------------
    # Archive interaction
    # ------------------------------------------------------------------

    @validate_network_station_campaign
    def update_catalog_from_archive(self) -> None:
        """Discover canonical campaign URLs and catalog them."""
        logger.loginfo(
            f"Updating catalog with remote paths of available data for "
            f"{self.workspace.network_name} {self.workspace.station_name} "
            f"{self.workspace.campaign_name}"
        )
        report = self.workspace.ingest.discover_campaign()
        logger.loginfo(
            f"Cataloged {report.cataloged} remote URLs (skipped {report.skipped})"
        )
        for err in report.errors:
            logger.logerr(err)

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
        write_dest = (
            Path(self.workspace.root)
            / self.workspace.network_name
            / self.workspace.station_name
            / "site_metadata.json"
        )
        site: Site | None = None

        sources: list = [site_metadata, write_dest]
        if site_metadata is None:
            sources = sources[::-1]  # disk first when no explicit input.

        for source in sources:
            if isinstance(source, str):
                source = Path(source)

            if isinstance(source, Site):
                site = source
                with open(write_dest, "w") as f:
                    json.dump(site.model_dump(mode="json"), f, indent=4)
                logger.loginfo(
                    f"Using provided site metadata and wrote to {write_dest}"
                )
                break

            if isinstance(source, Path) and source.exists():
                try:
                    site = Site.from_json(source)
                    break
                except Exception as e:
                    msg = f"Error loading site metadata from {source}: {e}"
                    warnings.warn(msg)
                    logger.logerr(msg)

            if source is None:
                try:
                    site = self.workspace.archive.load_site_metadata(
                        network=self.workspace.network_name,
                        station=self.workspace.station_name,
                    )
                    with open(write_dest, "w") as f:
                        json.dump(site.model_dump(mode="json"), f, indent=4)
                    logger.loginfo(
                        f"Downloaded site metadata from the ES archive to {write_dest}"
                    )
                    break
                except Exception as e:
                    msg = f"Error loading site metadata from the ES archive: {e}"
                    warnings.warn(msg)
                    logger.logerr(msg)

        if site is None:
            msg = (
                f"Warning: No site metadata found for "
                f"{self.workspace.network_name} {self.workspace.station_name}. "
                "Some functionality may be limited."
            )
            warnings.warn(msg)
            logger.logwarn(msg)

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
            raise RuntimeError(
                "sync_from_s3 requires s3_sync_bucket to be configured."
            )
        if self.workspace.network_name is None or self.workspace.station_name is None:
            raise ValueError(
                "sync_from_s3 requires network and station to be set."
            )

        s3_root = self.s3_sync_bucket
        if not s3_root.startswith("s3://"):
            s3_root = f"s3://{s3_root}"

        s3_files = S3FileStore()
        local_files = self.workspace._files

        s3_station = (
            Path(s3_root)
            / self.workspace.network_name
            / self.workspace.station_name
        )
        if not s3_files.is_dir(s3_station):
            logger.logerr(f"S3 station path not found: {s3_station}")
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
                logger.logerr(f"Failed to download {info.path} to {local_dest}: {e}")
