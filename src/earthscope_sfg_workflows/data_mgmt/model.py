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
    """Single source of truth for every asset type tracked in the catalog.

    Each member's value is the canonical string key stored in the database.

    Attributes
    ----------
    NOVATEL : str
        Raw NovAtel binary data.
    NOVATEL770 : str
        NovAtel data with the ``770`` suffix variant.
    NOVATEL000 : str
        NovAtel data with the ``000`` suffix variant.
    DFOP00 : str
        DFOP00-formatted acoustic ranging data.
    SONARDYNE : str
        Sonardyne acoustic data.
    RINEX2 : str
        RINEX version 2 GNSS observation file.
    RINEX3 : str
        RINEX version 3 GNSS observation file.
    RINEX4 : str
        RINEX version 4 GNSS observation file.
    KIN : str
        Kinematic GNSS solution file.
    SEABIRD : str
        Sea-Bird CTD instrument data.
    CTD : str
        Generic CTD (conductivity / temperature / depth) data.
    LEVERARM : str
        Lever-arm offset file.
    MASTER : str
        Master station configuration file.
    QCPIN : str
        QC position-input file.
    NOVATELPIN : str
        NovAtel position-input file.
    KINPOSITION : str
        Kinematic position output file.
    ACOUSTIC : str
        Processed acoustic ranging data.
    SITECONFIG : str
        Site configuration file.
    ATDOFFSET : str
        ATD (antenna-to-transducer) offset file.
    SVP : str
        Sound-velocity profile file.
    SHOTDATA : str
        Shot data (ranges + positions combined).
    SHOTDATAPRE : str
        Pre-processed shot data.
    IMUPOSITION : str
        IMU-derived position data.
    KINRESIDUALS : str
        Kinematic solution residuals file.
    GNSSOBSTDB : str
        GNSS observation TileDB array.
    BCOFFLOAD : str
        Bench-check offload data.
    QCSTA : str
        QC station file.
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


_RINEX_KIND_BY_MAJOR_VERSION: dict[str, "AssetKind"] = {
    "2": AssetKind.RINEX2,
    "3": AssetKind.RINEX3,
    "4": AssetKind.RINEX4,
}

# Every AssetKind a RINEX observation file could be cataloged under, regardless
# of which version is currently configured. Used by callers that need to find
# already-cataloged RINEX assets without knowing (or caring) which version
# produced them.
RINEX_KINDS: frozenset["AssetKind"] = frozenset(_RINEX_KIND_BY_MAJOR_VERSION.values())


def rinex_kind_for_version(rinex_version: str) -> "AssetKind":
    """Map a RINEX metadata version string (e.g. ``"4.02"``) to its :class:`AssetKind`.

    Raises
    ------
    ValueError
        If the major version has no corresponding :class:`AssetKind`.
    """
    major = rinex_version.split(".")[0]
    try:
        return _RINEX_KIND_BY_MAJOR_VERSION[major]
    except KeyError:
        raise ValueError(f"No AssetKind mapping for RINEX version {rinex_version!r}") from None


# Default download sets used by WorkflowHandler.download_data().
DEFAULT_PREPROCESS_KINDS: frozenset["AssetKind"] = frozenset(
    {
        AssetKind.SONARDYNE,
        AssetKind.NOVATEL,
        AssetKind.NOVATEL000,
        AssetKind.NOVATEL770,
        AssetKind.DFOP00,
        AssetKind.CTD,
        AssetKind.SEABIRD,
    }
)

DEFAULT_INTERMEDIATE_KINDS: frozenset["AssetKind"] = frozenset(
    {
        AssetKind.RINEX4,
        AssetKind.CTD,
        AssetKind.SEABIRD,
        AssetKind.DFOP00,
    }
)


# ---------------------------------------------------------------------------
# Scope (mutable cursor for a network/station/campaign[/survey] context)
# ---------------------------------------------------------------------------


@dataclass
class SFGScope:
    """Mutable scope cursor for a network/station/campaign[/survey] context.

    Attributes
    ----------
    network : str
        Network identifier (e.g. ``"CNRS"``).
    station : str
        Station identifier (e.g. ``"NCC1"``).
    campaign : str or None
        Campaign identifier, or ``None`` when not yet narrowed.
    survey : str or None
        Survey identifier, or ``None`` when not yet narrowed.

    Methods
    -------
    tuple
        Tuple form ``(network, station, campaign, survey)`` for equality checks.
    with_survey(survey)
        Return a new scope with ``survey`` set.
    from_ids(network_name, station_name, campaign_name, survey_name)
        Constructor alias kept for backward compatibility.
    """

    network: str
    station: str
    campaign: str | None = None
    survey: str | None = None

    @property
    def tuple(self) -> tuple[str, str, str | None, str | None]:
        """Tuple form for quick equality checks.

        Returns
        -------
        tuple[str, str, str | None, str | None]
            ``(network, station, campaign, survey)``.
        """
        return (self.network, self.station, self.campaign, self.survey)

    def with_survey(self, survey: str) -> "SFGScope":
        """Return a new scope with ``survey`` set.

        Parameters
        ----------
        survey : str
            Survey identifier to attach.

        Returns
        -------
        SFGScope
            A copy of this scope with ``survey`` populated.
        """
        return replace(self, survey=survey)

    @classmethod
    def from_ids(
        cls,
        network_name: str,
        station_name: str,
        campaign_name: str | None = None,
        survey_name: str | None = None,
    ) -> "SFGScope":
        """Constructor alias kept for backward compatibility.

        Parameters
        ----------
        network_name : str
            Network identifier.
        station_name : str
            Station identifier.
        campaign_name : str or None, optional
            Campaign identifier, by default ``None``.
        survey_name : str or None, optional
            Survey identifier, by default ``None``.

        Returns
        -------
        SFGScope
            A new :class:`SFGScope` instance.
        """
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
    """A single asset in the catalog. Pure data, no I/O methods.

    Attributes
    ----------
    kind : AssetKind
        The type of this asset.
    scope : SFGScope
        The network/station/campaign/survey context this asset belongs to.
    id : int or None
        Primary key assigned by the catalog; ``None`` before insertion.
    local_path : UPath or None
        Absolute path on the local filesystem, or ``None``.
    remote_path : str or None
        Remote URL/path string, or ``None``.
    remote_type : str or None
        Descriptor for the remote storage backend, or ``None``.
    is_processed : bool
        ``True`` when processing of this asset has been completed.
    parent_id : int or None
        Catalog id of the parent asset, or ``None``.
    timestamp_data_start : datetime or None
        Start time of the data contained in this asset.
    timestamp_data_end : datetime or None
        End time of the data contained in this asset.
    timestamp_created : datetime or None
        Wall-clock time when this entry was created.

    Methods
    -------
    is_addressable()
        Return ``True`` if at least one of ``local_path`` / ``remote_path`` is set.
    with_id(asset_id)
        Return a copy with ``id`` set to *asset_id*.
    with_local_path(path)
        Return a copy with ``local_path`` set to *path*.
    """

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
        """At least one of local_path / remote_path is set.

        Returns
        -------
        bool
            ``True`` if ``local_path`` or ``remote_path`` is not ``None``.
        """
        return self.local_path is not None or self.remote_path is not None

    def with_id(self, asset_id: int) -> "AssetEntry":
        """Return a copy of this entry with *asset_id* set.

        Parameters
        ----------
        asset_id : int
            The catalog primary key to assign.

        Returns
        -------
        AssetEntry
            A new :class:`AssetEntry` with ``id`` set to *asset_id*.
        """
        return replace(self, id=asset_id)

    def with_local_path(self, path: UPath) -> "AssetEntry":
        """Return a copy of this entry with *local_path* set.

        Parameters
        ----------
        path : UPath
            The local filesystem path to assign.

        Returns
        -------
        AssetEntry
            A new :class:`AssetEntry` with ``local_path`` set to *path*.
        """
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
    """All TileDB array paths for a station. Pure path math.

    Attributes
    ----------
    root : UPath
        Base TileDB directory (``<station>/TileDB``).
    acoustic : UPath
        Path to the acoustic TileDB array.
    kin_position : UPath
        Path to the kinematic position TileDB array.
    imu_position : UPath
        Path to the IMU position TileDB array.
    shotdata : UPath
        Path to the shot-data TileDB array.
    shotdata_pre : UPath
        Path to the pre-processed shot-data TileDB array.
    gnss_obs : UPath
        Path to the primary GNSS observations TileDB array.
    gnss_obs_secondary : UPath
        Path to the secondary GNSS observations TileDB array.
    qc_shotdata : UPath
        Path to the QC shot-data TileDB array.
    qc_shotdata_pre : UPath
        Path to the QC pre-processed shot-data TileDB array.
    qc_kin_position : UPath
        Path to the QC kinematic position TileDB array.
    qc_gnss_obs : UPath
        Path to the QC GNSS observations TileDB array.

    Methods
    -------
    for_station(station_dir)
        Build a :class:`TileDBLayout` rooted under *station_dir*/TileDB.
    all_paths
        All TileDB paths (root + every array) as a flat tuple.
    """

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
        """Build a TileDBLayout rooted under *station_dir*/TileDB.

        Parameters
        ----------
        station_dir : UPath
            Root directory of the station.

        Returns
        -------
        TileDBLayout
            A fully populated :class:`TileDBLayout` for the station.
        """
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
        """All TileDB paths (root + every array) as a flat tuple.

        Returns
        -------
        tuple[UPath, ...]
            Ordered tuple of root followed by all array paths.
        """
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
    """All paths for a campaign directory. Pure.

    Attributes
    ----------
    root : UPath
        Root campaign directory.
    raw : UPath
        Directory for raw input files.
    processed : UPath
        Directory for processed output files.
    intermediate : UPath
        Directory for intermediate processing artifacts.
    logs : UPath
        Directory for log files.
    qc : UPath
        Directory for QC output files.
    metadata_dir : UPath
        Directory containing campaign metadata.
    metadata_file : UPath
        Path to ``campaign_meta.json``.
    svp_file : UPath
        Path to the campaign-level SVP CSV file.
    rinex : UPath or None
        Path to the RINEX sub-directory under ``processed``, or ``None``.

    Methods
    -------
    for_campaign(campaign_dir)
        Build a :class:`CampaignLayout` rooted at *campaign_dir*.
    standard_dirs
        Tuple of directories that must exist for a valid campaign tree.
    """

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
        """Build a CampaignLayout rooted at *campaign_dir*.

        Parameters
        ----------
        campaign_dir : UPath
            Root directory of the campaign.

        Returns
        -------
        CampaignLayout
            A fully populated :class:`CampaignLayout` for the campaign.
        """
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
        """Directories that must exist for a valid campaign tree.

        Returns
        -------
        tuple[UPath, ...]
            Ordered tuple of required campaign directories.
        """
        return (
            self.root,
            self.raw,
            self.processed,
            self.intermediate,
            self.logs,
            self.qc,
            self.metadata_dir,
        )


@dataclass(frozen=True, slots=True)
class StationLayout:
    """All paths for a station directory. Pure.

    Attributes
    ----------
    root : UPath
        Root station directory.
    campaigns : dict[str, CampaignLayout | None]
        Mapping from campaign name to :class:`CampaignLayout`, or ``None``.
    metadata : UPath or None
        Path to ``site_metadata.json``, or ``None``.
    tiledb : TileDBLayout or None
        TileDB layout for this station, or ``None``.

    Methods
    -------
    standard_dirs
        Tuple of directories that must exist for a valid station tree.
    """

    root: UPath
    campaigns: dict[str, CampaignLayout | None] = field(default_factory=dict)
    metadata: UPath | None = None
    tiledb: TileDBLayout | None = None

    @property
    def standard_dirs(self) -> tuple[UPath, ...]:
        """Directories that must exist for a valid station tree.

        Returns
        -------
        tuple[UPath, ...]
            Ordered tuple of required station directories.
        """
        return (self.root,)


@dataclass
class NetworkLayout:
    """All paths for a network directory. Pure.

    Attributes
    ----------
    root : UPath
        Root network directory.
    stations : dict[str, StationLayout | None]
        Mapping from station name to :class:`StationLayout`, or ``None``.

    Methods
    -------
    standard_dirs
        Tuple of directories that must exist for a valid network tree.
    """

    root: UPath
    stations: dict[str, StationLayout | None] = field(default_factory=dict)

    @property
    def standard_dirs(self) -> tuple[UPath, ...]:
        """Directories that must exist for a valid network tree.

        Returns
        -------
        tuple[UPath, ...]
            Ordered tuple of required network directories.
        """
        return (self.root,)


@dataclass
class SurveyLayout:
    """All paths for a survey directory. Pure.

    Attributes
    ----------
    root : UPath
        Root survey directory.
    metadata_file : UPath
        Path to ``survey_meta.json``.
    garpos : GARPOSLayout
        GARPOS subdirectory layout for this survey.
    shotdata : UPath or None
        Path to the shot-data CSV file, or ``None`` if not yet resolved.
    kinpositiondata : UPath or None
        Path to the kinematic position data CSV file, or ``None``.
    imupositiondata : UPath or None
        Path to the IMU position data CSV file, or ``None``.

    Methods
    -------
    for_survey(survey_dir, survey_id, survey_type)
        Build a :class:`SurveyLayout` rooted at *survey_dir*.
    standard_dirs
        Tuple of directories that must exist for a valid survey tree.
    """

    root: UPath
    metadata_file: UPath
    garpos: "GARPOSLayout"
    shotdata: UPath | None = None
    kinpositiondata: UPath | None = None
    imupositiondata: UPath | None = None

    @staticmethod
    def for_survey(  # noqa: D102
        survey_dir: UPath,
        survey_id: str | None = None,
        survey_type: str | None = None,
    ) -> "SurveyLayout":
        """Build a SurveyLayout rooted at *survey_dir*.

        Parameters
        ----------
        survey_dir : UPath
            Root directory for the survey.
        survey_id : str or None, optional
            Survey identifier used to name data files, by default ``None``.
        survey_type : str or None, optional
            Survey type suffix (e.g. ``"SV3"``) used to name data files,
            by default ``None``.

        Returns
        -------
        SurveyLayout
            A fully populated :class:`SurveyLayout` for the survey.
        """
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
        """Directories that must exist for a valid survey tree.

        Returns
        -------
        tuple[UPath, ...]
            Ordered tuple of required survey directories.
        """
        return (self.root,)


@dataclass(frozen=True, slots=True)
class GARPOSLayout:
    """All paths for a GARPOS survey directory. Pure.

    Attributes
    ----------
    root : UPath
        Root GARPOS directory (``<survey>/GARPOS``).
    logs : UPath
        Directory for GARPOS log files.
    obs_file : UPath
        Path to ``observation.ini``.
    settings_file : UPath
        Path to ``default_settings.ini``.
    svp_file : UPath
        Path to ``svp.csv``.
    results : UPath
        Directory for GARPOS result files.

    Methods
    -------
    for_survey(survey_dir)
        Build a :class:`GARPOSLayout` rooted at *survey_dir*/GARPOS.
    standard_dirs
        Tuple of directories that must exist for a valid GARPOS tree.
    """

    root: UPath
    logs: UPath
    obs_file: UPath
    settings_file: UPath
    svp_file: UPath
    results: UPath

    @staticmethod
    def for_survey(survey_dir: UPath) -> "GARPOSLayout":
        """Build a GARPOSLayout rooted at *survey_dir*/GARPOS.

        Parameters
        ----------
        survey_dir : UPath
            Root directory of the survey.

        Returns
        -------
        GARPOSLayout
            A fully populated :class:`GARPOSLayout` for the survey.
        """
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
        """Directories that must exist for a valid GARPOS tree.

        Returns
        -------
        tuple[UPath, ...]
            Ordered tuple of required GARPOS directories.
        """
        return (self.root, self.logs, self.results)


@dataclass
class DirectoryTree:
    """Workspace-rooted view of the hierarchy. Pure path math, no I/O.

    Attributes
    ----------
    root : UPath
        Workspace root directory.
    catalog_db : UPath
        Path to ``catalog.sqlite`` at the root.
    pride_dir : UPath
        Path to the ``Pride`` directory at the root.

    Methods
    -------
    network_dir(network)
        Return the root directory path for *network*.
    station_dir(scope, *, network, station)
        Return the directory path for a network/station pair.
    campaign_dir(scope, *, network, station, campaign)
        Return the directory path for a network/station/campaign triple.
    survey_dir(scope, *, network, station, campaign, survey)
        Return the directory path for a specific survey.
    network(network)
        Return a :class:`NetworkLayout` for *network*.
    station(scope, *, network, station)
        Return a :class:`StationLayout` for a network/station pair.
    tiledb(scope, *, network, station)
        Return the :class:`TileDBLayout` for a network/station pair.
    campaign(scope, *, network, station, campaign)
        Return a :class:`CampaignLayout` for a network/station/campaign triple.
    survey(scope, *, network, station, campaign, survey)
        Return a :class:`SurveyLayout` for a specific survey.
    garpos(scope, *, network, station, campaign, survey)
        Return the :class:`GARPOSLayout` for a specific survey.
    """

    root: UPath
    catalog_db: UPath = field(default=None, init=False)  # type: ignore[assignment]
    pride_dir: UPath = field(default=None, init=False)  # type: ignore[assignment]

    def __post_init__(self) -> None:
        if self.catalog_db is None:
            self.catalog_db = self.root / _CATALOG_DB_FILE
        if self.pride_dir is None:
            self.pride_dir = self.root / _PRIDE_DIR

    def network_dir(self, network: str) -> UPath:
        """Return the root directory for *network*.

        Parameters
        ----------
        network : str
            Network identifier.

        Returns
        -------
        UPath
            ``<root>/<network>``.
        """
        return self.root / network

    def station_dir(
        self,
        scope: "SFGScope | None" = None,
        *,
        network: str | None = None,
        station: str | None = None,
    ) -> UPath:
        """Return the directory for *(network, station)*. Accepts a scope or keyword args.

        Parameters
        ----------
        scope : SFGScope or None, optional
            Scope providing ``network`` and ``station``, by default ``None``.
        network : str or None, optional
            Network identifier (used when *scope* is ``None``).
        station : str or None, optional
            Station identifier (used when *scope* is ``None``).

        Returns
        -------
        UPath
            ``<root>/<network>/<station>``.

        Raises
        ------
        ValueError
            If both *scope* and keyword args are insufficient to resolve
            ``network`` and ``station``.
        """
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
        """Return the directory for *(network, station, campaign)*. Accepts a scope or keyword args.

        Parameters
        ----------
        scope : SFGScope or None, optional
            Scope providing ``network``, ``station``, and ``campaign``,
            by default ``None``.
        network : str or None, optional
            Network identifier (used when *scope* is ``None``).
        station : str or None, optional
            Station identifier (used when *scope* is ``None``).
        campaign : str or None, optional
            Campaign identifier (used when *scope* is ``None``).

        Returns
        -------
        UPath
            ``<root>/<network>/<station>/<campaign>``.

        Raises
        ------
        ValueError
            If ``network``, ``station``, or ``campaign`` cannot be resolved.
        """
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
        """Return the directory for a specific survey. Requires survey to be set.

        Parameters
        ----------
        scope : SFGScope or None, optional
            Scope providing all four identifiers, by default ``None``.
        network : str or None, optional
            Network identifier (used when *scope* is ``None``).
        station : str or None, optional
            Station identifier (used when *scope* is ``None``).
        campaign : str or None, optional
            Campaign identifier (used when *scope* is ``None``).
        survey : str or None, optional
            Survey identifier (used when *scope* is ``None``).

        Returns
        -------
        UPath
            ``<root>/<network>/<station>/<campaign>/<survey>``.

        Raises
        ------
        ValueError
            If *survey* cannot be resolved from *scope* or keyword args.
        """
        net = scope.network if scope is not None else network
        sta = scope.station if scope is not None else station
        camp = scope.campaign if scope is not None else campaign
        surv = scope.survey if scope is not None else survey
        if surv is None:
            raise ValueError("survey must be set to compute survey_dir")
        return self.campaign_dir(network=net, station=sta, campaign=camp) / surv

    def network(self, network: str) -> NetworkLayout:
        """Return a :class:`NetworkLayout` for *network* (no I/O).

        Parameters
        ----------
        network : str
            Network identifier.

        Returns
        -------
        NetworkLayout
            Layout object for the network directory.
        """
        return NetworkLayout(root=self.network_dir(network))

    def station(
        self,
        scope: "SFGScope | None" = None,
        *,
        network: str | None = None,
        station: str | None = None,
    ) -> StationLayout:
        """Return a :class:`StationLayout` for *(network, station)* (no I/O).

        Parameters
        ----------
        scope : SFGScope or None, optional
            Scope providing ``network`` and ``station``, by default ``None``.
        network : str or None, optional
            Network identifier (used when *scope* is ``None``).
        station : str or None, optional
            Station identifier (used when *scope* is ``None``).

        Returns
        -------
        StationLayout
            Layout object for the station directory.

        Raises
        ------
        ValueError
            If ``network`` or ``station`` cannot be resolved.
        """
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
        """Return the :class:`TileDBLayout` for *(network, station)* (no I/O).

        Parameters
        ----------
        scope : SFGScope or None, optional
            Scope providing ``network`` and ``station``, by default ``None``.
        network : str or None, optional
            Network identifier (used when *scope* is ``None``).
        station : str or None, optional
            Station identifier (used when *scope* is ``None``).

        Returns
        -------
        TileDBLayout
            TileDB layout for the station.

        Raises
        ------
        ValueError
            If ``network`` or ``station`` cannot be resolved.
        """
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
        """Return a :class:`CampaignLayout` for the given scope/kwargs (no I/O).

        Parameters
        ----------
        scope : SFGScope or None, optional
            Scope providing ``network``, ``station``, and ``campaign``,
            by default ``None``.
        network : str or None, optional
            Network identifier (used when *scope* is ``None``).
        station : str or None, optional
            Station identifier (used when *scope* is ``None``).
        campaign : str or None, optional
            Campaign identifier (used when *scope* is ``None``).

        Returns
        -------
        CampaignLayout
            Layout object for the campaign directory.

        Raises
        ------
        ValueError
            If ``network``, ``station``, or ``campaign`` cannot be resolved.
        """
        net = scope.network if scope is not None else network
        sta = scope.station if scope is not None else station
        camp = scope.campaign if scope is not None else campaign
        if net is None or sta is None or camp is None:
            raise ValueError("Provide scope with campaign or (network, station, campaign)")
        return CampaignLayout.for_campaign(
            self.campaign_dir(network=net, station=sta, campaign=camp)
        )

    def survey(
        self,
        scope: "SFGScope | None" = None,
        *,
        network: str | None = None,
        station: str | None = None,
        campaign: str | None = None,
        survey: str | None = None,
    ) -> SurveyLayout:
        """Return a :class:`SurveyLayout` for the given scope/kwargs (no I/O).

        Parameters
        ----------
        scope : SFGScope or None, optional
            Scope providing all four identifiers, by default ``None``.
        network : str or None, optional
            Network identifier (used when *scope* is ``None``).
        station : str or None, optional
            Station identifier (used when *scope* is ``None``).
        campaign : str or None, optional
            Campaign identifier (used when *scope* is ``None``).
        survey : str or None, optional
            Survey identifier (used when *scope* is ``None``).

        Returns
        -------
        SurveyLayout
            Layout object for the survey directory.
        """
        net = scope.network if scope is not None else network
        sta = scope.station if scope is not None else station
        camp = scope.campaign if scope is not None else campaign
        surv = scope.survey if scope is not None else survey
        return SurveyLayout.for_survey(
            self.survey_dir(network=net, station=sta, campaign=camp, survey=surv)
        )

    def garpos(
        self,
        scope: "SFGScope | None" = None,
        *,
        network: str | None = None,
        station: str | None = None,
        campaign: str | None = None,
        survey: str | None = None,
    ) -> GARPOSLayout:
        """Return the :class:`GARPOSLayout` for the survey in scope/kwargs (no I/O).

        Parameters
        ----------
        scope : SFGScope or None, optional
            Scope providing all four identifiers, by default ``None``.
        network : str or None, optional
            Network identifier (used when *scope* is ``None``).
        station : str or None, optional
            Station identifier (used when *scope* is ``None``).
        campaign : str or None, optional
            Campaign identifier (used when *scope* is ``None``).
        survey : str or None, optional
            Survey identifier (used when *scope* is ``None``).

        Returns
        -------
        GARPOSLayout
            GARPOS layout for the survey.
        """
        net = scope.network if scope is not None else network
        sta = scope.station if scope is not None else station
        camp = scope.campaign if scope is not None else campaign
        surv = scope.survey if scope is not None else survey
        return GARPOSLayout.for_survey(
            self.survey_dir(network=net, station=sta, campaign=camp, survey=surv)
        )


# ---------------------------------------------------------------------------
# Reports
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class IngestReport:
    """Outcome of an ingest / discover / download operation.

    Attributes
    ----------
    cataloged : int
        Number of assets successfully added to the catalog.
    downloaded : int
        Number of files successfully downloaded.
    skipped : int
        Number of assets skipped (e.g. already present).
    errors : tuple[str, ...]
        Tuple of error message strings collected during the operation.

    Methods
    -------
    ok
        ``True`` when no errors were recorded.
    __add__(other)
        Combine two reports by summing counts and concatenating errors.
    """

    cataloged: int = 0
    downloaded: int = 0
    skipped: int = 0
    errors: tuple[str, ...] = field(default_factory=tuple)

    @property
    def ok(self) -> bool:
        """True when no errors were recorded during the operation.

        Returns
        -------
        bool
            ``True`` if ``errors`` is empty.
        """
        return not self.errors

    def __add__(self, other: "IngestReport") -> "IngestReport":
        """Combine two reports by summing counts and concatenating errors.

        Parameters
        ----------
        other : IngestReport
            The report to add to this one.

        Returns
        -------
        IngestReport
            A new report with summed ``cataloged``, ``downloaded``, ``skipped``
            and concatenated ``errors``.
        """
        return IngestReport(
            cataloged=self.cataloged + other.cataloged,
            downloaded=self.downloaded + other.downloaded,
            skipped=self.skipped + other.skipped,
            errors=self.errors + other.errors,
        )


@dataclass(frozen=True, slots=True)
class FileInfo:
    """Lightweight FS entry returned by FileStore.list_files.

    Attributes
    ----------
    path : UPath
        Absolute path of the filesystem entry.
    size_bytes : int or None
        Size of the file in bytes, or ``None`` if unknown.
    is_file : bool
        ``True`` if the entry is a regular file; ``False`` for directories.
    """

    path: UPath
    size_bytes: int | None = None
    is_file: bool = True


@dataclass(frozen=True, slots=True)
class ArchiveFile:
    """Lightweight remote file descriptor returned by ArchiveSource.list_files.

    Attributes
    ----------
    url : str
        Full URL of the remote file.
    size_bytes : int or None
        Size of the remote file in bytes, or ``None`` if unknown.

    Methods
    -------
    filename
        Basename of the remote URL.
    """

    url: str
    size_bytes: int | None = None

    @property
    def filename(self) -> str:
        """Basename of the remote URL (e.g. ``"data.csv"`` from ``"\u2026/data.csv``).

        Returns
        -------
        str
            The final path component of :attr:`url`.
        """
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
