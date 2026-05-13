"""CampaignSession — scoped, eager-initialising unit of work.

Network and station are **fixed** at construction and cannot be mutated;
they are the stable identity that anchors TileDB arrays (which are
station-scoped and built once in ``__init__``).  Campaign and survey are
mutable slots that can be set at any time via :meth:`set_campaign` and
:meth:`set_survey`.

Methods that require campaign or survey are guarded by the
:func:`_require_campaign` and :func:`_require_survey` decorators defined in
this module.  Workflow-layer methods (on :class:`WorkflowBase` subclasses)
are guarded by the decorators in :mod:`earthscope_sfg_workflows.workflows.base`.
"""

from __future__ import annotations

import json
import warnings
from contextlib import AbstractContextManager
from dataclasses import dataclass
from functools import wraps
from pathlib import Path
from typing import TYPE_CHECKING, Callable, Literal, Optional, TypeVar

from pride_ppp import PrideCLIConfig
from upath import UPath

from earthscope_sfg_workflows.data_mgmt.archives.earthscope_archive import EarthScopeArchive
from earthscope_sfg_workflows.data_mgmt.core import (
    FileManager,
    Ingestor,
    LayoutInspector,
)
from earthscope_sfg_workflows.data_mgmt.ports import ArchiveNotFoundError
from earthscope_sfg_workflows.data_mgmt.model import (
    AssetKind,
    CampaignLayout,
    GARPOSLayout,
    IngestReport,
    NetworkLayout,
    SFGScope,
    DirectoryTree,
    StationLayout,
    SurveyLayout,
    SurveyLayout,
    TileDBLayout,
)

from earthscope_sfg_tools.datamodels.metadata import Campaign, Site, Survey

_Site = Site  # alias kept for the .from_json() classmethod call below
from earthscope_sfg_tools.tiledb_integration import (
    TDBIMUPositionArray,
    TDBKinPositionArray,
    TDBShotDataArray,
)

from earthscope_sfg_workflows.data_mgmt.filestore.disk_filestore import FsspecFileStore
from earthscope_sfg_workflows.data_mgmt.model import DirectoryTree
from earthscope_sfg_workflows.data_mgmt.ports import (
    ArchiveSourcePort,
    AssetCatalogPort,
    FileStorePort,
)
from earthscope_sfg_workflows.data_mgmt.ports import ArchiveNotFoundError
from earthscope_sfg_workflows.logging import GarposLogger as logger
from earthscope_sfg_workflows.utils.model_update import validate_and_merge_config
from earthscope_sfg_workflows.workflows.pipelines.config import DFOP00Config, DFOP00Config, NovatelConfig, PositionUpdateConfig, QCPipelineConfig, SV3PipelineConfig,RinexConfig
from earthscope_sfg_workflows.workflows.pipelines.sv3_pipeline import SV3_JOBS, SV3Pipeline
from earthscope_sfg_workflows.workflows.pipelines.qc_pipeline import QC_JOBS, QCPipeline


if TYPE_CHECKING:  # pragma: no cover
    from earthscope_sfg_tools.tiledb_integration import (
        TDBAcousticArray,
        TDBGNSSObsArray,
    )

_F = TypeVar("_F", bound=Callable)

_Config = SV3PipelineConfig | PrideCLIConfig | NovatelConfig | RinexConfig | DFOP00Config | PositionUpdateConfig
# ---------------------------------------------------------------------------
# Method-level scope enforcement decorators (for CampaignSession internals)
# ---------------------------------------------------------------------------


def _require_campaign(method: _F) -> _F:
    """Decorator: raise if the session's campaign slot is not set."""

    @wraps(method)
    def wrapper(self: "StationSession", *args, **kwargs):
        if self.campaign is None:
            raise ValueError(
                f"{method.__name__} requires a campaign to be set; "
                "call set_campaign() first"
            )
        return method(self, *args, **kwargs)

    return wrapper  # type: ignore[return-value]


