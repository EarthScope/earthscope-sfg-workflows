# core

`earthscope_sfg_workflows.data_mgmt.core`

Domain core for the data_mgmt package.
Pure orchestration over the ports defined in ``data_mgmt.ports``. No I/O
happens directly here; everything is delegated to injected adapters. This
module is fully testable with the in-memory adapters in
``data_mgmt.adapters.memory``.

## class `FileManager`

Creates and validates workspace directories on a :class:`FileStore`.

Pass *remote_tree* and *remote_backend* to enable remote sync via
:class:`~earthscope_sfg_workflows.services.sync_service.SyncService`.
Both must be provided together; omitting either disables remote operations.

Attributes
----------
directory_tree : DirectoryTree
    The local directory layout used to derive all workspace paths.
file_backend : FileStorePort
    The file-store implementation that performs disk or S3 I/O.
has_remote : bool
    ``True`` when both *remote_tree* and *remote_backend* are configured.
remote_backend : FileStorePort or None
    The remote :class:`FileStorePort`, or ``None`` when not configured.

Methods
-------
remote_path_for(local_path)
    Return the theoretical remote path for *local_path*, or ``None``.
ensure_workspace()
    Create the workspace root and Pride directory.
ensure_network(network)
    Materialize the network directory and return the layout.
ensure_station(*, network, station)
    Materialize the station directory and return the layout.
ensure_campaign(scope, *, network, station, campaign)
    Materialize the campaign directory tree and return the layout.
ensure_survey(scope, *, network, station, campaign, survey)
    Materialize the survey directory and return the layout.
ensure_garpos_survey(scope, *, network, station, campaign, survey)
    Materialize the GARPOS survey directory tree and return the layout.

**Methods**

### `FileManager.ensure_campaign(self, scope: "'SFGScope | None'" = None, *, network: 'str | None' = None, station: 'str | None' = None, campaign: 'str | None' = None) -> 'CampaignLayout'`

Materialize the campaign directory tree (top-down); return the layout.

Parameters
----------
scope : SFGScope or None, optional
    Scope object providing network, station, and campaign. When given,
    the keyword arguments are ignored.
network : str or None, optional
    Network identifier. Used when *scope* is ``None``.
station : str or None, optional
    Station identifier. Used when *scope* is ``None``.
campaign : str or None, optional
    Campaign identifier. Used when *scope* is ``None``.

Returns
-------
CampaignLayout
    The layout object for the newly created campaign directory tree.

### `FileManager.ensure_garpos_survey(self, scope: "'SFGScope | None'" = None, *, network: 'str | None' = None, station: 'str | None' = None, campaign: 'str | None' = None, survey: 'str | None' = None) -> 'GARPOSLayout'`

Materialize the GARPOS survey directory tree; requires ``scope.survey``.

Parameters
----------
scope : SFGScope or None, optional
    Scope object providing network, station, campaign, and survey.
    When given, the keyword arguments are ignored.
network : str or None, optional
    Network identifier. Used when *scope* is ``None``.
station : str or None, optional
    Station identifier. Used when *scope* is ``None``.
campaign : str or None, optional
    Campaign identifier. Used when *scope* is ``None``.
survey : str or None, optional
    Survey identifier. Used when *scope* is ``None``.

Returns
-------
GARPOSLayout
    The layout object for the newly created GARPOS directory tree.

Raises
------
ValueError
    If no survey identifier can be resolved from *scope* or *survey*.

### `FileManager.ensure_network(self, network: 'str') -> 'NetworkLayout'`

Materialize the network directory; return the layout.

Parameters
----------
network : str
    Network identifier (e.g. ``"NCB1"``).

Returns
-------
NetworkLayout
    The layout object for the newly created network directory.

### `FileManager.ensure_station(self, *, network: 'str', station: 'str') -> 'StationLayout'`

Materialize the station and TileDB array directories; return the layout.

Parameters
----------
network : str
    Network identifier.
station : str
    Station identifier.

Returns
-------
StationLayout
    The layout object for the newly created station directory.

### `FileManager.ensure_survey(self, scope: "'SFGScope | None'" = None, *, network: 'str | None' = None, station: 'str | None' = None, campaign: 'str | None' = None, survey: 'str | None' = None) -> 'SurveyLayout'`

Materialize the survey directory; return the layout.

Parameters
----------
scope : SFGScope or None, optional
    Scope object providing network, station, campaign, and survey.
    When given, the keyword arguments are ignored.
