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
from dataclasses import dataclass
from functools import wraps
from pathlib import Path
from typing import TYPE_CHECKING, Callable, Optional, TypeVar

from upath import UPath

from earthscope_sfg_workflows.data_mgmt.core import (
    FileManager,
)
from earthscope_sfg_workflows.data_mgmt.ports import ArchiveNotFoundError
from earthscope_sfg_workflows.data_mgmt.model import (
    CampaignLayout,
    GARPOSLayout,
    NetworkLayout,
    SFGScope,
    DirectoryTree,
    StationLayout,
    SurveyLayout,
    TileDBLayout,
)

from earthscope_sfg_tools.datamodels.metadata import Campaign, Site

_Site = Site  # alias kept for the .from_json() classmethod call below

from earthscope_sfg_workflows.data_mgmt.filestore.disk_filestore import FsspecFileStore
from earthscope_sfg_workflows.data_mgmt.ports import (
    ArchiveSourcePort,
    AssetCatalogPort,
    FileStorePort,
)
from earthscope_sfg_workflows.logging import GarposLogger as logger


if TYPE_CHECKING:  # pragma: no cover
    from earthscope_sfg_tools.tiledb_integration import (
        TDBAcousticArray,
        TDBGNSSObsArray,
    )
    from earthscope_sfg_workflows.services.ingest_service import IngestService
    from earthscope_sfg_workflows.services.processing_service import ProcessingService
    from earthscope_sfg_workflows.services.sync_service import SyncService

_F = TypeVar("_F", bound=Callable)

# ---------------------------------------------------------------------------
# Method-level scope enforcement decorators (for CampaignSession internals)
# ---------------------------------------------------------------------------


