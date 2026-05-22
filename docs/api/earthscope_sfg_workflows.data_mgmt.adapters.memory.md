# memory

`earthscope_sfg_workflows.data_mgmt.adapters.memory`

In-memory adapters for the data_mgmt ports.
Used by the test suite and by callers that want to exercise the data_mgmt
core without touching disk, network, or a database. They implement the same
public contracts as the production adapters and are interchangeable at the
``Workflow`` / ``Ingestor`` construction site.

## class `FakeArchive`

In-memory :class:`ArchiveSource`. Seed with ``url -> bytes`` mappings.

Directory listings are computed by URL prefix (treating any URL whose
parent prefix matches ``directory_url`` as a child).

Methods
-------
seed(url, data)
    Add or overwrite a single archive file.
list_files(directory_url)
    List direct children of *directory_url* (no recursion).
download_file(file_url, dest_path)
    Copy seeded bytes for *file_url* to *dest_path*.
authenticate(profile)
    Mark the archive as authenticated (always succeeds).
close()
    No-op; present for port parity.

**Methods**

### `FakeArchive.authenticate(self, profile: 'str | None' = None) -> 'bool'`

Mark the archive as authenticated (always succeeds for the fake).

Parameters
----------
profile : str or None, optional
    Authentication profile name. Ignored by this implementation.

Returns
-------
bool
    Always ``True``.

### `FakeArchive.close(self) -> 'None'`

No-op for the fake archive; present for port parity.

### `FakeArchive.download_file(self, file_url: 'str', dest_path: 'Path') -> 'None'`

Copy seeded bytes for *file_url* to *dest_path*.

Parameters
----------
file_url : str
    URL key of the file to download.
dest_path : Path
    Local filesystem path to write the bytes to.

Raises
------
ArchiveNotFoundError
    If *file_url* was not seeded in this archive.

### `FakeArchive.list_files(self, directory_url: 'str') -> 'list[ArchiveFile]'`

List direct children of *directory_url* (no recursion).

Parameters
----------
directory_url : str
    URL prefix treated as the parent directory.

Returns
-------
list[ArchiveFile]
    Sorted list of :class:`ArchiveFile` objects for each direct child.

### `FakeArchive.seed(self, url: 'str', data: 'bytes') -> 'None'`

Add or overwrite a single archive file.

Parameters
----------
url : str
    URL key for the file entry.
data : bytes
    Raw bytes to store under *url*.


## class `InMemoryAssetStore`

Thread-safe in-memory implementation of :class:`AssetStore`.

Methods
-------
add(asset)
    Insert *asset*, assigning it a new auto-increment id.
update(asset)
    Replace an existing row by id.
by_id(asset_id)
    Look up an asset by its primary id.
by_local_path(path)
    Return all assets whose ``local_path`` equals *path*.
assets_for(kind, *, network, station, campaign)
    Return assets matching the given scope fields.
delete(scope, kind)
    Delete assets in *scope*, optionally filtered by *kind*.
count_by_kind(scope)
    Return a per-``AssetKind`` row count for assets in *scope*.
distinct_values(field, **filters)
    Return sorted distinct non-null values of *field* matching *filters*.
delete_by_id(asset_id)
    Delete a single asset by id.
assets_to_process(kind, override, *, network, station, campaign)
    Return unprocessed assets, or all assets when *override* is ``True``.
mark_processed_bulk(asset_ids)
    Mark multiple assets as processed in one operation.
add_merge_job(parent_type, child_type, parent_ids)
    Record that a merge job ran.
is_merge_complete(parent_type, child_type, parent_ids)
    Return ``True`` iff a matching merge job has been recorded.
close()
    No-op; present for port parity.

**Methods**

### `InMemoryAssetStore.add(self, asset: 'AssetEntry') -> 'AssetEntry'`

Insert *asset*, assigning it a new auto-increment id.

Parameters
----------
asset : AssetEntry
    The asset to insert. The ``id`` field is ignored and replaced.

Returns
-------
AssetEntry
    A copy of *asset* with the newly assigned ``id``.

### `InMemoryAssetStore.add_merge_job(self, parent_type: 'str', child_type: 'str', parent_ids: 'list[int] | list[str]') -> 'None'`

Record that a merge job for *(parent_type, child_type, parents)* ran.

Parameters
----------
parent_type : str
    Type label for the parent assets (e.g. ``"RINEX2"``).
child_type : str
    Type label for the merged child asset.
parent_ids : list[int] or list[str]
    Identifiers of the parent assets that were merged.

### `InMemoryAssetStore.assets_for(self, kind: "'AssetKind | None'" = None, *, network: 'str | None' = None, station: 'str | None' = None, campaign: 'str | None' = None) -> "list['AssetEntry']"`

Return assets matching the given scope fields, optionally filtered by ``kind``.

