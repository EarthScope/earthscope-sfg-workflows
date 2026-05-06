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
from pathlib import Path

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
    IMUPOSITION = "imuposition"
    KINRESIDUALS = "kinresiduals"
    GNSSOBSTDB = "GNSSOBSTDB"
    BCOFFLOAD = "bcoffload"


# ---------------------------------------------------------------------------
# Scope (frozen identity for a campaign / survey)
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class CampaignScope:
    """Immutable identifier for a network/station/campaign[/survey] context."""

    network: str
    station: str
    campaign: str
    survey: str | None = None

    @property
    def tuple(self) -> tuple[str, str, str]:
        return (self.network, self.station, self.campaign)

    def with_survey(self, survey: str) -> "CampaignScope":
        return replace(self, survey=survey)


# ---------------------------------------------------------------------------
# Asset entry (pure data; replaces Pydantic AssetEntry with embedded I/O)
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class AssetEntry:
    """A single asset in the catalog. Pure data, no I/O methods."""

    kind: AssetKind
    scope: CampaignScope
    id: int | None = None
    local_path: Path | None = None
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

    def with_local_path(self, path: Path) -> "AssetEntry":
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

    root: Path
    acoustic: Path
    kin_position: Path
    imu_position: Path
    shotdata: Path
    shotdata_pre: Path
    gnss_obs: Path
    gnss_obs_secondary: Path
    qc_shotdata: Path
    qc_shotdata_pre: Path
    qc_kin_position: Path
    qc_gnss_obs: Path

    @staticmethod
    def for_station(station_dir: Path) -> "TileDBLayout":
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
    def all_paths(self) -> tuple[Path, ...]:
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

    root: Path
    raw: Path
    processed: Path
    intermediate: Path
    logs: Path
    qc: Path
    metadata_file: Path
    svp_file: Path

    @staticmethod
    def for_campaign(campaign_dir: Path) -> "CampaignLayout":
        processed = campaign_dir / _PROCESSED_DIR
        return CampaignLayout(
            root=campaign_dir,
            raw=campaign_dir / _RAW_DIR,
            processed=processed,
            intermediate=campaign_dir / _INTERMEDIATE_DIR,
            logs=campaign_dir / _LOGS_DIR,
            qc=campaign_dir / _QC_DIR,
            metadata_file=campaign_dir / _CAMPAIGN_META_FILE,
            svp_file=processed / _CAMPAIGN_SVP_FILE,
        )

    @property
    def standard_dirs(self) -> tuple[Path, ...]:
        return (self.root, self.raw, self.processed, self.intermediate, self.logs, self.qc)


@dataclass(frozen=True, slots=True)
class GARPOSLayout:
    """All paths for a GARPOS survey directory. Pure."""

    root: Path
    logs: Path
    obs_file: Path
    settings_file: Path
    svp_file: Path
    results: Path

    @staticmethod
    def for_survey(survey_dir: Path) -> "GARPOSLayout":
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
    def standard_dirs(self) -> tuple[Path, ...]:
        return (self.root, self.logs, self.results)


@dataclass(frozen=True, slots=True)
class DirectoryTree:
    """Workspace-rooted view of the hierarchy. Pure path math, no I/O."""

    root: Path

    @property
    def catalog_db(self) -> Path:
        return self.root / _CATALOG_DB_FILE

    @property
    def pride_dir(self) -> Path:
        return self.root / _PRIDE_DIR

    def network_dir(self, network: str) -> Path:
        return self.root / network

    def station_dir(self, scope: CampaignScope) -> Path:
        return self.network_dir(scope.network) / scope.station

    def campaign_dir(self, scope: CampaignScope) -> Path:
        return self.station_dir(scope) / scope.campaign

    def survey_dir(self, scope: CampaignScope) -> Path:
        if scope.survey is None:
            raise ValueError("CampaignScope.survey must be set to compute survey_dir")
        return self.campaign_dir(scope) / scope.survey

    def tiledb(self, scope: CampaignScope) -> TileDBLayout:
        return TileDBLayout.for_station(self.station_dir(scope))

    def campaign(self, scope: CampaignScope) -> CampaignLayout:
        return CampaignLayout.for_campaign(self.campaign_dir(scope))

    def garpos(self, scope: CampaignScope) -> GARPOSLayout:
        return GARPOSLayout.for_survey(self.survey_dir(scope))


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


@dataclass(frozen=True, slots=True)
class FileInfo:
    """Lightweight FS entry returned by FileStore.list_files."""

    path: Path
    size_bytes: int | None = None
    is_file: bool = True


@dataclass(frozen=True, slots=True)
class ArchiveFile:
    """Lightweight remote file descriptor returned by ArchiveSource.list_files."""

    url: str
    size_bytes: int | None = None

    @property
    def filename(self) -> str:
        return Path(self.url).name


__all__ = [
    "AssetKind",
    "CampaignScope",
    "AssetEntry",
    "TileDBLayout",
    "CampaignLayout",
    "GARPOSLayout",
    "DirectoryTree",
    "IngestReport",
    "FileInfo",
    "ArchiveFile",
]
