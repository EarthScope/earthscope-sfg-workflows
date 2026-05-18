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
from enum import Enum
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


class ScopeLevel(Enum):
    """Ordered hierarchy of scope slots. Used by :meth:`StationSession._reset_below`.

    Attributes
    ----------
    CAMPAIGN : str
        Represents the campaign scope level with value ``"campaign"``.
    SURVEY : str
        Represents the survey scope level with value ``"survey"``.
    """

    CAMPAIGN = "campaign"
    SURVEY = "survey"


# ---------------------------------------------------------------------------
# Method-level scope enforcement decorators (for CampaignSession internals)
# ---------------------------------------------------------------------------


def _require_campaign(method: _F) -> _F:
    """Decorator: raise if the session's campaign slot is not set.

    Parameters
    ----------
    method : _F
        The method to wrap with campaign-slot enforcement.

    Returns
    -------
    _F
        Wrapped method that raises ``ValueError`` when the campaign slot is ``None``.

    Raises
    ------
    ValueError
        If the session's campaign slot is ``None`` when the wrapped method is called.
    """

    @wraps(method)
    def wrapper(self: "StationSession", *args, **kwargs):
        if self._scope.campaign is None:
            raise ValueError(
                f"{method.__name__} requires a campaign to be set; call set_campaign() first"
            )
        return method(self, *args, **kwargs)

    return wrapper  # type: ignore[return-value]


def _require_survey(method: _F) -> _F:
    """Decorator: raise if the session's survey slot is not set.

    Parameters
    ----------
    method : _F
        The method to wrap with survey-slot enforcement.

    Returns
    -------
    _F
        Wrapped method that raises ``ValueError`` when the survey slot is ``None``.

    Raises
    ------
    ValueError
        If the session's survey slot is ``None`` when the wrapped method is called.
    """

    @wraps(method)
    def wrapper(self: "StationSession", *args, **kwargs):
        if self._scope.survey is None:
            raise ValueError(
                f"{method.__name__} requires a survey to be set; call set_survey() first"
            )
        return method(self, *args, **kwargs)

    return wrapper  # type: ignore[return-value]