def _require_survey(method: _F) -> _F:
    """Decorator: raise if the session's survey slot is not set."""

    @wraps(method)
    def wrapper(self: "StationSession", *args, **kwargs):
        if self.survey is None:
            raise ValueError(
                f"{method.__name__} requires a survey to be set; "
                "call set_survey() first"
            )
        return method(self, *args, **kwargs)

    return wrapper  # type: ignore[return-value]

def _require_site_metadata(method: _F) -> _F:
    """Decorator: raise if the session's station metadata is not available."""

    @wraps(method)
    def wrapper(self: "StationSession", *args, **kwargs):
        if self.station is None or self.station.metadata is None:
            raise ValueError(
                f"{method.__name__} requires station metadata to be available; "
                "ensure site metadata is loaded successfully"
            )
        return method(self, *args, **kwargs)

    return wrapper  # type: ignore[return-value]

# ---------------------------------------------------------------------------
# TileDB handle registry
# ---------------------------------------------------------------------------


@dataclass
class TileDBRegistry:
    acoustic: "TDBAcousticArray"
    kin_position: "TDBKinPositionArray"
    imu_position: "TDBIMUPositionArray"
    shotdata: "TDBShotDataArray"
    shotdata_pre: "TDBShotDataArray"
    gnss_obs: "TDBGNSSObsArray"
    gnss_obs_secondary: "TDBGNSSObsArray"


@dataclass
class ScopeRegistry:
    name: str | None = None
    layout: StationLayout | CampaignLayout | SurveyLayout | None = None
    metadata: Site | Campaign | Survey | None = None

_SCOPE_LEVELS = ["network", "station", "campaign", "survey"]

# ---------------------------------------------------------------------------
# CampaignSession
# ---------------------------------------------------------------------------