``None`` scope fields are treated as wildcards (match any value).

Parameters
----------
kind : AssetKind or None, optional
    Asset kind to filter by. ``None`` matches all kinds.
network : str or None, optional
    Network identifier to filter by. ``None`` matches any network.
station : str or None, optional
    Station identifier to filter by. ``None`` matches any station.
campaign : str or None, optional
    Campaign identifier to filter by. ``None`` matches any campaign.

Returns
-------
list[AssetEntry]
    Assets matching all supplied criteria, sorted by id.

### `InMemoryAssetStore.assets_to_process(self, kind: "'AssetKind | None'" = None, override: 'bool' = False, *, network: 'str | None' = None, station: 'str | None' = None, campaign: 'str | None' = None) -> "list['AssetEntry']"`

Return unprocessed assets, or all assets when *override* is ``True``.

Parameters
----------
kind : AssetKind or None, optional
    Asset kind to filter by.  ``None`` matches all kinds.
override : bool, optional
    When ``True``, return all matching assets regardless of their
    ``is_processed`` flag.
network : str or None, optional
    Network identifier to filter by.  ``None`` matches any network.
station : str or None, optional
    Station identifier to filter by.  ``None`` matches any station.
campaign : str or None, optional
    Campaign identifier to filter by.  ``None`` matches any campaign.

Returns
-------
list[AssetEntry]
    Unprocessed (or all, when *override* is ``True``) matching assets,
    sorted by id.

### `InMemoryAssetStore.by_id(self, asset_id: 'int') -> 'AssetEntry | None'`

Look up an asset by its primary id, or ``None`` if missing.

Parameters
----------
asset_id : int
    The primary id of the asset to retrieve.

Returns
-------
AssetEntry or None
    The matching asset, or ``None`` if no asset with *asset_id* exists.

### `InMemoryAssetStore.by_local_path(self, path: 'Path') -> 'list[AssetEntry]'`

Return all assets whose ``local_path`` equals *path*.

Parameters
----------
path : Path
    The local path to match against stored assets.

Returns
-------
list[AssetEntry]
    All assets whose ``local_path`` attribute equals *path*.

### `InMemoryAssetStore.close(self) -> 'None'`

No-op for the in-memory store; present for port parity.

### `InMemoryAssetStore.count_by_kind(self, scope: 'SFGScope') -> 'dict[AssetKind, int]'`

Return a per-``AssetKind`` row count for assets in *scope*.

Parameters
----------
scope : SFGScope
    The scope whose assets are counted.

Returns
-------
dict[AssetKind, int]
    Mapping from each ``AssetKind`` present in *scope* to its count.

### `InMemoryAssetStore.delete(self, scope: 'SFGScope', kind: 'AssetKind | None' = None) -> 'int'`

Delete assets in *scope*, optionally filtered by *kind*.

Parameters
----------
scope : SFGScope
    Scope whose assets are targeted for deletion.
kind : AssetKind or None, optional
    If given, only assets of this kind are deleted.

Returns
-------
int
    Number of rows deleted.

### `InMemoryAssetStore.delete_by_id(self, asset_id: 'int') -> 'bool'`

Delete a single asset by id.

Parameters
----------
asset_id : int
    The primary id of the asset to delete.

Returns
-------
bool
    ``True`` if the row existed and was deleted; ``False`` otherwise.

### `InMemoryAssetStore.distinct_values(self, field: 'str', **filters: 'str | None') -> 'list[str]'`

Return sorted distinct non-null values of *field* matching *filters*.

Parameters
----------
field : str
    One of ``"network"``, ``"station"``, or ``"campaign"``.
**filters : str or None
    Keyword filters for scope fields. Supported keys are the same set
    as *field*.

Returns
-------
list[str]
    Sorted list of distinct non-null values for *field*.

Raises
------
ValueError
    If *field* is not one of the supported scope fields.

### `InMemoryAssetStore.is_merge_complete(self, parent_type: 'str', child_type: 'str', parent_ids: 'list[int] | list[str]') -> 'bool'`

Return ``True`` iff a matching merge job has been recorded.

Parameters
----------
parent_type : str
    Type label for the parent assets.
child_type : str
    Type label for the merged child asset.
parent_ids : list[int] or list[str]
    Identifiers of the parent assets.

Returns
-------
bool
    ``True`` if a merge job with the given signature has been recorded.

### `InMemoryAssetStore.mark_processed_bulk(self, asset_ids: 'list[int]') -> 'int'`

Mark multiple assets as processed in one operation.

Parameters
----------
asset_ids : list[int]
    Primary ids of assets to mark as processed.

Returns
-------
int
    Number of rows updated.

### `InMemoryAssetStore.update(self, asset: 'AssetEntry') -> 'bool'`

Replace an existing row by id.

Parameters
----------
asset : AssetEntry
    The updated asset. Its ``id`` must match an existing row.