network : str or None, optional
    Network identifier. Used when *scope* is ``None``.
station : str or None, optional
    Station identifier. Used when *scope* is ``None``.
campaign : str or None, optional
    Campaign identifier. Used when *scope* is ``None``.
survey : str or None, optional
    Survey identifier. Used when *scope* is ``None``.

Returns
-------
SurveyLayout
    The layout object for the newly created survey directory.

### `FileManager.ensure_workspace(self) -> 'None'`

Create the workspace root and Pride directory.

### `FileManager.remote_path_for(self, local_path: 'UPath') -> 'UPath | None'`

Return the theoretical remote path for *local_path*, or ``None``.

Maps ``local_tree.root / <relative>`` → ``remote_tree.root / <relative>``.

Parameters
----------
local_path : UPath
    A path under the local directory tree root to translate.

Returns
-------
UPath or None
    The corresponding remote path, or ``None`` when no remote is
    configured or *local_path* is not under the local tree root.


## class `FileTypeDetector`

Classifies filenames to :class:`AssetKind` using regex patterns.

Wraps :data:`DEFAULT_PATTERNS` by default; inject a custom list to
override.

Methods
-------
detect(filename)
    Return the first matching ``AssetKind``, or ``None``.

**Methods**

### `FileTypeDetector.detect(self, filename: 'str') -> 'AssetKind | None'`

Return the first matching :class:`AssetKind`, or ``None``.

Parameters
----------
filename : str
    The filename or path string to classify.

Returns
-------
AssetKind or None
    The first ``AssetKind`` whose pattern matches *filename*,
    or ``None`` if no pattern matches.


## class `LayoutInspector`

File-store-backed introspection of pure :mod:`data_mgmt.model` layouts.

Methods
-------
is_garpos_directory(layout)
    Return ``True`` when the GARPOS observation and settings files are present.
find_rectified_shotdata(layout)
    Return the first ``*_rectified.csv`` file under the GARPOS root, or ``None``.
find_filtered_shotdata(survey_dir)
    Return the first ``*_filtered.csv`` file under *survey_dir*, or ``None``.
is_campaign_directory(layout)
    Return ``True`` when the campaign root, raw, and processed dirs all exist.
list_kind(directory, suffix, contains)
    List files in *directory* optionally filtered by *suffix* and/or *contains*.

**Methods**

### `LayoutInspector.find_filtered_shotdata(self, survey_dir: 'Path') -> 'Path | None'`

Return the first ``*_filtered.csv`` file under *survey_dir*, or ``None``.

Parameters
----------
survey_dir : Path
    Directory to search for a filtered shot-data CSV.

Returns
-------
Path or None
    Absolute path to the first matching file, or ``None`` if none found.

### `LayoutInspector.find_rectified_shotdata(self, layout: 'GARPOSLayout') -> 'Path | None'`

Return the first ``*_rectified.csv`` file under the GARPOS root, or ``None``.

Parameters
----------
layout : GARPOSLayout
    The GARPOS directory layout whose root is searched.

Returns
-------
Path or None
    Absolute path to the first matching file, or ``None`` if none found.

### `LayoutInspector.is_campaign_directory(self, layout: 'CampaignLayout') -> 'bool'`

Return ``True`` when the campaign root, raw, and processed dirs all exist.

Parameters
----------
layout : CampaignLayout
    The campaign directory layout to inspect.

Returns
-------
bool
    ``True`` if the campaign root, raw, and processed directories all exist.

### `LayoutInspector.is_garpos_directory(self, layout: 'GARPOSLayout') -> 'bool'`

Return ``True`` when the observation and settings files are present.

Parameters
----------
layout : GARPOSLayout
    The GARPOS directory layout to inspect.

Returns
-------
bool
    ``True`` if both ``layout.obs_file`` and ``layout.settings_file`` exist.

### `LayoutInspector.list_kind(self, directory: 'Path', suffix: 'str | None' = None, contains: 'str | None' = None) -> 'list[Path]'`

List files in *directory* optionally filtered by *suffix* and/or *contains*.

Parameters
----------
directory : Path
    Directory to list files from (non-recursive).
suffix : str or None, optional
    Only include files whose names end with this suffix.
contains : str or None, optional
    Only include files whose names contain this substring (case-insensitive).

Returns
-------
list[Path]
    Sorted list of matching file paths.

