# sql_asset_catalog

`earthscope_sfg_workflows.data_mgmt.catalog.sql_asset_catalog`

SQLite-backed :class:`AssetStore` adapter.
Defines the SQLAlchemy ORM tables (``Assets`` / ``MergeJobs``) that back the
on-disk asset catalog and exposes them through the :class:`AssetStore` port.

The asset catalog schema has no ``survey`` column; ``CampaignScope.survey``
is intentionally not persisted here. Survey-scoped metadata lives at the
workflow layer.

## class `AssetCatalog`

SQLAlchemy-backed asset catalog. Works with any SQLAlchemy URL.

Backed by SQLite locally or Postgres/RDS in cloud. Pass an engine
directly or use the factory classmethods :meth:`sqlite` and
:meth:`from_url`.

Attributes
----------
_engine : Engine
    The underlying SQLAlchemy engine (not for direct use by callers).

Methods
-------
sqlite(db_path, create_schema)
    Build a catalog backed by a local SQLite file.
from_url(url, create_schema)
    Build a catalog from a SQLAlchemy database URL.
add(asset)
    Insert an asset and return it with its assigned id.
update(asset)
    Update an existing asset row by id.
mark_processed_bulk(asset_ids)
    Mark multiple assets as processed in a single statement.
by_id(asset_id)
    Look up an asset by primary key.
by_local_path(path)
    Return all assets whose local_path matches a given path.
assets_for(kind, network, station, campaign)
    Return assets matching the given scope fields.
assets_to_process(kind, override, network, station, campaign)
    Return unprocessed assets optionally filtered by kind.
delete(kind, network, station, campaign)
    Delete assets matching the given scope and optional kind.
delete_by_id(asset_id)
    Delete a single asset by id.
count_by_kind(network, station, campaign)
    Return per-kind row counts for the specified scope.
distinct_values(field, **filters)
    Return sorted distinct non-null values of a scope field.
add_merge_job(parent_type, child_type, parent_ids)
    Persist a record that a merge job ran.
is_merge_complete(parent_type, child_type, parent_ids)
    Check whether a merge job has previously been recorded.
close()
    Dispose of the underlying SQLAlchemy engine.

**Methods**

### `AssetCatalog.add(self, asset: 'AssetEntry') -> 'AssetEntry'`

Insert an asset and return it with its assigned id.

Parameters
----------
asset : AssetEntry
    The asset to persist. ``asset.id`` is ignored; a new id is assigned.

Returns
-------
AssetEntry
    A copy of ``asset`` with ``id`` populated from the database.

Raises
------
IntegrityError
    If ``local_path`` or ``remote_path`` already exists (UNIQUE
    constraint violation). Callers may treat this as a no-op skip;
    the existing matching entry is returned instead.

### `AssetCatalog.add_merge_job(self, parent_type: 'str', child_type: 'str', parent_ids: 'list[int] | list[str]') -> 'None'`

Persist a record that a merge job ran.

Parameters
----------
parent_type : str
    Asset kind string for the parent (input) assets.
child_type : str
    Asset kind string for the child (output) asset.
parent_ids : list[int] or list[str]
    Ids of the parent assets consumed in this merge.

### `AssetCatalog.assets_for(self, kind: 'AssetKind | None' = None, *, network: 'str | None' = None, station: 'str | None' = None, campaign: 'str | None' = None) -> 'list[AssetEntry]'`

Return assets matching the given scope fields.

All scope fields default to ``None``; ``None`` is treated as an exact
``NULL`` match rather than a wildcard.  To query across all campaigns
for a station, use :meth:`distinct_values` first.

Parameters
----------
kind : AssetKind or None, optional
    Filter to a specific asset kind. ``None`` returns all kinds.
network : str or None, optional
    Network to filter on (exact match).
station : str or None, optional
    Station to filter on (exact match).
campaign : str or None, optional
    Campaign to filter on (exact match).

Returns
-------
list[AssetEntry]
    All matching entries, ordered by id.

### `AssetCatalog.assets_to_process(self, kind: 'AssetKind | None' = None, override: 'bool' = False, *, network: 'str | None' = None, station: 'str | None' = None, campaign: 'str | None' = None) -> 'list[AssetEntry]'`

Return unprocessed assets optionally filtered by kind.

Parameters
----------
kind : AssetKind or None, optional
    Filter to a specific asset kind. ``None`` returns all kinds.
