"""Pure data layer for the data_mgmt package.
This module defines immutable, I/O-free dataclasses describing the workspace
directory hierarchy and asset metadata. All path computation is pure; nothing
here touches the filesystem, a database, or the network.

See ``plans/rfc-a-data-mgmt-ports-and-adapters.md`` for the full design.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import datetime
from enum import Enum
from earthscope_sfg_tools.datamodels import Campaign, Site, Survey
from upath import UPath

# ---------------------------------------------------------------------------
# Asset taxonomy
# ---------------------------------------------------------------------------


class AssetKind(str, Enum):
    """Single source of truth for asset types in the catalog.
    Values match the legacy ``AssetType`` enum in ``config/file_config.py``
    so persisted catalog rows remain readable across the migration.
    """

    NOVATEL = "novatel"
    NOVATEL770 = "novatel770"
    NOVATEL000 = "novatel000"
    DFOP00 = "dfop00"
    SONARDYNE = "sonardyne"
    RINEX2 = "rinex2"
    RINEX3 = "rinex3"
    RINEX4 = "rinex4"
    KIN = "kin"
    SEABIRD = "seabird"
    CTD = "ctd"
    LEVERARM = "leverarm"
    MASTER = "master"
    QCPIN = "qcpin"
    NOVATELPIN = "novatelpin"
    KINPOSITION = "kinposition"
    ACOUSTIC = "acoustic"
    SITECONFIG = "siteconfig"
    ATDOFFSET = "atdoffset"
    SVP = "svp"
    SHOTDATA = "shotdata"
    SHOTDATAPRE = "shotdata_pre"
    IMUPOSITION = "imuposition"
    KINRESIDUALS = "kinresiduals"
    GNSSOBSTDB = "GNSSOBSTDB"
    BCOFFLOAD = "bcoffload"
    QCSTA = "qcsta"


# ---------------------------------------------------------------------------
# Scope (mutable cursor for a network/station/campaign[/survey] context)
# ---------------------------------------------------------------------------


@dataclass
class SFGScope:
    """Mutable scope cursor for a network/station/campaign[/survey] context."""

    network: str
    station: str
    campaign: str | None = None
    survey: str | None = None

    @property
    def tuple(self) -> tuple[str, str, str | None, str | None]:
        """Tuple form for quick equality checks."""
        return (self.network, self.station, self.campaign, self.survey)

    def with_survey(self, survey: str) -> "SFGScope":
        """Return a new scope with ``survey`` set."""
        return replace(self, survey=survey)

    @classmethod
    def from_ids(
        cls,
        network_name: str,
        station_name: str,
        campaign_name: str | None = None,
        survey_name: str | None = None,
    ) -> "SFGScope":
        """Constructor alias kept for backward compatibility."""
        return cls(
            network=network_name,
            station=station_name,
            campaign=campaign_name,
            survey=survey_name,
        )

# ---------------------------------------------------------------------------
# Asset entry (pure data; replaces Pydantic AssetEntry with embedded I/O)
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class AssetEntry:
    """A single asset in the catalog. Pure data, no I/O methods."""

    kind: AssetKind
    scope: SFGScope
    id: int | None = None
    local_path: UPath | None = None
    remote_path: str | None = None
    remote_type: str | None = None
    is_processed: bool = False
    parent_id: int | None = None
    timestamp_data_start: datetime | None = None
    timestamp_data_end: datetime | None = None
    timestamp_created: datetime | None = None

    def is_addressable(self) -> bool:
        """At least one of local_path / remote_path is set."""
        return self.local_path is not None or self.remote_path is not None

    def with_id(self, asset_id: int) -> "AssetEntry":
        return replace(self, id=asset_id)

    def with_local_path(self, path: UPath) -> "AssetEntry":
        return replace(self, local_path=path)


# ---------------------------------------------------------------------------
# Layout dataclasses (pure path math)
# ---------------------------------------------------------------------------


# Directory / file name constants. These mirror
# ``data_mgmt/directorymgmt/config.py`` and remain importable from there
# during the migration.
_RAW_DIR = "raw"
_PROCESSED_DIR = "processed"
_INTERMEDIATE_DIR = "intermediate"
_LOGS_DIR = "logs"
_QC_DIR = "qc"
_TILEDB_DIR = "TileDB"
_PRIDE_DIR = "Pride"
_GARPOS_DIR = "GARPOS"
_GARPOS_RESULTS_DIR = "results"
_GARPOS_LOGS_DIR = "logs"
_GARPOS_OBS_FILE = "observation.ini"
_GARPOS_SETTINGS_FILE = "default_settings.ini"
_GARPOS_SVP_FILE = "svp.csv"
_CAMPAIGN_SVP_FILE = "svp.csv"
_CAMPAIGN_META_FILE = "campaign_meta.json"
_SURVEY_META_FILE = "survey_meta.json"
_CATALOG_DB_FILE = "catalog.sqlite"

# TileDB arrays (per station)
_TDB_ACOUSTIC = "acoustic.tdb"
_TDB_KIN = "kin_position.tdb"
_TDB_IMU = "imu_position.tdb"
_TDB_SHOTDATA = "shotdata.tdb"
_TDB_SHOTDATA_PRE = "shotdata_pre.tdb"
_TDB_GNSS = "gnss_obs.tdb"
_TDB_GNSS_SECONDARY = "gnss_obs_secondary.tdb"
_TDB_QC_SHOTDATA = "qc_shotdata.tdb"
_TDB_QC_SHOTDATA_PRE = "qc_shotdata_pre.tdb"
_TDB_QC_KIN = "qc_kin_position.tdb"
_TDB_QC_GNSS = "qc_gnss_obs.tdb"


@dataclass(frozen=True, slots=True)
class TileDBLayout:
    """All TileDB array paths for a station. Pure path math."""

    root: UPath
    acoustic: UPath
    kin_position: UPath
    imu_position: UPath
    shotdata: UPath
    shotdata_pre: UPath
    gnss_obs: UPath
    gnss_obs_secondary: UPath
    qc_shotdata: UPath
    qc_shotdata_pre: UPath
    qc_kin_position: UPath
    qc_gnss_obs: UPath

    @staticmethod
    def for_station(station_dir: UPath) -> "TileDBLayout":
        root = station_dir / _TILEDB_DIR
        return TileDBLayout(
            root=root,
            acoustic=root / _TDB_ACOUSTIC,
            kin_position=root / _TDB_KIN,
            imu_position=root / _TDB_IMU,
            shotdata=root / _TDB_SHOTDATA,
            shotdata_pre=root / _TDB_SHOTDATA_PRE,
            gnss_obs=root / _TDB_GNSS,
            gnss_obs_secondary=root / _TDB_GNSS_SECONDARY,
            qc_shotdata=root / _TDB_QC_SHOTDATA,
            qc_shotdata_pre=root / _TDB_QC_SHOTDATA_PRE,
            qc_kin_position=root / _TDB_QC_KIN,
            qc_gnss_obs=root / _TDB_QC_GNSS,
        )

    @property
    def all_paths(self) -> tuple[UPath, ...]:
        return (
            self.root,
            self.acoustic,
            self.kin_position,
            self.imu_position,
            self.shotdata,
            self.shotdata_pre,
            self.gnss_obs,
            self.gnss_obs_secondary,
            self.qc_shotdata,
            self.qc_shotdata_pre,
            self.qc_kin_position,
            self.qc_gnss_obs,
        )


@dataclass(frozen=True, slots=True)
class CampaignLayout:
    """All paths for a campaign directory. Pure."""

    root: UPath
    raw: UPath
    processed: UPath
    intermediate: UPath
    logs: UPath
    qc: UPath
    metadata_dir: UPath
    metadata_file: UPath
    svp_file: UPath
    rinex: UPath | None = None

    @staticmethod
    def for_campaign(campaign_dir: UPath) -> "CampaignLayout":
        processed = campaign_dir / _PROCESSED_DIR
        return CampaignLayout(
            root=campaign_dir,
            raw=campaign_dir / _RAW_DIR,
            processed=processed,
            intermediate=campaign_dir / _INTERMEDIATE_DIR,
            logs=campaign_dir / _LOGS_DIR,
            qc=campaign_dir / _QC_DIR,
            metadata_dir=campaign_dir / "metadata",
            metadata_file=campaign_dir / "metadata" / _CAMPAIGN_META_FILE,
            svp_file=processed / _CAMPAIGN_SVP_FILE,
            rinex=processed / "rinex",
        )

    @property
    def standard_dirs(self) -> tuple[UPath, ...]:
        return (self.root, self.raw, self.processed, self.intermediate, self.logs, self.qc, self.metadata_dir)

@dataclass(frozen=True, slots=True)
class StationLayout:
    """All paths for a station directory. Pure."""

    root: UPath
    campaigns : dict[str, CampaignLayout | None] = field(default_factory=dict)
    metadata: UPath | None = None
    tiledb: TileDBLayout | None = None

    @property
    def standard_dirs(self) -> tuple[UPath, ...]:
        return (self.root,)
    
@dataclass
class NetworkLayout:
    """All paths for a network directory. Pure."""

    root: UPath
    stations: dict[str, StationLayout | None] = field(default_factory=dict)

    @property
    def standard_dirs(self) -> tuple[UPath, ...]:
        return (self.root,)

    
@dataclass
class SurveyLayout:
    """All paths for a survey directory. Pure."""

    root: UPath
    metadata_file: UPath
    garpos: "GARPOSLayout"
    shotdata: UPath | None = None
    kinpositiondata: UPath | None = None
    imupositiondata: UPath | None = None

    @staticmethod
    def for_survey(
        survey_dir: UPath,
        survey_id: str | None = None,
        survey_type: str | None = None,
    ) -> "SurveyLayout":
        shotdata = kinpositiondata = imupositiondata = None
        if survey_id is not None and survey_type is not None:
            prefix = f"{survey_id}_{survey_type}".replace(" ", "")
            shotdata = survey_dir / f"{prefix}_shotdata.csv"
            kinpositiondata = survey_dir / f"{prefix}_kinpositiondata.csv"
            imupositiondata = survey_dir / f"{prefix}_imupositiondata.csv"
        return SurveyLayout(
            root=survey_dir,
            metadata_file=survey_dir / _SURVEY_META_FILE,
            garpos=GARPOSLayout.for_survey(survey_dir),
            shotdata=shotdata,
            kinpositiondata=kinpositiondata,
            imupositiondata=imupositiondata,
        )

    @property
    def standard_dirs(self) -> tuple[UPath, ...]:
        return (self.root,)


@dataclass(frozen=True, slots=True)
class GARPOSLayout:
    """All paths for a GARPOS survey directory. Pure."""

    root: UPath
    logs: UPath
    obs_file: UPath
    settings_file: UPath
    svp_file: UPath
    results: UPath

    @staticmethod
    def for_survey(survey_dir: UPath) -> "GARPOSLayout":
        root = survey_dir / _GARPOS_DIR
        return GARPOSLayout(
            root=root,
            logs=root / _GARPOS_LOGS_DIR,
            obs_file=root / _GARPOS_OBS_FILE,
            settings_file=root / _GARPOS_SETTINGS_FILE,
            svp_file=root / _GARPOS_SVP_FILE,
            results=root / _GARPOS_RESULTS_DIR,
        )

    @property
    def standard_dirs(self) -> tuple[UPath, ...]:
        return (self.root, self.logs, self.results)


@dataclass
class DirectoryTree:
    """Workspace-rooted view of the hierarchy. Pure path math, no I/O."""

    root: UPath
    catalog_db: UPath = field(default=None, init=False)  # type: ignore[assignment]
    pride_dir: UPath = field(default=None, init=False)   # type: ignore[assignment]

    def __post_init__(self) -> None:
        if self.catalog_db is None:
            self.catalog_db = self.root / _CATALOG_DB_FILE
        if self.pride_dir is None:
            self.pride_dir = self.root / _PRIDE_DIR

    def network_dir(self, network: str) -> UPath:
        return self.root / network

    def station_dir(
        self,
        scope: "SFGScope | None" = None,
        *,
        network: str | None = None,
        station: str | None = None,
    ) -> UPath:
        net = scope.network if scope is not None else network
        sta = scope.station if scope is not None else station
        if net is None or sta is None:
            raise ValueError("Provide scope or (network, station)")
        return self.network_dir(net) / sta

    def campaign_dir(
        self,
        scope: "SFGScope | None" = None,
        *,
        network: str | None = None,
        station: str | None = None,
        campaign: str | None = None,
    ) -> UPath:
        net = scope.network if scope is not None else network
        sta = scope.station if scope is not None else station
        camp = scope.campaign if scope is not None else campaign
        if net is None or sta is None or camp is None:
            raise ValueError("Provide scope with campaign or (network, station, campaign)")
        return self.station_dir(network=net, station=sta) / camp

    def survey_dir(
        self,
        scope: "SFGScope | None" = None,
        *,
        network: str | None = None,
        station: str | None = None,
        campaign: str | None = None,
        survey: str | None = None,
    ) -> UPath:
        net = scope.network if scope is not None else network
        sta = scope.station if scope is not None else station
        camp = scope.campaign if scope is not None else campaign
        surv = scope.survey if scope is not None else survey
        if surv is None:
            raise ValueError("survey must be set to compute survey_dir")
        return self.campaign_dir(network=net, station=sta, campaign=camp) / surv

    def network(self, network: str) -> NetworkLayout:
        return NetworkLayout(root=self.network_dir(network))

    def station(
        self,
        scope: "SFGScope | None" = None,
        *,
        network: str | None = None,
        station: str | None = None,
    ) -> StationLayout:
        net = scope.network if scope is not None else network
        sta = scope.station if scope is not None else station
        if net is None or sta is None:
            raise ValueError("Provide scope or (network, station)")
        return StationLayout(
            root=self.station_dir(network=net, station=sta),
            metadata=self.station_dir(network=net, station=sta) / "site_metadata.json",
            tiledb=self.tiledb(network=net, station=sta),
        )

    def tiledb(
        self,
        scope: "SFGScope | None" = None,
        *,
        network: str | None = None,
        station: str | None = None,
    ) -> TileDBLayout:
        net = scope.network if scope is not None else network
        sta = scope.station if scope is not None else station
        if net is None or sta is None:
            raise ValueError("Provide scope or (network, station)")
        return TileDBLayout.for_station(self.station_dir(network=net, station=sta))

    def campaign(
        self,
        scope: "SFGScope | None" = None,
        *,
        network: str | None = None,
        station: str | None = None,
        campaign: str | None = None,
    ) -> CampaignLayout:
        net = scope.network if scope is not None else network
        sta = scope.station if scope is not None else station
        camp = scope.campaign if scope is not None else campaign
        if net is None or sta is None or camp is None:
            raise ValueError("Provide scope with campaign or (network, station, campaign)")
        return CampaignLayout.for_campaign(self.campaign_dir(network=net, station=sta, campaign=camp))

    def survey(
        self,
        scope: "SFGScope | None" = None,
        *,
        network: str | None = None,
        station: str | None = None,
        campaign: str | None = None,
        survey: str | None = None,
    ) -> SurveyLayout:
        net = scope.network if scope is not None else network
        sta = scope.station if scope is not None else station
        camp = scope.campaign if scope is not None else campaign
        surv = scope.survey if scope is not None else survey
        return SurveyLayout.for_survey(self.survey_dir(network=net, station=sta, campaign=camp, survey=surv))

    def garpos(
        self,
        scope: "SFGScope | None" = None,
        *,
        network: str | None = None,
        station: str | None = None,
        campaign: str | None = None,
        survey: str | None = None,
    ) -> GARPOSLayout:
        net = scope.network if scope is not None else network
        sta = scope.station if scope is not None else station
        camp = scope.campaign if scope is not None else campaign
        surv = scope.survey if scope is not None else survey
        return GARPOSLayout.for_survey(self.survey_dir(network=net, station=sta, campaign=camp, survey=surv))


# ---------------------------------------------------------------------------
# Reports
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class IngestReport:
    """Outcome of an ingest / discover / download operation."""

    cataloged: int = 0
    downloaded: int = 0
    skipped: int = 0
    errors: tuple[str, ...] = field(default_factory=tuple)

    @property
    def ok(self) -> bool:
        return not self.errors

    def __add__(self, other: "IngestReport") -> "IngestReport":
        return IngestReport(
            cataloged=self.cataloged + other.cataloged,
            downloaded=self.downloaded + other.downloaded,
            skipped=self.skipped + other.skipped,
            errors=self.errors + other.errors,
        )


@dataclass(frozen=True, slots=True)
class FileInfo:
    """Lightweight FS entry returned by FileStore.list_files."""

    path: UPath
    size_bytes: int | None = None
    is_file: bool = True


@dataclass(frozen=True, slots=True)
class ArchiveFile:
    """Lightweight remote file descriptor returned by ArchiveSource.list_files."""

    url: str
    size_bytes: int | None = None

    @property
    def filename(self) -> str:
        return UPath(self.url).name



__all__ = [
    "AssetKind",
    "SFGScope",
    "AssetEntry",
    "TileDBLayout",
    "CampaignLayout",
    "SurveyLayout",
    "GARPOSLayout",
    "DirectoryTree",
    "IngestReport",
    "FileInfo",
    "ArchiveFile",
]
