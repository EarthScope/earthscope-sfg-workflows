# workspace

`earthscope_sfg_workflows.workflows.workspace`

Workspace — multi-session orchestration object.

## class `Workspace`

Low-level multi-session container.

Owns the three infrastructure ports (catalog, files, archive) and manages
a pool of :class:`StationSession` instances keyed by ``(network, station)``.

**Typical users should prefer** :class:`~earthscope_sfg_workflows.workflows.workflow_handler.WorkflowHandler`,
which wraps ``Workspace`` with a higher-level API suited for notebooks and
scripts.  Use ``Workspace`` directly when you need:

* Fine-grained control over port construction (e.g. injecting test fakes).
* Simultaneous access to multiple ``(network, station)`` sessions.
* Building a custom orchestrator on top of the session pool.

Call :meth:`set_active` to make a session active; :attr:`session` returns it.

Attributes
----------
root : Path
    Absolute root directory of this workspace.
s3_sync_bucket : str or None
    S3 bucket name for remote sync, read from the ``S3_SYNC_BUCKET`` env var.
catalog : AssetCatalogPort
    The asset catalog port backing all sessions in this workspace.
session : StationSession
    The currently active session.

Methods
-------
set_active(network, station, campaign)
    Get-or-create a session for ``(network, station)`` and make it active.
get_session(network, station)
    Return a session without changing the active session.
list_networks()
    Return all distinct network names in the catalog.
list_stations(network)
    Return all distinct station names for a network in the catalog.
list_campaigns(network, station)
    Return all distinct campaign names for a network/station pair.
query_assets(network, station, kind)
    Return catalog entries matching optional filters.

**Methods**

### `Workspace.get_session(self, network: 'str', station: 'str') -> 'StationSession'`

Return the session for *(network, station)* without changing the active session.

Useful when you need to inspect or configure a non-active session.
Creates the session on first access.

Parameters
----------
network : str
    Network identifier.
station : str
    Station identifier within the network.

Returns
-------
StationSession
    Session for the given ``(network, station)`` pair.

### `Workspace.list_campaigns(self, network: 'str', station: 'str') -> 'list[str]'`

Return all distinct campaign names for *(network, station)* in the catalog.

Parameters
----------
network : str
    Network identifier.
station : str
    Station identifier within the network.

Returns
-------
list of str
    Distinct campaign identifiers in the asset catalog for the given
    network/station pair.

### `Workspace.list_networks(self) -> 'list[str]'`

Return all distinct network names recorded in the catalog.

Returns
-------
list of str
    Distinct network identifiers in the asset catalog.

### `Workspace.list_stations(self, network: 'str') -> 'list[str]'`

Return all distinct station names for *network* in the catalog.

Parameters
----------
network : str
    Network identifier to filter by.

Returns
-------
list of str
    Distinct station identifiers in the asset catalog for the given network.

### `Workspace.query_assets(self, *, network: 'str | None' = None, station: 'str | None' = None, kind: "'AssetKind | None'" = None) -> "'list[AssetEntry]'"`

Return catalog entries matching the optional *network*, *station*, and *kind* filters.

Parameters
----------
network : str or None, optional
    Filter by network identifier.
station : str or None, optional
    Filter by station identifier.
kind : AssetKind or None, optional
    Filter by asset kind (e.g. raw, processed).

Returns
-------
list of AssetEntry
    Catalog entries that match all provided filters.

### `Workspace.set_active(self, network: 'str', station: 'str', campaign: 'str | None' = None) -> 'StationSession'`

Get-or-create the session for *(network, station)*, make it active, and return it.

If a session for this pair already exists it is reused — TileDB arrays
are only opened once.  If ``campaign`` is provided and differs from the
session's current campaign, :meth:`StationSession.set_campaign` is called
to materialise campaign directories and update scope.

After this call, :attr:`session` returns the same session.

Parameters
----------
network : str
    Network identifier.
station : str
    Station identifier within the network.
campaign : str or None, optional
    If provided and different from the session's current campaign,
    :meth:`StationSession.set_campaign` is called automatically.

Returns
-------
StationSession
    The active session for the given ``(network, station)`` pair.