override : bool, optional
    When ``True``, returns all matching assets regardless of processed
    status (equivalent to calling :meth:`assets_for`).
network : str or None, optional
    Network to filter on.
station : str or None, optional
    Station to filter on.
campaign : str or None, optional
    Campaign to filter on.

Returns
-------
list[AssetEntry]
    Unprocessed (or all, when ``override=True``) matching entries.

### `AssetCatalog.by_id(self, asset_id: 'int') -> 'AssetEntry | None'`

Look up an asset by primary key.

Parameters
----------
asset_id : int
    The primary key to look up.

Returns
-------
AssetEntry or None
    The matching entry, or ``None`` if no row has that id.

### `AssetCatalog.by_local_path(self, path: 'Path') -> 'list[AssetEntry]'`

Return all assets whose local_path matches a given path.

Parameters
----------
path : Path
    Filesystem path to match against.

Returns
-------
list[AssetEntry]
    All entries with ``local_path == str(path)``.

### `AssetCatalog.close(self) -> 'None'`

Dispose of the underlying SQLAlchemy engine, releasing all connections.

### `AssetCatalog.count_by_kind(self, *, network: 'str | None' = None, station: 'str | None' = None, campaign: 'str | None' = None) -> 'dict[AssetKind, int]'`

Return per-kind row counts for assets in the specified scope.

Parameters
----------
network : str or None, optional
    Network to filter on.
station : str or None, optional
    Station to filter on.
campaign : str or None, optional
    Campaign to filter on.

Returns
-------
dict[AssetKind, int]
    Mapping of :class:`AssetKind` to row count. Only kinds with at
    least one row are included. Unknown legacy kind values are skipped.

### `AssetCatalog.delete(self, kind: 'AssetKind | None' = None, *, network: 'str | None' = None, station: 'str | None' = None, campaign: 'str | None' = None) -> 'int'`

Delete assets matching the given scope fields and optional kind.

Parameters
----------
kind : AssetKind or None, optional
    Restrict deletion to this asset kind. ``None`` deletes all kinds
    in the given scope.
network : str or None, optional
    Network to match.
station : str or None, optional
    Station to match.
campaign : str or None, optional
    Campaign to match.

Returns
-------
int
    Number of rows deleted.

### `AssetCatalog.delete_by_id(self, asset_id: 'int') -> 'bool'`

Delete a single asset by primary key.

Parameters
----------
asset_id : int
    Primary key of the asset to delete.

Returns
-------
bool
    ``True`` if a row was deleted; ``False`` if no matching row existed.

### `AssetCatalog.distinct_values(self, field: 'str', **filters: 'str | None') -> 'list[str]'`

Return sorted distinct non-null values of a scope field.

Parameters
----------
field : str
    Column to query. Must be one of ``"network"``, ``"station"``,
    or ``"campaign"``.
**filters : str or None
    Optional equality filters using the same supported column names.

Returns
-------
list[str]
    Sorted list of distinct non-null values.

Raises
------
ValueError
    If ``field`` or any filter key is not a supported column name.

### `AssetCatalog.is_merge_complete(self, parent_type: 'str', child_type: 'str', parent_ids: 'list[int] | list[str]') -> 'bool'`

Check whether a merge job for these inputs has previously been recorded.

Parameters
----------
parent_type : str
    Asset kind string for the parent assets.
child_type : str
    Asset kind string for the child asset.
parent_ids : list[int] or list[str]
    Ids of the parent assets to check.

Returns
-------
bool
    ``True`` if a matching merge record exists; ``False`` otherwise.

### `AssetCatalog.mark_processed_bulk(self, asset_ids: 'list[int]') -> 'int'`

Mark multiple assets as processed in a single statement.

Parameters
----------
asset_ids : list[int]
    Primary keys of assets to mark as processed.

Returns
-------
int
    Number of rows updated.

### `AssetCatalog.update(self, asset: 'AssetEntry') -> 'bool'`

Update an existing asset row by id.

Parameters
----------
asset : AssetEntry
    Asset containing updated field values. Must have ``id`` set.

Returns
-------
bool
    ``True`` if a row was modified; ``False`` if the id was ``None``
    or no matching row existed.


## class `Assets`

SQLAlchemy ORM model for the ``assets`` table.

## class `MergeJobs`

SQLAlchemy ORM model for the ``mergejobs`` table.