def _require_site_metadata(method: _F) -> _F:
    """Decorator: raise if the session's station metadata is not available.

    Parameters
    ----------
    method : _F
        The method to wrap with site-metadata enforcement.

    Returns
    -------
    _F
        Wrapped method that raises ``ValueError`` when site metadata is ``None``.

    Raises
    ------
    ValueError
        If the session's site metadata is ``None`` when the wrapped method is called.
    """

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
    """Open TileDB array handles for a single station, held for the session lifetime.

    Attributes
    ----------
    acoustic : TDBAcousticArray
        TileDB array handle for acoustic shot data.
    kin_position : TDBKinPositionArray
        TileDB array handle for kinematic position data.
    imu_position : TDBIMUPositionArray
        TileDB array handle for IMU position data.
    shotdata : TDBShotDataArray
        TileDB array handle for processed shot data.
    shotdata_pre : TDBShotDataArray
        TileDB array handle for pre-processed shot data.
    gnss_obs : TDBGNSSObsArray
        TileDB array handle for primary GNSS observation data.
    gnss_obs_secondary : TDBGNSSObsArray
        TileDB array handle for secondary GNSS observation data.
    """

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

    Network and station are **immutable** after construction.  TileDB arrays are
    station-scoped and materialised eagerly in ``__init__`` so that switching
    campaigns never triggers a redundant rebuild.

    Campaign and survey are **mutable** slots — call :meth:`set_campaign` to
    change campaign context (materialises directories, resolves metadata, and
    clears the survey slot) and :meth:`set_survey` to select a specific survey.
    Methods that require these slots are decorated with
    :func:`_require_campaign` / :func:`_require_survey` so callers receive a
    clear :class:`ValueError` rather than an opaque ``AttributeError``.

    **Typical access pattern** — most callers obtain a session via
    :class:`~earthscope_sfg_workflows.workflows.workspace.Workspace` or
    :class:`~earthscope_sfg_workflows.workflows.workflow_handler.WorkflowHandler`
    rather than constructing one directly.

    Attributes
    ----------
    scope : SFGScope
        Live view of the current network/station/campaign/survey context.
    site : Site or None
        Station site metadata loaded from disk or EarthScope archive.
    campaign_meta : Campaign or None
        Campaign metadata object for the active campaign.
    root : Path
        Workspace root directory (parent of all network directories).
    survey_dir : Path
        Root directory of the active survey (requires survey to be set).
    survey_metadata_file : Path
        Metadata file path for the active survey (requires survey to be set).
    tiledb_layout : TileDBLayout
        TileDB layout for the current station.
    network_dir : Path
        Root directory for this network.
    station_dir : Path
        Root directory for this station.
    campaign_layout : CampaignLayout
        Directory layout for the active campaign (requires campaign to be set).
    garpos_survey_layout : GARPOSLayout
        Cached GARPOS layout for the active survey (requires survey to be set).
    active_campaign_layout : CampaignLayout or None
        The :class:`CampaignLayout` for the currently active campaign, or ``None``.
    catalog : AssetCatalogPort
        The asset catalog port for this session.
    ingest : IngestService
        Ingest operations scoped to this session (lazy-initialised).
    pipeline : ProcessingService
        Pipeline construction and execution scoped to this session (lazy-initialised).
    sync : SyncService
        Remote sync operations scoped to this session (lazy-initialised).

    Methods
    -------
    configure_remote(remote_root)
        Point the session's file manager at a remote S3 root.
    set_campaign(campaign_id)
        Set the active campaign and materialise its directories.
    set_survey(survey_id)
        Set the active survey and materialise its directory.
    ensure_campaign()
        Materialise campaign directories and return the layout.
    prepare_garpos_survey()
        Materialise GARPOS survey directories on disk and return the layout.
    load_site_metadata(site)
        Load pre-fetched site metadata into this session.
    list_campaigns()
        Return campaign names seen in the catalog for this network/station.
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
        """Initialise a session anchored to *network* and *station*.

        Parameters
        ----------
        network : str
            Network identifier (e.g. ``"NCB"``).
        station : str
            Station identifier within the network.
        catalog : AssetCatalogPort
            Asset catalog port for tracking data assets.
        file_manager : FileManager
            File manager that owns the local directory tree and file-store backend.
        archive : ArchiveSourcePort
            Archive source port for fetching raw data from remote archives.
        """

        self._catalog = catalog
        self._file_manager = file_manager
        self._archive = archive

        self._scope = SFGScope(network=network, station=station)

        self._network_layout: NetworkLayout = self._file_manager.ensure_network(network)
        self._station_layout: StationLayout = self._file_manager.ensure_station(
            network=network, station=station
        )
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

        Parameters
        ----------
        remote_root : str or None
            S3 bucket root (e.g. ``"s3://my-bucket"``).  Pass ``None`` to clear
            any previously configured remote.

        Returns
        -------
        None
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

    def _reset_below(self, level: ScopeLevel) -> None:
        """Clear mutable scope slots below *level* in the hierarchy CAMPAIGN → SURVEY.

        Parameters
        ----------
        level : ScopeLevel
            The scope level at which to reset; all levels below this are cleared.

        Returns
        -------
        None
        """
        if level == ScopeLevel.CAMPAIGN:
            self._scope.survey = None
            self._survey_layout = None
        # ScopeLevel.SURVEY: nothing below survey to clear

    @property
    def scope(self) -> SFGScope:
        """Live view of the current network/station/campaign/survey context."""
        return self._scope

    def _fetch_site_metadata(self, network: str, station: str) -> "Site | None":
        """Load site metadata: disk first, then EarthScope archive. Persist on fetch.

        Parameters
        ----------
        network : str
            Network identifier.
        station : str
            Station identifier within the network.

        Returns
        -------
        Site or None
            The loaded site metadata, or ``None`` if unavailable from both disk and archive.
        """

        write_dest = (
            self._file_manager.directory_tree.station_dir(network=network, station=station)
            / "site_metadata.json"
        )

        if write_dest.exists():
            try:
                return _Site.from_json(write_dest)
            except Exception as exc:
                warnings.warn(f"Error loading site metadata from disk: {exc}")

        try:
            site = self._archive.load_site_metadata(network=network, station=station)
            with open(write_dest, "w") as f:
                json.dump(site.model_dump(mode="json"), f, indent=4)
            return site
        except Exception as exc:
            if not isinstance(exc, ArchiveNotFoundError):
                warnings.warn(f"Error loading site metadata from EarthScope archive: {exc}")

        return None

    def _resolve_campaign_in_site(self, campaign_id: str) -> "Campaign | None":
        """Find the campaign object in site metadata matching ``campaign_id``.

        Parameters
        ----------
        campaign_id : str
            Campaign identifier to look up in the site metadata.

        Returns
        -------
        Campaign or None
            The matching campaign object, or ``None`` if not found.
        """
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
        """Find the survey object in campaign metadata matching ``survey_id``.

        Parameters
        ----------
        survey_id : str
            Survey identifier to look up in the active campaign metadata.

        Returns
        -------
        Survey or None
            The matching survey object, or ``None`` if not found.
        """
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
        """Set the active campaign, materialise its directories, and clear the survey slot.

        Parameters
        ----------
        campaign_id : str
            Campaign identifier to activate.

        Returns
        -------
        CampaignLayout
            The directory layout for the newly activated campaign.
        """
        self._reset_below(ScopeLevel.CAMPAIGN)
        self._campaign_meta = self._resolve_campaign_in_site(campaign_id)
        layout: CampaignLayout = self._file_manager.ensure_campaign(
            network=self._scope.network, station=self._scope.station, campaign=campaign_id
        )
        self._scope.campaign = campaign_id
        self._campaign_layout = layout
        return layout

    @_require_campaign
    def set_survey(self, survey_id: str) -> None:
        """Set the active survey and materialise its directory.

        Parameters
        ----------
        survey_id : str
            Survey identifier to activate.

        Returns
        -------
        None

        Raises
        ------
        ValueError
            If a campaign has not been set (enforced by :func:`_require_campaign`).
        """
        layout: Optional[SurveyLayout] = self._file_manager.ensure_survey(
            network=self._scope.network,
            station=self._scope.station,
            campaign=self._scope.campaign,
            survey=survey_id,
        )
        self._scope.survey = survey_id
        self._survey_layout = layout
        self._campaign_meta = (
            self._resolve_campaign_in_site(self._scope.campaign)
            if self._campaign_meta is None
            else self._campaign_meta
        )

    # ------------------------------------------------------------------
    # Convenience accessors (metadata slots and layout helpers)
    # ------------------------------------------------------------------

    @property
    def site(self) -> "Site | None":
        """Station site metadata loaded from disk or EarthScope archive, or ``None`` if unavailable."""
        return self._site

    @property
    def campaign_meta(self) -> "Campaign | None":
        """Campaign metadata object for the active campaign, or ``None`` if not resolved."""
        return self._campaign_meta

    @property
    def root(self) -> Path:
        """Workspace root directory (parent of all network directories)."""
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
            raise ValueError(
                "survey_metadata_file requires a survey to be set; call set_survey() first"
            )
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
        """Materialise campaign directories and return the layout.

        Returns
        -------
        CampaignLayout
            Directory layout for the current campaign.

        Raises
        ------
        ValueError
            If a campaign has not been set.
        """
        if self._scope.campaign is None:
            raise ValueError(
                "ensure_campaign requires a campaign to be set; call set_campaign() first"
            )
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
        return Path(
            self._file_manager.directory_tree.station_dir(
                network=self._scope.network, station=self._scope.station
            )
        )

    @property
    def campaign_layout(self) -> CampaignLayout:
        """Return the campaign layout. Raises if campaign not set."""
        if not self._scope.campaign:
            raise ValueError(
                "campaign_layout requires a campaign to be set; call set_campaign() first"
            )
        return self._file_manager.directory_tree.campaign(
            network=self._scope.network, station=self._scope.station, campaign=self._scope.campaign
        )

    @property
    def garpos_survey_layout(self) -> "GARPOSLayout":
        """Cached GARPOS layout for the active survey (read-only; no side effects).

        Raises :class:`ValueError` if survey is not set.
        Use :meth:`prepare_garpos_survey` to materialise directories first.
        """
        if not self._scope.survey:
            raise ValueError(
                "garpos_survey_layout requires a survey to be set; call set_survey() first"
            )
        return self._file_manager.directory_tree.garpos(
            network=self._scope.network,
            station=self._scope.station,
            campaign=self._scope.campaign,
            survey=self._scope.survey,
        )

    def prepare_garpos_survey(self) -> "GARPOSLayout":
        """Materialise GARPOS survey directories on disk and return the layout.

        Unlike :attr:`garpos_survey_layout`, this method creates the directories
        if they do not yet exist.

        Returns
        -------
        GARPOSLayout
            Directory layout for the active GARPOS survey.

        Raises
        ------
        ValueError
            If a survey has not been set.
        """
        if not self._scope.survey:
            raise ValueError(
                "prepare_garpos_survey requires a survey to be set; call set_survey() first"
            )
        return self._file_manager.ensure_garpos_survey(
            network=self._scope.network,
            station=self._scope.station,
            campaign=self._scope.campaign,
            survey=self._scope.survey,
        )

    def load_site_metadata(self, site: object) -> None:
        """Load pre-fetched site metadata into this session (test helper).

        Parameters
        ----------
        site : object
            Site metadata object to inject, replacing any previously loaded metadata.

        Returns
        -------
        None
        """
        self._site = site  # type: ignore[assignment]

    def list_campaigns(self) -> list[str]:
        """Return campaign names seen in the catalog for this network/station.

        Returns
        -------
        list of str
            Distinct campaign identifiers recorded in the asset catalog for this
            network/station pair, in insertion order.
        """
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
