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
    IngestReport,
    NetworkLayout,
    SFGScope,
    DirectoryTree,
    GARPOSLayout,
    StationLayout,
    SurveyLayout,
    SurveyLayout,
    TileDBLayout,
)

from earthscope_sfg_tools.datamodels.metadata import Site as _Site

from earthscope_sfg_workflows.data_mgmt.ports import (
    ArchiveSourcePort,
    AssetCatalogPort,
    FileStorePort,
)
from earthscope_sfg_workflows.data_mgmt.ports import ArchiveNotFoundError
from earthscope_sfg_workflows.utils.model_update import validate_and_merge_config
from earthscope_sfg_workflows.workflows.pipelines.config import DFOP00Config, DFOP00Config, NovatelConfig, PositionUpdateConfig, SV3PipelineConfig,RinexConfig
from earthscope_sfg_workflows.workflows.pipelines.sv3_pipeline import SV3_JOBS, SV3Pipeline


if TYPE_CHECKING:  # pragma: no cover
    from earthscope_sfg_tools.datamodels.metadata import Campaign, Site, Survey
    from earthscope_sfg_tools.tiledb_integration import (
        TDBAcousticArray,
        TDBGNSSObsArray,
        TDBIMUPositionArray,
        TDBKinPositionArray,
        TDBShotDataArray,
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
    ) -> None:

        self._catalog = catalog
        self._file_manager = file_manager
        self._archive = archive

        network_layout: NetworkLayout = self._file_manager.ensure_network(network)
        self.network = ScopeRegistry(name=network, layout=network_layout)

        station_layout: StationLayout = self._file_manager.ensure_station(network=network, station=station)
        station_metadata = self._fetch_site_metadata()
        self.station = ScopeRegistry(name=station, layout=station_layout, metadata=station_metadata)

        # Mutable campaign/survey slots — None until explicitly set.
        self.campaign: ScopeRegistry = ScopeRegistry()  # dummy initial value; guarded by _require_campaign
        self.survey: ScopeRegistry = ScopeRegistry()  # dummy initial value; guarded by _require_survey

        self.scope = SFGScope(
            network=self.network,
            station=self.station,
            campaign=self.campaign,
            survey=self.survey,
        )  

        self._ingestor = Ingestor(
            catalog=self._catalog,
            file_manager=self._file_manager,
            archive=self._archive,
        )

        self._sv3_pipeline = None  # lazy init in sv3_pipeline property

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

    def _fetch_site_metadata(self) -> "Site | None":
        """Load site metadata: disk first, then EarthScope archive. Persist on fetch."""

        write_dest = (
            self._file_manager.directory_tree.station_dir(self._station_scope) / "site_metadata.json"
        )

        if write_dest.exists():
            try:
                return _Site.from_json(write_dest)
            except Exception as exc:
                warnings.warn(f"Error loading site metadata from disk: {exc}")

        try:
            site = self._archive.load_site_metadata(
                scope=self._station_scope
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
    def discover_remote(self) -> None:
        """Discover assets in the archive for the current scope and add to catalog."""
        report: IngestReport = self._ingestor.discover_archive(self.scope)
        logger.info(f"Discovered {report.cataloged} assets in archive for scope {self.scope} with {report.errors} errors")

    @_require_campaign
    def download_remote(self,override:bool=False,rinex_1hz: bool = False) -> None:
        """Download assets from the archive for the current scope to local storage."""

        dest_dir = self.campaign.layout.raw
        report: IngestReport = self._ingestor.download(self.scope, dest_dir=dest_dir, override=override, rinex_1hz=rinex_1hz)
        logger.info(f"Downloaded {report.cataloged} assets from archive for scope {self.scope} with {report.errors} errors")

    # ------------------------------------------------------------------
    # Mutation
    # ------------------------------------------------------------------

    def set_campaign(self, campaign_id: str) -> None:
        """Set the active campaign, clear survey, materialise campaign dirs."""
        self._reset_lower("campaign")  # clear mutable levels below campaign (i.e. survey)
        campaign_meta:Optional[Campaign] = self._resolve_campaign_in_site(campaign_id)
        layout:Optional[CampaignLayout] = self._file_manager.ensure_campaign(self.scope)
        self.campaign = ScopeRegistry(
            name=campaign_id, layout=layout,metadata=campaign_meta
        )

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
    # Pre-Processing
    # ------------------------------------------------------------------

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
        
    