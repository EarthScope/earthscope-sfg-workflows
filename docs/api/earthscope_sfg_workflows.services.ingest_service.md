# ingest_service

`earthscope_sfg_workflows.services.ingest_service`

IngestService — data ingest operations for a StationSession.

## class `IngestService`

Ingest operations (local, remote, download) scoped to a :class:`StationSession`.

Holds the catalog, file-backend, and archive ports directly so all ingest
orchestration lives here without an intermediate helper object.

``override`` is stored at construction and applied to every operation that
supports it (e.g. :meth:`qcpin_tarballs` and :meth:`download_remote`).

Attributes
----------
override : bool
    When ``True``, re-ingest assets that are already cataloged or already
    exist on disk.

Methods
-------
detect(filename)
    Return the first matching ``AssetKind`` for *filename*, or ``None``.
local(source_dir)
    Catalog every recognized file under *source_dir*.
qcpin_tarballs(tarball_dir, override)
    Extract ``.pin``/``.sta`` files from ``.tar.gz`` tarballs and catalog them.
discover_remote()
    Discover canonical EarthScope archive URLs and catalog them.
list_archive_urls(scope)
    Enumerate every archive file URL for the scope without writing to the catalog.
download_remote(kinds, override, rinex_1hz)
    Download cataloged remote assets to local storage.

**Methods**

### `IngestService.detect(self, filename: 'str') -> 'AssetKind | None'`

Return the first matching :class:`AssetKind`, or ``None``.

Parameters
----------
filename : str
    Bare filename (no directory component) to classify.

Returns
-------
AssetKind or None
    The matched asset kind, or ``None`` if no pattern matches.

### `IngestService.discover_remote(self) -> 'IngestReport'`

Discover canonical EarthScope archive URLs and catalog them.

Returns
-------
IngestReport
    Summary of cataloged, skipped, and errored items.

### `IngestService.download_remote(self, kinds: "'list[AssetKind] | None'" = None, *, override: 'bool | None' = None, rinex_1hz: 'bool' = False) -> 'IngestReport'`

Download cataloged remote assets to local storage.

Parameters
----------
kinds : list of AssetKind or None, optional
    Asset kinds to restrict the download to. When ``None`` all
    cataloged remote assets are considered. Default is ``None``.
override : bool or None, optional
    When ``True``, re-download files that already exist locally.
    Defaults to the ``override`` value set at construction.
rinex_1hz : bool, optional
    When ``True``, download only high-rate (1 Hz) RINEX files.
    When ``False``, skip 1 Hz RINEX files. Default is ``False``.

Returns
-------
IngestReport
    Summary of downloaded, skipped, and errored items.

### `IngestService.list_archive_urls(self, scope=None) -> 'list[str]'`

Enumerate every archive file URL for the scope without writing to the catalog.

Parameters
----------
scope : SFGScope or None, optional
    Scope to enumerate. Defaults to the session's active scope when
    ``None``.

Returns
-------
list of str
    All archive file URLs found for the given scope.

### `IngestService.local(self, source_dir: 'Path') -> 'IngestReport'`

Catalog every recognized file under *source_dir*.

Parameters
----------
source_dir : Path
    Root directory to scan recursively for ingestable files.

Returns
-------
IngestReport
    Summary of cataloged, skipped, and errored items.

### `IngestService.qcpin_tarballs(self, tarball_dir: 'Path | None' = None, *, override: 'bool | None' = None) -> 'IngestReport'`

Extract ``.pin``/``.sta`` files from ``.tar.gz`` tarballs and catalog them.

Parameters
----------
tarball_dir : Path or None, optional
    Directory containing ``.tar.gz`` tarballs. When ``None`` the
    campaign layout's ``qc`` directory is used. Default is ``None``.
override : bool or None, optional
    When ``True``, re-extract and re-catalog assets that already exist.
    Defaults to the ``override`` value set at construction.

Returns
-------
IngestReport
    Summary of cataloged, skipped, and errored items.

Raises
------
ValueError
    If *tarball_dir* is ``None`` and no campaign with a layout is active.