Returns
-------
bool
    ``True`` if the row existed and was replaced; ``False`` otherwise.


## class `InMemoryFileStore`

Tree-shaped in-memory filesystem.

Paths are normalized to absolute via ``Path.resolve(strict=False)`` only
when they're already absolute; otherwise stored as-is. Directories are
tracked separately from files so ``mkdir`` and ``write_bytes`` semantics
line up with a real filesystem.

Methods
-------
exists(path)
    Return ``True`` iff *path* names a known file or directory.
is_file(path)
    Return ``True`` iff *path* names a known file.
is_dir(path)
    Return ``True`` iff *path* names a known directory.
list_files(directory, recursive)
    List files under *directory*; recurse when *recursive* is ``True``.
get_size(path)
    Return the size of the file at *path*, or ``None`` if not a file.
mkdir(path, parents)
    Create directory *path*; create parent directories iff *parents*.
read_bytes(path)
    Return the bytes stored at *path*.
write_bytes(path, data)
    Write *data* to *path*, auto-creating parent directories.
get_remote(source, target)
    Copy seeded bytes for *source* to the real filesystem at *target*.
put_remote(source, target)
    Read *source* from the real filesystem and seed it under *target*.
remove(path)
    Remove the file at *path*.
close()
    No-op; present for port parity.

**Methods**

### `InMemoryFileStore.close(self) -> 'None'`

No-op for the in-memory store; present for port parity.

### `InMemoryFileStore.exists(self, path: 'UPath') -> 'bool'`

Return ``True`` iff *path* names a known file or directory.

Parameters
----------
path : UPath
    The path to test.

Returns
-------
bool
    ``True`` if *path* is recorded as either a file or a directory.

### `InMemoryFileStore.get_remote(self, source: 'str', target: 'UPath') -> 'None'`

Copy seeded bytes for *source* to the real filesystem at *target*.

Parameters
----------
source : str
    URL key of the seeded file to retrieve.
target : UPath
    Local filesystem path to write the bytes to.

Raises
------
FileNotFoundError
    If *source* has not been seeded in this store.

### `InMemoryFileStore.get_size(self, path: 'UPath') -> 'int | None'`

Return the size of the file at *path*, or ``None`` if not a file.

Parameters
----------
path : UPath
    Path of the file to size.

Returns
-------
int or None
    Size in bytes, or ``None`` if *path* is not a known file.

### `InMemoryFileStore.is_dir(self, path: 'UPath') -> 'bool'`

Return ``True`` iff *path* names a known directory.

Parameters
----------
path : UPath
    The path to test.

Returns
-------
bool
    ``True`` if *path* has been created via :meth:`mkdir`.

### `InMemoryFileStore.is_file(self, path: 'UPath') -> 'bool'`

Return ``True`` iff *path* names a known file.

Parameters
----------
path : UPath
    The path to test.

Returns
-------
bool
    ``True`` if *path* has been written to this store.

### `InMemoryFileStore.list_files(self, directory: 'UPath', recursive: 'bool' = False) -> 'list[FileInfo]'`

List files under *directory*; recurse when *recursive* is ``True``.

Parameters
----------
directory : UPath
    The directory to list.
recursive : bool, optional
    When ``True``, descend into sub-directories. Default ``False``.

Returns
-------
list[FileInfo]
    Sorted list of :class:`FileInfo` objects for each matching file.

### `InMemoryFileStore.mkdir(self, path: 'UPath', parents: 'bool' = True) -> 'None'`

Create directory *path*; create parent directories iff *parents*.

Parameters
----------
path : UPath
    The directory path to create.
parents : bool, optional
    When ``True`` (default), create all missing parent directories.

### `InMemoryFileStore.put_remote(self, source: 'UPath', target: 'str') -> 'None'`

Read *source* from the real filesystem and seed it under *target*.

Parameters
----------
source : UPath
    Local filesystem path to read bytes from.
target : str
    URL key under which the bytes are stored in this fake store.

### `InMemoryFileStore.read_bytes(self, path: 'UPath') -> 'bytes'`

Return the bytes stored at *path*.

Parameters
----------
path : UPath
    Path of the file to read.

Returns
-------
bytes
    Raw bytes previously written to *path*.

Raises
------
FileNotFoundError
    If *path* has not been written to this store.

### `InMemoryFileStore.remove(self, path: 'UPath') -> 'bool'`

Remove the file at *path*.

Parameters
----------
path : UPath
    Path of the file to remove.

Returns
-------
bool
    ``True`` if the file existed and was removed; ``False`` otherwise.

### `InMemoryFileStore.write_bytes(self, path: 'UPath', data: 'bytes') -> 'None'`

Write *data* to *path*, auto-creating parent directories.

Parameters
----------
path : UPath
    Destination path.
data : bytes
    Raw bytes to store.