class StationSession:
    """Scoped unit of work anchored to a fixed network/station pair.

    Network and station are immutable after construction.  TileDB arrays are
    station-scoped and are materialised eagerly in ``__init__`` so that
    switching campaigns never triggers a redundant rebuild.

    Campaign and survey are mutable: call :meth:`set_campaign` (which
    materialises campaign directories and resolves campaign metadata) and
    :meth:`set_survey`.  Methods that require these slots are decorated with
    :func:`_require_campaign` / :func:`_require_survey` so that callers
    receive a clear ``ValueError`` rather than an opaque attribute error.
    """

    def __init__(
        self,
        network: str,
        station: str,
        *,
        catalog: AssetCatalogPort,
        file_manager: FileManager,
        archive: ArchiveSourcePort = EarthScopeArchive(),
        remote_root: str | None = None,
    ) -> None:

        self._catalog = catalog
        self._file_manager = file_manager
        self._archive = archive

        if remote_root is not None:
            self.configure_remote(remote_root)

        network_layout: NetworkLayout = self._file_manager.ensure_network(network)
        self.network = ScopeRegistry(name=network, layout=network_layout)

        station_layout: StationLayout = self._file_manager.ensure_station(network=network, station=station)
        station_metadata = self._fetch_site_metadata(network, station)
        self.station = ScopeRegistry(name=station, layout=station_layout, metadata=station_metadata)

        # Mutable campaign/survey slots — None until explicitly set.
        self.campaign: ScopeRegistry = ScopeRegistry()  # dummy initial value; guarded by _require_campaign
        self.survey: ScopeRegistry = ScopeRegistry()  # dummy initial value; guarded by _require_survey

        self._ingestor = Ingestor(
            catalog=self._catalog,
            file_manager=self._file_manager,
            archive=self._archive,
        )

        self._sv3_pipeline = None  # lazy init in get_pipeline_sv3
        self._qc_pipeline = None   # lazy init in get_pipeline_qc

    # ------------------------------------------------------------------
    # Remote configuration
    # ------------------------------------------------------------------

    def configure_remote(self, remote_root: str | None) -> None:
        """Point the session's file manager at a remote S3 root for push/pull operations.

        Pass ``None`` to clear the remote configuration.  Calling this with the
        same bucket multiple times is idempotent.
        """
        if remote_root is None:
            self._file_manager._remote_tree = None
            self._file_manager._remote_backend = None
            return
        bucket = remote_root if remote_root.startswith("s3://") else f"s3://{remote_root}"
        self._file_manager._remote_tree = DirectoryTree(root=UPath(bucket))
        self._file_manager._remote_backend = FsspecFileStore(root=bucket)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _reset_lower(self, level: str) -> None:
        """Reset mutable scope levels below *level* (e.g. if campaign changes, clear survey)."""
        if level not in _SCOPE_LEVELS:
            raise ValueError(f"Invalid scope level {level!r}; must be one of {_SCOPE_LEVELS}")
        level_index = _SCOPE_LEVELS.index(level)
        for lower_level in _SCOPE_LEVELS[level_index + 1 :]:
            setattr(self, lower_level, ScopeRegistry(name="", layout=None, metadata=None))

    @property
    def scope(self) -> SFGScope:
        """Live view of the current network/station/campaign/survey context.

        Raises ``ValueError`` if campaign has not been set yet.
        """
        if not self.campaign.name:
            raise ValueError(
                "scope requires a campaign to be set; call set_campaign() first"
            )
        return SFGScope(
            network=self.network.name,
            station=self.station.name,
            campaign=self.campaign.name,
            survey=self.survey.name if self.survey.name else None,
        )

    def _fetch_site_metadata(self, network: str, station: str) -> "Site | None":
        """Load site metadata: disk first, then EarthScope archive. Persist on fetch."""

        write_dest = (
            self._file_manager.directory_tree.station_dir(network=network, station=station) / "site_metadata.json"
        )

        if write_dest.exists():
            try:
                return _Site.from_json(write_dest)
            except Exception as exc:
                warnings.warn(f"Error loading site metadata from disk: {exc}")

        try:
            site = self._archive.load_site_metadata(
                network=network, station=station
            )
            with open(write_dest, "w") as f:
                json.dump(site.model_dump(mode="json"), f, indent=4)
            return site
        except Exception as exc:

            if not isinstance(exc, ArchiveNotFoundError):
                warnings.warn(f"Error loading site metadata from EarthScope archive: {exc}")

        return None

    def _resolve_campaign_in_site(self, campaign_id: str) -> "Campaign | None":
        """Find the campaign object in site metadata matching ``campaign_id``."""
        if self.station is None or self.station.metadata is None:
            return None
        try:
            for c in self.station.metadata.campaigns:
                if c.name == campaign_id:
                    return c
        except AttributeError:
            pass
        return None

    def _resolve_survey_in_campaign(self, survey_id: str) -> "Survey | None":
        """Find the survey object in campaign metadata matching ``survey_id``."""
        if self.campaign is None or self.campaign.metadata is None:
            return None
        try:
            for s in self.campaign.metadata.surveys:
                if s.id == survey_id:
                    return s
        except AttributeError:
            pass
        return None

    @_require_campaign
    def ingest_local(self,source_dir:UPath) -> None:
        """Ingest files from a local directory into the session's asset catalog."""
        report: IngestReport = self._ingestor.ingest_local(self.scope, source_dir)
        logger.info(f"Ingested {report.cataloged} assets from {source_dir} with {report.errors} errors")

    @_require_campaign
    def ingest_qcpin_tarballs(
        self,
        tarball_dir: Path | None = None,
        *,
        override: bool = False,
    ) -> None:
        """Extract ``.pin`` files from ``.tar.gz`` tarballs and catalog them as QCPIN assets.

        Defaults to the campaign's ``qc/`` directory when *tarball_dir* is not given.
        """
        if tarball_dir is None:
            if self.campaign.layout is None:
                raise ValueError("ingest_qcpin_tarballs requires a campaign with a layout")
            tarball_dir = Path(self.campaign.layout.qc)
        report: IngestReport = self._ingestor.ingest_qcpin_tarballs(
            self.scope, tarball_dir, override=override
        )
        logger.info(
            f"Ingested {report.cataloged} QCPIN assets from tarballs in {tarball_dir}"
            f" ({report.skipped} skipped, {len(report.errors)} errors)"
        )

    @_require_campaign
    def discover_remote(self) -> None:
        """Discover assets in the archive for the current scope and add to catalog."""
        report: IngestReport = self._ingestor.discover_archive(self.scope)
        logger.info(f"Discovered {report.cataloged} assets in archive for scope {self.scope} with {report.errors} errors")

    @_require_campaign
    def download_remote(
        self,
        kinds: list[AssetKind] | None = None,
        override: bool = False,
        rinex_1hz: bool = False,
    ) -> None:
        """Download assets from the archive for the current scope to local storage.

        Args:
            kinds: Restrict downloads to these asset kinds. ``None`` downloads all.
            override: Re-download even when a local file already exists.
            rinex_1hz: When ``True``, keep only 1-Hz RINEX files.
        """
        dest_dir = self.campaign.layout.raw
        report: IngestReport = self._ingestor.download(
            self.scope, kinds=kinds, dest_dir=dest_dir, override=override, rinex_1hz=rinex_1hz
        )
        logger.info(f"Downloaded {report.downloaded} files (skipped {report.skipped})")

    # ------------------------------------------------------------------
    # Mutation
    # ------------------------------------------------------------------

    def set_campaign(self, campaign_id: str) -> CampaignLayout:
        """Set the active campaign, clear survey, materialise campaign dirs."""
        self._reset_lower("campaign")  # clear mutable levels below campaign (i.e. survey)
        campaign_meta:Optional[Campaign] = self._resolve_campaign_in_site(campaign_id)
        layout:CampaignLayout = self._file_manager.ensure_campaign(
            network=self.network.name, station=self.station.name, campaign=campaign_id
        )
        self.campaign = ScopeRegistry(
            name=campaign_id, layout=layout,metadata=campaign_meta
        )
        return layout

    @_require_campaign
    def set_survey(self, survey_id: str) -> None:
        """Set the active survey and materialise its directory."""
        if self.campaign is None:
            raise ValueError(
                "A campaign must be set before setting a survey; "
                "call set_campaign() first"
            )
        layout: Optional[SurveyLayout] =  self._file_manager.ensure_survey(
            network=self.network.name,
            station=self.station.name,
            campaign=self.campaign.name,
            survey=survey_id,
        )
        survey_metadata: Optional[Survey] = self._resolve_survey_in_campaign(survey_id)
        self.survey = ScopeRegistry(
            name=survey_id, layout=layout, metadata=survey_metadata
        )

    # ------------------------------------------------------------------
    # Convenience accessors (scope names and metadata)
    # ------------------------------------------------------------------

    @property
    def network_name(self) -> str | None:
        return self.network.name

    @property
    def station_name(self) -> str | None:
        return self.station.name

    @property
    def campaign_name(self) -> str | None:
        return self.campaign.name

    @property
    def survey_name(self) -> str | None:
        return self.survey.name or None

    @property
    def site(self) -> "Site | None":
        return self.station.metadata  # type: ignore[return-value]

    @property
    def campaign_meta(self) -> "Campaign | None":
        return self.campaign.metadata  # type: ignore[return-value]

    @property
    def root(self) -> Path:
        return Path(self._file_manager.directory_tree.root)

    @property
    def survey_dir(self) -> Path:
        """Root directory of the active survey. Requires campaign and survey to be set."""
        if self.survey.layout is None:
            raise ValueError("survey_dir requires a survey to be set; call set_survey() first")
        return Path(self.survey.layout.root)  # type: ignore[arg-type]

    @property
    def survey_metadata_file(self) -> Path:
        """Metadata file path for the active survey."""
        if self.survey.layout is None:
            raise ValueError("survey_metadata_file requires a survey to be set; call set_survey() first")
        return Path(self.survey.layout.metadata_file)  # type: ignore[arg-type]

    # ------------------------------------------------------------------
    # Convenience layout helpers
    # ------------------------------------------------------------------

    def tiledb_layout(self) -> TileDBLayout:
        """Return the TileDB layout for the current station."""
        return self._file_manager.directory_tree.tiledb(
            network=self.network.name, station=self.station.name
        )

    def ensure_campaign(self) -> CampaignLayout:
        """Materialise campaign directories and return the layout."""
        if self.campaign.name is None:
            raise ValueError("ensure_campaign requires a campaign to be set; call set_campaign() first")
        return self._file_manager.ensure_campaign(
            network=self.network.name,
            station=self.station.name,
            campaign=self.campaign.name,
        )

    # ------------------------------------------------------------------
    # Facade accessors (expose injected ports under clean names)
    # ------------------------------------------------------------------

    @property
    def catalog(self) -> AssetCatalogPort:
        """The asset catalog port for this session."""
        return self._catalog

    @property
    def _files(self):
        """The file store port (used by tests to inspect in-memory state)."""
        return self._file_manager.file_backend

    @property
    def ingestor(self) -> Ingestor:
        """Ingestor bound to the current catalog/files/archive."""
        return self._ingestor

    @property
    def network_dir(self) -> Path:
        """Root directory for this network."""
        return Path(self._file_manager.directory_tree.network_dir(self.network.name))

    @property
    def station_dir(self) -> Path:
        """Root directory for this station."""
        return Path(self._file_manager.directory_tree.station_dir(
            network=self.network.name, station=self.station.name
        ))

    def campaign_layout(self) -> CampaignLayout:
        """Return the campaign layout. Raises if campaign not set."""
        if not self.campaign.name:
            raise ValueError("campaign_layout requires a campaign to be set; call set_campaign() first")
        return self._file_manager.directory_tree.campaign(
            network=self.network.name, station=self.station.name, campaign=self.campaign.name
        )

    def garpos_survey(self) -> "GARPOSLayout":
        """Return the GARPOS survey layout. Raises if survey not set."""
        if not self.survey.name:
            raise ValueError("garpos_survey requires a survey to be set; call set_survey() first")
        return self._file_manager.directory_tree.garpos(
            network=self.network.name,
            station=self.station.name,
            campaign=self.campaign.name,
            survey=self.survey.name,
        )

    def ensure_garpos_survey(self) -> "GARPOSLayout":
        """Materialise GARPOS survey directories and return the layout."""
        if not self.survey.name:
            raise ValueError("ensure_garpos_survey requires a survey to be set; call set_survey() first")
        return self._file_manager.ensure_garpos_survey(
            network=self.network.name,
            station=self.station.name,
            campaign=self.campaign.name,
            survey=self.survey.name,
        )

    def list_campaigns(self) -> list[str]:
        """Return campaign names seen in the catalog for this network/station."""
        assets = self._catalog.assets_for(
            network=self.network.name,
            station=self.station.name,
        )
        seen: dict[str, None] = {}
        for a in assets:
            if a.scope.campaign:
                seen[a.scope.campaign] = None
        return list(seen.keys())

    def load_site_metadata(self, site: object) -> None:
        """Load pre-fetched site metadata into this session (test helper)."""
        self.station = ScopeRegistry(
            name=self.station.name,
            layout=self.station.layout,
            metadata=site,
        )

    # ------------------------------------------------------------------
    # Test factory
    # ------------------------------------------------------------------

    @classmethod
    def for_test(
        cls,
        network: str,
        station: str,
        *,
        root: str | Path | None = None,
        campaign: str | None = None,
        survey: str | None = None,
        catalog: AssetCatalogPort | None = None,
        archive: ArchiveSourcePort | None = None,
    ) -> "StationSession":
        """Build a ``StationSession`` backed by in-memory adapters (no disk/network).

        Suitable for unit tests. Optional *campaign* / *survey* set the
        corresponding slots after construction.
        """
        from earthscope_sfg_workflows.data_mgmt.adapters.memory import (
            FakeArchive,
            InMemoryAssetStore,
            InMemoryFileStore,
        )
        from earthscope_sfg_workflows.data_mgmt.core import FileManager
        from earthscope_sfg_workflows.data_mgmt.model import DirectoryTree

        root_path = Path(root) if root else Path("/ws")
        in_catalog = catalog or InMemoryAssetStore()
        in_files = InMemoryFileStore()
        in_archive = archive or FakeArchive()
        file_manager = FileManager(DirectoryTree(root=root_path), in_files)

        # Bypass the real __init__ network/station materialisation side effects
        # by calling __new__ and wiring manually.
        self = cls.__new__(cls)
        object.__setattr__(self, "_catalog", in_catalog)
        object.__setattr__(self, "_file_manager", file_manager)
        object.__setattr__(self, "_archive", in_archive)

        network_layout = file_manager.ensure_network(network)
        object.__setattr__(self, "network", ScopeRegistry(name=network, layout=network_layout))

        station_layout = file_manager.ensure_station(network=network, station=station)
        object.__setattr__(self, "station", ScopeRegistry(name=station, layout=station_layout))

        object.__setattr__(self, "campaign", ScopeRegistry())
        object.__setattr__(self, "survey", ScopeRegistry())
        object.__setattr__(self, "_sv3_pipeline", None)
        object.__setattr__(self, "_qc_pipeline", None)
        object.__setattr__(
            self,
            "_ingestor",
            Ingestor(catalog=in_catalog, file_manager=file_manager, archive=in_archive),
        )

        if campaign is not None:
            self.set_campaign(campaign)
        if survey is not None:
            self.set_survey(survey)

        return self



    def get_pipeline_sv3(self, config: Optional[SV3PipelineConfig] = None, secondary_config: Optional[_Config] = None) -> SV3Pipeline:
        """Get a pipeline instance for the current session scope."""

        base_config = SV3PipelineConfig()
        base_config_updated = base_config.model_copy()
        # Merge primary config if provided, overwriting defaults. Also check for misspelled keys
        if config is not None:
            if isinstance(
                config,
                _Config
            ):
                config = config.model_dump()

            base_config_updated = validate_and_merge_config(
                base_class=base_config, override_config=config
            )

        # Merge secondary config if provided, overwriting primary and defaults. Also check for misspelled keys
        if secondary_config is not None:
            if isinstance(
                secondary_config,
                _Config
            ):
                secondary_config = secondary_config.model_dump()
            base_config_updated = validate_and_merge_config(
                base_class=base_config_updated, override_config=secondary_config
            )

        if self._sv3_pipeline is None:
            self._sv3_pipeline = SV3Pipeline(
                catalog=self._catalog,
                scope=self.scope,
                config=base_config_updated,
            )
        else:
            # Update config of existing pipeline instance
            self._sv3_pipeline.config = base_config_updated

        return self._sv3_pipeline

    def get_pipeline_qc(self, config: Optional[QCPipelineConfig] = None, secondary_config: Optional[_Config] = None) -> QCPipeline:
        """Get a QC pipeline instance for the current session scope."""
        base_config = QCPipelineConfig()
        base_config_updated = base_config.model_copy()
        if config is not None:
            if isinstance(config, QCPipelineConfig):
                config = config.model_dump()
            base_config_updated = validate_and_merge_config(
                base_class=base_config, override_config=config
            )
        if secondary_config is not None:
            if isinstance(
                secondary_config,
                _Config
            ):
                secondary_config = secondary_config.model_dump()
            base_config_updated = validate_and_merge_config(
                base_class=base_config_updated, override_config=secondary_config
            )
        if self._qc_pipeline is None:
            self._qc_pipeline = QCPipeline(
                catalog=self._catalog,
                scope=self.scope,
                config=base_config_updated,
            )
        else:
            self._qc_pipeline.config = base_config_updated
        return self._qc_pipeline

    def run_pipeline_qc(
        self,
        job: Literal[
            "all",
            "process_qcpin",
            "build_rinex",
            "run_pride",
            "process_kinematic",
            "refine_shotdata",
        ] = "all",
        config: Optional[QCPipelineConfig] = None,
    ) -> None:
        """Run the QC pipeline for the current session scope."""
        assert job in QC_JOBS, f"Job must be one of {list(QC_JOBS.keys())}"
        pipeline = self.get_pipeline_qc(config=config)
        try:
            QC_JOBS[job](pipeline)
        except Exception as e:
            logger.error(f"QC job '{job}' failed: {e}")
            raise e

    def run_pipeline_sv3(
        self,
        job: Literal[
            "all",
            "intermediate",
            "process_novatel",
            "build_rinex",
            "run_pride",
            "process_kinematic",
            "process_dfop00",
            "refine_shotdata",
            "process_svp",
        ] = "all",
        config: Optional[_Config] = None,
        secondary_config: Optional[_Config] = None,
    ) -> None:
        """Run the SV3 pipeline for the current session scope."""
        assert job in SV3_JOBS, f"Job must be one of {SV3_JOBS.keys()}"

        pipeline = self.get_pipeline_sv3(config=config, secondary_config=secondary_config)
        try:
            SV3_JOBS[job](pipeline)
        except Exception as e:
            logger.error(f"SV3 job '{job}' failed: {e}")
            raise e

    # ------------------------------------------------------------------
    # Remote sync (push / pull)
    # ------------------------------------------------------------------

    def push_station_to_remote(self, overwrite: bool = False) -> None:
        """Upload TileDB arrays for the current station to the remote backend."""
        if not self._file_manager.has_remote:
            logger.warning("push_station_to_remote: no remote configured, skipping.")
            return
        tiledb = self.tiledb_layout()
        for path in tiledb.all_paths:
            count = self._file_manager.push_dir(UPath(path), overwrite=overwrite)
            logger.info(f"Pushed {count} files from {path}")

    @_require_campaign
    def push_campaign_to_remote(self, overwrite: bool = False) -> None:
        """Upload SVP, RINEX, and log files for the active campaign to the remote backend."""
        if not self._file_manager.has_remote:
            logger.warning("push_campaign_to_remote: no remote configured, skipping.")
            return
        campaign = self.ensure_campaign()
        self._compress_rinex(UPath(campaign.intermediate))
        for dir_path in (campaign.processed, campaign.intermediate, campaign.logs):
            count = self._file_manager.push_dir(UPath(dir_path), overwrite=overwrite)
            logger.info(f"Pushed {count} files from {dir_path}")

    def pull_from_remote(self, overwrite: bool = False) -> None:
        """Download TileDB arrays and active campaign files from the remote mirror."""
        if not self._file_manager.has_remote:
            logger.warning("pull_from_remote: no remote configured, skipping.")
            return
        tiledb = self.tiledb_layout()
        for path in tiledb.all_paths:
            count = self._file_manager.pull_dir(UPath(path), overwrite=overwrite)
            logger.info(f"Pulled {count} files to {path}")
        if self.campaign.name:
            campaign = self.ensure_campaign()
            count = self._file_manager.pull_dir(UPath(campaign.root), overwrite=overwrite)
            logger.info(f"Pulled {count} campaign files to {campaign.root}")

    def _compress_rinex(self, rinex_dir: UPath) -> None:
        """Compress any uncompressed RINEX files in *rinex_dir* to CRINEX gz format."""
        from earthscope_sfg_tools.rinex_tools import crinex_compress

        rinex_dir = UPath(rinex_dir)
        if not rinex_dir.exists():
            return
        station_name = self.station.name
        for rinex_file in rinex_dir.rglob(f"*{station_name}*"):
            if not rinex_file.is_file():
                continue
            if ".crx" in rinex_file.suffix:
                continue
            if any(ext in rinex_file.suffix for ext in ["S", "d", ".gz"]):
                continue
            new_suffix = rinex_file.suffix[:-1] + "d"
            compressed = rinex_file.with_suffix(new_suffix + ".gz")
            if not compressed.exists():
                try:
                    crinex_compress(rinex_file, compressed, gzip=True, logger=logger.logger)
                except Exception as e:
                    logger.error(f"Failed to compress {rinex_file}: {e}")

    # ------------------------------------------------------------------
    # Mid-processing — survey parsing
    # ------------------------------------------------------------------
    @_require_site_metadata
    @_require_campaign
    def parse_surveys(
        self,
        survey_id: str | None = None,
        override: bool = False,
        write_intermediate: bool = False,
    ) -> None:
        """Parse surveys for the active campaign and write CSVs into survey dirs."""
        campaign_meta = self.campaign.metadata
        if campaign_meta is None:
            raise ValueError("Campaign metadata must be loaded before parse_surveys")

        tiledb = self.tiledb_layout()
        campaign = self.ensure_campaign()

        shotDataTDB = TDBShotDataArray(tiledb.shotdata)

        with open(campaign.metadata_file, "w") as f:
            json.dump(campaign_meta.model_dump(mode="json"), f, indent=4)

        surveys_to_process: list[Survey] = [
            s for s in campaign_meta.surveys if survey_id is None or survey_id == s.id
        ]
        if not surveys_to_process:
            raise ValueError(f"Survey {survey_id} not found in campaign {campaign_meta.name}.")

        for survey in surveys_to_process:
            self.set_survey(survey_id=survey.id)
            survey_root = self.survey_dir

            shotdata_file_name = f"{survey.id}_{survey.type.value}_shotdata.csv".replace(" ", "")
            shotdata_dest = survey_root / shotdata_file_name

            if not shotdata_dest.exists() or shotdata_dest.stat().st_size == 0 or override:
                df = shotDataTDB.read_df(start=survey.start, end=survey.end)
                if df.empty:
                    logger.warning(
                        f"No shot data found for survey {survey.id} from "
                        f"{survey.start} to {survey.end}, skipping survey."
                    )
                    continue
                df.to_csv(shotdata_dest)

            if write_intermediate:
                kin_name = f"{survey.id}_{survey.type.value}_kinpositiondata.csv".replace(" ", "")
                kin_dest = survey_root / kin_name
                if not kin_dest.exists() or kin_dest.stat().st_size == 0 or override:
                    kin_tdb = TDBKinPositionArray(tiledb.kin_position)
                    kin_df = kin_tdb.read_df(start=survey.start, end=survey.end)
                    if kin_df.empty:
                        logger.warning(f"No kinposition data found for survey {survey.id}")
                    else:
                        kin_df.to_csv(kin_dest)

                imu_name = f"{survey.id}_{survey.type.value}_imupositiondata.csv".replace(" ", "")
                imu_dest = survey_root / imu_name
                if not imu_dest.exists() or imu_dest.stat().st_size == 0 or override:
                    imu_tdb = TDBIMUPositionArray(tiledb.imu_position)
                    imu_df = imu_tdb.read_df(start=survey.start, end=survey.end)
                    if imu_df.empty:
                        logger.warning(f"No imuposition data found for survey {survey.id}")
                    else:
                        imu_df.to_csv(imu_dest)

            with open(self.survey_metadata_file, "w") as f:
                json.dump(survey.model_dump(mode="json"), f, indent=4)