def _require_campaign(method: _F) -> _F:
    """Decorator: raise if the session's campaign slot is not set."""

    @wraps(method)
    def wrapper(self: "StationSession", *args, **kwargs):
        if self._scope.campaign is None:
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
        if self._scope.survey is None:
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
        if self._site is None:
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
        archive: ArchiveSourcePort,
    ) -> None:

        self._catalog = catalog
        self._file_manager = file_manager
        self._archive = archive

        self._scope = SFGScope(network=network, station=station)

        self._network_layout: NetworkLayout = self._file_manager.ensure_network(network)
        self._station_layout: StationLayout = self._file_manager.ensure_station(network=network, station=station)
        self._site: "Site | None" = self._fetch_site_metadata(network, station)

        # Mutable campaign/survey slots — None until explicitly set.
        self._campaign_layout: "CampaignLayout | None" = None
        self._survey_layout: "SurveyLayout | None" = None
        self._campaign_meta: "Campaign | None" = None

        # Lazy-initialised service instances.
        self._ingest_service = None
        self._pipeline_service = None
        self._sync_service = None

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
        if level == "campaign":
            self._scope.survey = None
            self._survey_layout = None
        elif level == "survey":
            pass  # nothing below survey

    @property
    def scope(self) -> SFGScope:
        """Live view of the current network/station/campaign/survey context."""
        return self._scope

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
        if self._site is None:
            return None
        try:
            for c in self._site.campaigns:
                if c.name == campaign_id:
                    return c
        except AttributeError:
            pass
        return None

    def _resolve_survey_in_campaign(self, survey_id: str) -> "Survey | None":
        """Find the survey object in campaign metadata matching ``survey_id``."""
        if self._campaign_meta is None:
            return None
        try:
            for s in self._campaign_meta.surveys:
                if s.id == survey_id:
                    return s
        except AttributeError:
            pass
        return None

    # ------------------------------------------------------------------
    # Mutation
    # ------------------------------------------------------------------

    def set_campaign(self, campaign_id: str) -> CampaignLayout:
        """Set the active campaign, clear survey, materialise campaign dirs."""
        self._reset_lower("campaign")  # clear mutable levels below campaign (i.e. survey)
        self._campaign_meta = self._resolve_campaign_in_site(campaign_id)
        layout: CampaignLayout = self._file_manager.ensure_campaign(
            network=self._scope.network, station=self._scope.station, campaign=campaign_id
        )
        self._scope.campaign = campaign_id
        self._campaign_layout = layout
        return layout

    @_require_campaign
    def set_survey(self, survey_id: str) -> None:
        """Set the active survey and materialise its directory."""
        layout: Optional[SurveyLayout] = self._file_manager.ensure_survey(
            network=self._scope.network,
            station=self._scope.station,
            campaign=self._scope.campaign,
            survey=survey_id,
        )
        self._scope.survey = survey_id
        self._survey_layout = layout
        self._campaign_meta = self._resolve_campaign_in_site(self._scope.campaign) if self._campaign_meta is None else self._campaign_meta

    # ------------------------------------------------------------------
    # Convenience accessors (metadata slots and layout helpers)
    # ------------------------------------------------------------------

    @property
    def site(self) -> "Site | None":
        return self._site

    @property
    def campaign_meta(self) -> "Campaign | None":
        return self._campaign_meta

    @property
    def root(self) -> Path:
        return Path(self._file_manager.directory_tree.root)

    @property
    def survey_dir(self) -> Path:
        """Root directory of the active survey. Requires campaign and survey to be set."""
        if self._survey_layout is None:
            raise ValueError("survey_dir requires a survey to be set; call set_survey() first")
        return Path(self._survey_layout.root)  # type: ignore[arg-type]

    @property
    def survey_metadata_file(self) -> Path:
        """Metadata file path for the active survey."""
        if self._survey_layout is None:
            raise ValueError("survey_metadata_file requires a survey to be set; call set_survey() first")
        return Path(self._survey_layout.metadata_file)  # type: ignore[arg-type]

    # ------------------------------------------------------------------
    # Convenience layout helpers
    # ------------------------------------------------------------------

    @property
    def tiledb_layout(self) -> TileDBLayout:
        """Return the TileDB layout for the current station."""
        return self._file_manager.directory_tree.tiledb(
            network=self._scope.network, station=self._scope.station
        )

    def ensure_campaign(self) -> CampaignLayout:
        """Materialise campaign directories and return the layout."""
        if self._scope.campaign is None:
            raise ValueError("ensure_campaign requires a campaign to be set; call set_campaign() first")
        return self._file_manager.ensure_campaign(
            network=self._scope.network,
            station=self._scope.station,
            campaign=self._scope.campaign,
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
    def network_dir(self) -> Path:
        """Root directory for this network."""
        return Path(self._file_manager.directory_tree.network_dir(self._scope.network))

    @property
    def station_dir(self) -> Path:
        """Root directory for this station."""
        return Path(self._file_manager.directory_tree.station_dir(
            network=self._scope.network, station=self._scope.station
        ))

    @property
    def campaign_layout(self) -> CampaignLayout:
        """Return the campaign layout. Raises if campaign not set."""
        if not self._scope.campaign:
            raise ValueError("campaign_layout requires a campaign to be set; call set_campaign() first")
        return self._file_manager.directory_tree.campaign(
            network=self._scope.network, station=self._scope.station, campaign=self._scope.campaign
        )

    def garpos_survey(self) -> "GARPOSLayout":
        """Return the GARPOS survey layout. Raises if survey not set."""
        if not self._scope.survey:
            raise ValueError("garpos_survey requires a survey to be set; call set_survey() first")
        return self._file_manager.directory_tree.garpos(
            network=self._scope.network,
            station=self._scope.station,
            campaign=self._scope.campaign,
            survey=self._scope.survey,
        )

    def ensure_garpos_survey(self) -> "GARPOSLayout":
        """Materialise GARPOS survey directories and return the layout."""
        if not self._scope.survey:
            raise ValueError("ensure_garpos_survey requires a survey to be set; call set_survey() first")
        return self._file_manager.ensure_garpos_survey(
            network=self._scope.network,
            station=self._scope.station,
            campaign=self._scope.campaign,
            survey=self._scope.survey,
        )

    def load_site_metadata(self, site: object) -> None:
        """Load pre-fetched site metadata into this session (test helper)."""
        self._site = site  # type: ignore[assignment]

    def list_campaigns(self) -> list[str]:
        """Return campaign names seen in the catalog for this network/station."""
        assets = self._catalog.assets_for(
            network=self._scope.network,
            station=self._scope.station,
        )
        seen: dict[str, None] = {}
        for a in assets:
            if a.scope.campaign:
                seen[a.scope.campaign] = None
        return list(seen.keys())

    @property
    def active_campaign_layout(self) -> "CampaignLayout | None":
        """The :class:`CampaignLayout` for the currently active campaign, or ``None``."""
        return self._campaign_layout

    # ------------------------------------------------------------------
    # Service properties (lazy-initialised)
    # ------------------------------------------------------------------

    @property
    def ingest(self) -> "IngestService":
        """Ingest operations scoped to this session."""
        from earthscope_sfg_workflows.services.ingest_service import IngestService
        if self._ingest_service is None:
            self._ingest_service = IngestService(self)
        return self._ingest_service

    @property
    def pipeline(self) -> "ProcessingService":
        """Pipeline construction and execution scoped to this session."""
        from earthscope_sfg_workflows.services.processing_service import ProcessingService
        if self._pipeline_service is None:
            self._pipeline_service = ProcessingService(self)
        return self._pipeline_service

    @property
    def sync(self) -> "SyncService":
        """Remote sync operations scoped to this session."""
        from earthscope_sfg_workflows.services.sync_service import SyncService
        if self._sync_service is None:
            self._sync_service = SyncService(self)
        return self._sync_service
