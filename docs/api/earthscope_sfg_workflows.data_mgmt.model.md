# model

`earthscope_sfg_workflows.data_mgmt.model`

Pure data layer for the data_mgmt package.
This module defines immutable, I/O-free dataclasses describing the workspace
directory hierarchy and asset metadata. All path computation is pure; nothing
here touches the filesystem, a database, or the network.

See ``plans/rfc-a-data-mgmt-ports-and-adapters.md`` for the full design.

## class `ArchiveFile`

Lightweight remote file descriptor returned by ArchiveSource.list_files.

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

**Fields**

| Name | Type | Description |
|---|---|---|
| `url` | `str` |  |
| `size_bytes` | `int \| None` |  |

## class `AssetEntry`

A single asset in the catalog. Pure data, no I/O methods.

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

**Fields**

| Name | Type | Description |
|---|---|---|
| `kind` | `AssetKind` |  |
| `scope` | `SFGScope` |  |
| `id` | `int \| None` |  |
| `local_path` | `UPath \| None` |  |
| `remote_path` | `str \| None` |  |
| `remote_type` | `str \| None` |  |
| `is_processed` | `bool` |  |
| `parent_id` | `int \| None` |  |
| `timestamp_data_start` | `datetime \| None` |  |
| `timestamp_data_end` | `datetime \| None` |  |
| `timestamp_created` | `datetime \| None` |  |

**Methods**

### `AssetEntry.is_addressable(self) -> 'bool'`

At least one of local_path / remote_path is set.

Returns
-------
bool
    ``True`` if ``local_path`` or ``remote_path`` is not ``None``.

### `AssetEntry.with_id(self, asset_id: 'int') -> "'AssetEntry'"`

Return a copy of this entry with *asset_id* set.

Parameters
----------
asset_id : int
    The catalog primary key to assign.

Returns
-------
AssetEntry
    A new :class:`AssetEntry` with ``id`` set to *asset_id*.

### `AssetEntry.with_local_path(self, path: 'UPath') -> "'AssetEntry'"`

Return a copy of this entry with *local_path* set.

Parameters
----------
path : UPath
    The local filesystem path to assign.

Returns
-------
AssetEntry
    A new :class:`AssetEntry` with ``local_path`` set to *path*.


## class `AssetKind`

Single source of truth for every asset type tracked in the catalog.

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

## class `CampaignLayout`

All paths for a campaign directory. Pure.

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

**Fields**

| Name | Type | Description |
|---|---|---|
| `root` | `UPath` |  |
| `raw` | `UPath` |  |
| `processed` | `UPath` |  |
| `intermediate` | `UPath` |  |
| `logs` | `UPath` |  |
| `qc` | `UPath` |  |
| `metadata_dir` | `UPath` |  |
| `metadata_file` | `UPath` |  |
| `svp_file` | `UPath` |  |
| `rinex` | `UPath \| None` |  |

**Methods**

### `CampaignLayout.for_campaign(campaign_dir: 'UPath') -> "'CampaignLayout'"`

Build a CampaignLayout rooted at *campaign_dir*.

Parameters
----------
campaign_dir : UPath
    Root directory of the campaign.

Returns
-------
CampaignLayout
    A fully populated :class:`CampaignLayout` for the campaign.


## class `DirectoryTree`

Workspace-rooted view of the hierarchy. Pure path math, no I/O.

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

**Fields**

| Name | Type | Description |
|---|---|---|
| `root` | `UPath` |  |
| `catalog_db` | `UPath` |  |
| `pride_dir` | `UPath` |  |

**Methods**

### `DirectoryTree.campaign(self, scope: "'SFGScope | None'" = None, *, network: 'str | None' = None, station: 'str | None' = None, campaign: 'str | None' = None) -> 'CampaignLayout'`

Return a :class:`CampaignLayout` for the given scope/kwargs (no I/O).

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

### `DirectoryTree.campaign_dir(self, scope: "'SFGScope | None'" = None, *, network: 'str | None' = None, station: 'str | None' = None, campaign: 'str | None' = None) -> 'UPath'`

Return the directory for *(network, station, campaign)*. Accepts a scope or keyword args.

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

### `DirectoryTree.garpos(self, scope: "'SFGScope | None'" = None, *, network: 'str | None' = None, station: 'str | None' = None, campaign: 'str | None' = None, survey: 'str | None' = None) -> 'GARPOSLayout'`

Return the :class:`GARPOSLayout` for the survey in scope/kwargs (no I/O).

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

### `DirectoryTree.network(self, network: 'str') -> 'NetworkLayout'`

Return a :class:`NetworkLayout` for *network* (no I/O).

Parameters
----------
network : str
    Network identifier.

Returns
-------
NetworkLayout
    Layout object for the network directory.

### `DirectoryTree.network_dir(self, network: 'str') -> 'UPath'`

Return the root directory for *network*.

Parameters
----------
network : str
    Network identifier.

Returns
-------
UPath
    ``<root>/<network>``.

### `DirectoryTree.station(self, scope: "'SFGScope | None'" = None, *, network: 'str | None' = None, station: 'str | None' = None) -> 'StationLayout'`

Return a :class:`StationLayout` for *(network, station)* (no I/O).

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

### `DirectoryTree.station_dir(self, scope: "'SFGScope | None'" = None, *, network: 'str | None' = None, station: 'str | None' = None) -> 'UPath'`

