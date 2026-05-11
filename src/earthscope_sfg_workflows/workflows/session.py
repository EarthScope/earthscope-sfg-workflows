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
from typing import TYPE_CHECKING, Callable, TypeVar

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
    SFGScope,
    DirectoryTree,
    GARPOSLayout,
    SurveyLayout,
    SurveyLayout,
    TileDBLayout,
)
from earthscope_sfg_workflows.data_mgmt.adapters.test_adapters import (
    FakeArchive,
    InMemoryAssetStore,
    InMemoryFileStore,
)
from earthscope_sfg_tools.datamodels.metadata import Site as _Site

from earthscope_sfg_workflows.data_mgmt.ports import (
    ArchiveSourcePort,
    AssetCatalogPort,
    FileStorePort,
)
from earthscope_sfg_workflows.data_mgmt.ports import ArchiveNotFoundError

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

# ---------------------------------------------------------------------------
# Method-level scope enforcement decorators (for CampaignSession internals)
# ---------------------------------------------------------------------------


def _require_campaign(method: _F) -> _F:
    """Decorator: raise if the session's campaign slot is not set."""

    @wraps(method)
    def wrapper(self: "StationSession", *args, **kwargs):
        if self._campaign is None:
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
        if self._survey is None:
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
    name: str
    layout: CampaignLayout | SurveyLayout | None
    metadata: Campaign | Survey | None
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
        self._network = ScopeRegistry(name=network, layout=None, location=None)
        self._station = ScopeRegistry(name=station, layout=None, location=None)
        self._catalog = catalog
        self._file_manager = file_manager
        self._archive = archive

        self._ingestor = Ingestor(
            catalog=self._catalog,
            file_manager=self._file_manager,
            archive=self._archive,
        )
    
        self._station_scope = SFGScope(network=self._network.name, station=self._station.name, campaign="")
        # Materialise N/S dirs eagerly; TileDB is station-scoped (built once here).
  
        self._file_manager.ensure_station(self._station_scope)
        
        # Load site metadata eagerly: disk → archive fallback.
        self._station.metadata = self._fetch_site_metadata()

        # Mutable campaign/survey slots — None until explicitly set.
        self._campaign:ScopeRegistry = ScopeRegistry(name="", layout=None, metadata=None)  # dummy initial value; guarded by _require_campaign
        self._survey:ScopeRegistry = ScopeRegistry(name="", layout=None, metadata=None)  # dummy initial value; guarded by _require_survey

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------


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
                network=self._network.name,
                station=self._station.name,
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
        if self._station is None:
            return None
        try:
            for c in self._station.campaigns:
                if c.name == campaign_id:
                    return c
        except AttributeError:
            pass
        return None

    # ------------------------------------------------------------------
    # Test factory
    # ------------------------------------------------------------------

    @classmethod
    def for_test(
        cls,
        *,
        root: Path | str = Path("/virtual"),
        network: str = "test_net",
        station: str = "test_sta",
        campaign: str | None = None,
        survey: str | None = None,
    ) -> "StationSession":
        """Build a session backed entirely by in-memory adapters."""


        session = cls(
            network,
            station,
            root=root,
            catalog=InMemoryAssetStore(),
            files=InMemoryFileStore(),
            archive=FakeArchive(),
        )
        if campaign is not None:
            session.set_campaign(campaign)
        if survey is not None:
            session.set_survey(survey)
        return session

    # ------------------------------------------------------------------
    # Scope readers — N/S fixed, C/V mutable
    # ------------------------------------------------------------------



    @property
    @_require_campaign
    def scope(self) -> SFGScope:
        """Active scope including campaign and current survey (survey may be None)."""
        return SFGScope(
            network=self._network.name,
            station=self._station.name,
            campaign=self._campaign,  # type: ignore[arg-type]  # guarded above
            survey=self._survey,
        )

    # ------------------------------------------------------------------
    # Campaign mutation
    # ------------------------------------------------------------------

    def set_campaign(self, campaign_id: str) -> CampaignLayout:
        """Set the active campaign, clear survey, materialise campaign dirs."""
        self._campaign = campaign_id
        self._survey = None
        self._survey_meta = None
        self._campaign_meta = self._resolve_campaign_in_site(campaign_id)
        layout = self._file_manager.ensure_campaign(self.scope)
        self._campaign = ScopeRegistry(
            name=campaign_id, layout=layout,metadata=self._campaign_meta
        )

    # ------------------------------------------------------------------
    # Survey mutation
    # ------------------------------------------------------------------

    def set_survey(self, survey_id: str) -> SurveyLayout:
        """Set the active survey and materialise its directory."""
        if self._campaign is None:
            raise ValueError(
                "A campaign must be set before setting a survey; "
                "call set_campaign() first"
            )
        layout =  self._file_manager.ensure_survey(self.scope)
        self._survey = ScopeRegistry(
            name=survey_id, layout=layout, metadata=None
        )

    def select_survey_from_metadata(self, survey_id: str) -> None:
        """Find ``survey_id`` in campaign metadata, activate it, materialise directory."""
        if self._campaign_meta is None:
            raise ValueError(
                "Campaign metadata not loaded; cannot select survey from metadata."
            )
        for survey in self._campaign_meta.surveys:
            if survey.id == survey_id:
                self.set_survey(survey_id)
                self._survey.metadata = survey
                return
        raise ValueError(
            f"Survey {survey_id!r} not found in campaign {self._campaign!r}"
        )

    # ------------------------------------------------------------------
    # Metadata properties and loaders
    # ------------------------------------------------------------------

    @property
    def site(self) -> "Site | None":
        return self._station.metadata

    @property
    def campaign_meta(self) -> "Campaign | None":
        return self._campaign.metadata

    @property
    def survey_meta(self) -> "Survey | None":
        return self._survey.metadata

    def load_site_metadata(self, site: "Site") -> None:
        """Override the cached site metadata and re-resolve campaign metadata."""
        self._station = site
        if self._campaign is not None:
            self._campaign_meta = self._resolve_campaign_in_site(self._campaign.name)

    def load_campaign_metadata(self, campaign: "Campaign") -> None:
        """Override the cached campaign metadata."""
        if self._campaign is not None:
            self._campaign.metadata = campaign

    def load_survey_metadata(self, survey: "Survey") -> None:
        """Override the cached survey metadata."""
        self._survey_meta = survey
        
    @_require_campaign
    def select_campaign_from_metadata(self, campaign_id: str) -> CampaignLayout:
        """Switch to ``campaign_id`` (must match an entry in site metadata)."""
        if self._station is None:
            raise ValueError(
                "Site metadata must be loaded before select_campaign_from_metadata()"
            )
        self._campaign_meta = self._resolve_campaign_in_site(campaign_id)
        self._campaign = campaign_id
        self._survey = None
        self._survey_meta = None
        return self._file_manager.ensure_campaign(self.scope)

    # ------------------------------------------------------------------
    # Layout — paths and materialisation
    # ------------------------------------------------------------------

    @property
    def network_dir(self) -> Path:
        return self._tree.network_dir(self._network)

    @property
    def station_dir(self) -> Path:
        return self._tree.station_dir(self._station_scope)

    @property
    @_require_campaign
    @_require_survey
    def survey_dir(self) -> Path:
        return self._tree.survey_dir(self.scope)

    @_require_campaign
    def campaign_layout(self) -> CampaignLayout:
        return self._tree.campaign(self.scope)

    def tiledb_layout(self) -> TileDBLayout:
        """Station-scoped TileDB layout — always available after construction."""
        return self._tree.tiledb(self._station_scope)

    @_require_campaign
    @_require_survey
    def garpos_survey(self) -> GARPOSLayout:
        return self._tree.garpos(self.scope)

    def ensure_station(self) -> TileDBLayout:
        """Materialise TileDB layout dirs (idempotent); station-scoped."""
        return self._file_manager.ensure_station(self._station_scope)

    @_require_campaign
    def ensure_campaign(self) -> CampaignLayout:
        """Materialise campaign dirs (idempotent)."""
        return self._file_manager.ensure_campaign(self.scope)

    @_require_campaign
    @_require_survey
    def ensure_garpos_survey(self) -> GARPOSLayout:
        return self._file_manager.ensure_garpos_survey(self.scope)

    @_require_campaign
    @_require_survey
    def is_garpos_directory(self) -> bool:
        return self._inspector.is_garpos_directory(self.garpos_survey())

    @_require_campaign
    @_require_survey
    def find_rectified_shotdata(self) -> Path | None:
        return self._inspector.find_rectified_shotdata(self.garpos_survey())

    @_require_campaign
    @_require_survey
    def find_filtered_shotdata(self) -> Path | None:
        return self._inspector.find_filtered_shotdata(self.survey_dir)

    @_require_campaign
    def list_surveys(self) -> list[str]:
        try:
            return sorted(
                p.name for p in self.campaign_layout().root.iterdir() if p.is_dir()
            )
        except (OSError, AttributeError):
            return []

    def list_campaigns(self) -> list[str]:
        import re
        try:
            return sorted(
                p.name
                for p in self.station_dir.iterdir()
                if p.is_dir() and re.match(r"^\d{4}", p.name)
            )
        except (OSError, AttributeError):
            return []

    @property
    def site_metadata_file(self) -> Path:
        return self.station_dir / "site_metadata.json"

    @property
    @_require_campaign
    def campaign_metadata_file(self) -> Path:
        return self.campaign_layout().metadata_file

    @property
    @_require_campaign
    @_require_survey
    def survey_metadata_file(self) -> Path:
        return self.survey_dir / "survey_meta.json"

    @property
    def pride_directory(self) -> Path:
        return self._tree.pride_dir

    # ------------------------------------------------------------------
    # Port accessors — callers use these with self.scope directly
    # ------------------------------------------------------------------

    @property
    def catalog(self) -> AssetCatalogPort:
        return self._catalog

    @property
    def ingestor(self) -> Ingestor:
        return self._ingestor

    @property
    def archive(self) -> ArchiveSourcePort:
        return self._archive

    # ------------------------------------------------------------------
    # TileDB — station-scoped, built once
    # ------------------------------------------------------------------

    def build_tiledb(self) -> TileDBRegistry:
        """Materialise TileDB directories and return array handles.

        TileDB arrays are station-scoped.  This method is idempotent and does
        not depend on campaign being set — call it once after construction
        rather than on every campaign switch.
        """
        from earthscope_sfg_tools.tiledb_integration import (
            TDBAcousticArray,
            TDBGNSSObsArray,
            TDBIMUPositionArray,
            TDBKinPositionArray,
            TDBShotDataArray,
        )

        layout = self._file_manager.ensure_station(self._station_scope)
        return TileDBRegistry(
            acoustic=TDBAcousticArray(layout.acoustic),
            kin_position=TDBKinPositionArray(layout.kin_position),
            imu_position=TDBIMUPositionArray(layout.imu_position),
            shotdata=TDBShotDataArray(layout.shotdata),
            shotdata_pre=TDBShotDataArray(layout.shotdata_pre),
            gnss_obs=TDBGNSSObsArray(layout.gnss_obs),
            gnss_obs_secondary=TDBGNSSObsArray(layout.gnss_obs_secondary),
        )

    # ------------------------------------------------------------------
    # Internal (for adapters / tests)
    # ------------------------------------------------------------------

    @property
    def _tree_view(self) -> DirectoryTree:
        return self._tree

    # ------------------------------------------------------------------
    # Resource lifecycle
    # ------------------------------------------------------------------

    def close(self) -> None:
        self._catalog.close()
        self._files.close()
        self._archive.close()

    def __exit__(self, exc_type, exc, tb) -> None:  # noqa: D401
        self.close()


__all__ = [
    "StationSession",
    "TileDBRegistry",
    "_Ports",
    "_build_default_workspace",
    "_build_ports",
    "_require_campaign",
    "_require_survey",
    "_to_asset_kind",
]
