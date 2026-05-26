# session

`earthscope_sfg_workflows.workflows.session`

CampaignSession — scoped, eager-initialising unit of work.

Network and station are **fixed** at construction and cannot be mutated;
they are the stable identity that anchors TileDB arrays (which are
station-scoped and built once in ``__init__``).  Campaign and survey are
mutable slots that can be set at any time via :meth:`set_campaign` and
:meth:`set_survey`.

Methods that require campaign or survey are guarded by the
:func:`_require_campaign` and :func:`_require_survey` decorators defined in
this module.  Workflow-layer methods (on :class:`WorkflowBase` subclasses)
are guarded by the decorators in :mod:`earthscope_sfg_workflows.workflows.base`.

## class `ScopeLevel`

Ordered hierarchy of scope slots. Used by :meth:`StationSession._reset_below`.

Attributes
----------
CAMPAIGN : str
    Represents the campaign scope level with value ``"campaign"``.
SURVEY : str
    Represents the survey scope level with value ``"survey"``.

## class `StationSession`

Scoped unit of work anchored to a fixed network/station pair.

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

**Methods**

### `StationSession.configure_remote(self, remote_root: 'str | None') -> 'None'`

Point the session's file manager at a remote S3 root for push/pull operations.

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

### `StationSession.ensure_campaign(self) -> 'CampaignLayout'`

Materialise campaign directories and return the layout.

Returns
-------
CampaignLayout
    Directory layout for the current campaign.

Raises
------
ValueError
    If a campaign has not been set.

### `StationSession.list_campaigns(self) -> 'list[str]'`

Return campaign names seen in the catalog for this network/station.

Returns
-------
list of str
    Distinct campaign identifiers recorded in the asset catalog for this
    network/station pair, in insertion order.

### `StationSession.load_site_metadata(self, site: 'object') -> 'None'`

Load pre-fetched site metadata into this session (test helper).

Parameters
----------
site : object
    Site metadata object to inject, replacing any previously loaded metadata.

Returns
-------
None

### `StationSession.prepare_garpos_survey(self) -> "'GARPOSLayout'"`

Materialise GARPOS survey directories on disk and return the layout.

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

### `StationSession.set_campaign(self, campaign_id: 'str') -> 'CampaignLayout'`

Set the active campaign, materialise its directories, and clear the survey slot.

Parameters
----------
campaign_id : str
    Campaign identifier to activate.

Returns
-------
CampaignLayout
    The directory layout for the newly activated campaign.

### `StationSession.set_survey(self, survey_id: 'str') -> 'None'`

Set the active survey and materialise its directory.

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


## class `TileDBRegistry`

Open TileDB array handles for a single station, held for the session lifetime.

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

**Fields**

| Name | Type | Description |
|---|---|---|
| `acoustic` | `'TDBAcousticArray'` |  |
| `kin_position` | `'TDBKinPositionArray'` |  |
| `imu_position` | `'TDBIMUPositionArray'` |  |
| `shotdata` | `'TDBShotDataArray'` |  |
| `shotdata_pre` | `'TDBShotDataArray'` |  |
| `gnss_obs` | `'TDBGNSSObsArray'` |  |
| `gnss_obs_secondary` | `'TDBGNSSObsArray'` |  |