Return the directory for *(network, station)*. Accepts a scope or keyword args.

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

### `DirectoryTree.survey(self, scope: "'SFGScope | None'" = None, *, network: 'str | None' = None, station: 'str | None' = None, campaign: 'str | None' = None, survey: 'str | None' = None) -> 'SurveyLayout'`

Return a :class:`SurveyLayout` for the given scope/kwargs (no I/O).

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

### `DirectoryTree.survey_dir(self, scope: "'SFGScope | None'" = None, *, network: 'str | None' = None, station: 'str | None' = None, campaign: 'str | None' = None, survey: 'str | None' = None) -> 'UPath'`

Return the directory for a specific survey. Requires survey to be set.

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

### `DirectoryTree.tiledb(self, scope: "'SFGScope | None'" = None, *, network: 'str | None' = None, station: 'str | None' = None) -> 'TileDBLayout'`

Return the :class:`TileDBLayout` for *(network, station)* (no I/O).

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


## class `FileInfo`

Lightweight FS entry returned by FileStore.list_files.

Attributes
----------
path : UPath
    Absolute path of the filesystem entry.
size_bytes : int or None
    Size of the file in bytes, or ``None`` if unknown.
is_file : bool
    ``True`` if the entry is a regular file; ``False`` for directories.

**Fields**

| Name | Type | Description |
|---|---|---|
| `path` | `UPath` |  |
| `size_bytes` | `int \| None` |  |
| `is_file` | `bool` |  |

## class `GARPOSLayout`

All paths for a GARPOS survey directory. Pure.

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

**Fields**

| Name | Type | Description |
|---|---|---|
| `root` | `UPath` |  |
| `logs` | `UPath` |  |
| `obs_file` | `UPath` |  |
| `settings_file` | `UPath` |  |
| `svp_file` | `UPath` |  |
| `results` | `UPath` |  |

**Methods**

### `GARPOSLayout.for_survey(survey_dir: 'UPath') -> "'GARPOSLayout'"`

Build a GARPOSLayout rooted at *survey_dir*/GARPOS.

Parameters
----------
survey_dir : UPath
    Root directory of the survey.

Returns
-------
GARPOSLayout
    A fully populated :class:`GARPOSLayout` for the survey.


## class `IngestReport`

Outcome of an ingest / discover / download operation.

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

**Fields**

| Name | Type | Description |
|---|---|---|
| `cataloged` | `int` |  |
| `downloaded` | `int` |  |
| `skipped` | `int` |  |
| `errors` | `tuple[str, ...]` |  |

## class `NetworkLayout`

All paths for a network directory. Pure.

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

**Fields**

| Name | Type | Description |
|---|---|---|
| `root` | `UPath` |  |
| `stations` | `dict[str, StationLayout \| None]` |  |

## class `SFGScope`

Mutable scope cursor for a network/station/campaign[/survey] context.

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

**Fields**

| Name | Type | Description |
|---|---|---|
| `network` | `str` |  |
| `station` | `str` |  |
| `campaign` | `str \| None` |  |
| `survey` | `str \| None` |  |

**Methods**

### `SFGScope.with_survey(self, survey: 'str') -> "'SFGScope'"`

Return a new scope with ``survey`` set.

Parameters
----------
survey : str
    Survey identifier to attach.

Returns
-------
SFGScope
    A copy of this scope with ``survey`` populated.


## class `StationLayout`

All paths for a station directory. Pure.

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

**Fields**

| Name | Type | Description |
|---|---|---|
| `root` | `UPath` |  |
| `campaigns` | `dict[str, CampaignLayout \| None]` |  |
| `metadata` | `UPath \| None` |  |
| `tiledb` | `TileDBLayout \| None` |  |

## class `SurveyLayout`

All paths for a survey directory. Pure.

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

**Fields**

| Name | Type | Description |
|---|---|---|
| `root` | `UPath` |  |
| `metadata_file` | `UPath` |  |
| `garpos` | `'GARPOSLayout'` |  |
| `shotdata` | `UPath \| None` |  |
| `kinpositiondata` | `UPath \| None` |  |
| `imupositiondata` | `UPath \| None` |  |

**Methods**

### `SurveyLayout.for_survey(survey_dir: 'UPath', survey_id: 'str | None' = None, survey_type: 'str | None' = None) -> "'SurveyLayout'"`

Build a SurveyLayout rooted at *survey_dir*.

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


## class `TileDBLayout`

All TileDB array paths for a station. Pure path math.

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

**Fields**

| Name | Type | Description |
|---|---|---|
| `root` | `UPath` |  |
| `acoustic` | `UPath` |  |
| `kin_position` | `UPath` |  |
| `imu_position` | `UPath` |  |
| `shotdata` | `UPath` |  |
| `shotdata_pre` | `UPath` |  |
| `gnss_obs` | `UPath` |  |
| `gnss_obs_secondary` | `UPath` |  |
| `qc_shotdata` | `UPath` |  |
| `qc_shotdata_pre` | `UPath` |  |
| `qc_kin_position` | `UPath` |  |
| `qc_gnss_obs` | `UPath` |  |

**Methods**

### `TileDBLayout.for_station(station_dir: 'UPath') -> "'TileDBLayout'"`

Build a TileDBLayout rooted under *station_dir*/TileDB.

Parameters
----------
station_dir : UPath
    Root directory of the station.

Returns
-------
TileDBLayout
    A fully populated :class:`TileDBLayout` for the station.

