# earthscope_archive

`earthscope_sfg_workflows.data_mgmt.archives.earthscope_archive`

EarthScope archive :class:`ArchiveSource` adapter.

Wraps the token retrieval, directory listing (via ``?list&uris=1``), and
authenticated download logic behind the :class:`~ports.ArchiveSourcePort`.
Network and auth failures are translated to :class:`~ports.ArchiveError`
subclasses so callers can handle them uniformly.

## class `EarthScopeArchive`

Production :class:`~ports.ArchiveSourcePort` backed by the EarthScope public archive.

Tokens are acquired lazily on first use and refreshed via the EarthScope
SDK. A *profile* (e.g. ``"dev"``) selects a non-production settings
profile; omit to use the SDK's active default.

Attributes
----------
ARCHIVE_PREFIX : str
    Base URL for the EarthScope seafloor archive
    (``"https://data.earthscope.org/archive/seafloor"``).

Methods
-------
authenticate(profile)
    Acquire or refresh an access token from the EarthScope SDK.
list_files(directory_url)
    List files at *directory_url* via the ``?list&uris=1`` endpoint.
download_file(file_url, dest_path)
    Download *file_url* to *dest_path*.
download_to_dir(file_url, dest_dir)
    Download *file_url* into *dest_dir* using the URL's basename.
canonical_campaign_urls(scope)
    Return the four canonical archive directory URLs for a campaign.
list_campaign_archive_urls(scope)
    Enumerate every archive file URL for a campaign.
campaign_raw_url(scope)
    Return the raw-data directory URL for a campaign.
campaign_metadata_url(scope)
    Return the metadata directory URL for a campaign.
campaign_rinex_url(scope, hz)
    Compose a RINEX directory URL for a given sample rate.
site_metadata_url(scope)
    Return the archive URL for a station's site metadata JSON.
vessel_json_url(vessel_code)
    Return the archive URL for a vessel metadata JSON file.
load_vessel_metadata(vessel_code)
    Load a :class:`Vessel` from the archive.
load_site_metadata(scope, network, station)
    Load a :class:`Site` from the archive, populating per-campaign vessels.
close()
    Discard the cached access token.

**Methods**

### `EarthScopeArchive.authenticate(self, profile: 'str' = 'default') -> 'bool'`

Acquire or refresh an access token from the EarthScope SDK.

Parameters
----------
profile : str, optional
    EarthScope SDK settings profile to use for authentication, by
    default ``"default"``.

Returns
-------
bool
    ``True`` if a valid token was obtained.

Raises
------
ArchiveAuthError
    If the SDK login flow fails or returns an empty token.

### `EarthScopeArchive.campaign_metadata_url(self, scope: 'SFGScope') -> 'str'`

Return the archive URL for the metadata directory of a campaign.

Parameters
----------
scope : SFGScope
    Network, station, and campaign identifiers.

Returns
-------
str
    URL of the campaign's ``metadata`` directory on the archive.

### `EarthScopeArchive.campaign_raw_url(self, scope: 'SFGScope') -> 'str'`

Return the archive URL for the raw data directory of a campaign.

Parameters
----------
scope : SFGScope
    Network, station, and campaign identifiers.

Returns
-------
str
    URL of the campaign's ``raw`` directory on the archive.

### `EarthScopeArchive.campaign_rinex_url(self, scope: 'SFGScope', hz: 'str') -> 'str'`

Compose a RINEX directory URL for the given sample rate.

Parameters
----------
scope : SFGScope
    Network, station, and campaign identifiers.
hz : str
    Sample-rate label; either ``"1Hz"`` or ``"10Hz"``.

Returns
-------
str
    URL of the campaign's ``rinex_<hz>`` directory on the archive.

### `EarthScopeArchive.canonical_campaign_urls(self, scope: 'SFGScope') -> 'tuple[str, str, str, str]'`

Return the four canonical archive directory URLs for a campaign.

Parameters
----------
scope : SFGScope
    Network, station, and campaign identifiers for the target campaign.

Returns
-------
tuple of str
    A four-element tuple ``(raw_url, metadata_url, rinex_1hz_url,
    rinex_10hz_url)`` in the order consumed by
    ``Ingestor.discover_campaign``.

### `EarthScopeArchive.close(self) -> 'None'`

Discard the cached access token so subsequent requests re-authenticate.

### `EarthScopeArchive.download_file(self, file_url: 'str', dest_path: 'Path') -> 'None'`

Download *file_url* to *dest_path*, creating parent directories as needed.

Parameters
----------
file_url : str
    URL of the file to download.
dest_path : Path
    Local destination path for the downloaded file.

Raises
------
ArchiveAuthError
    If the server returns HTTP 401.
ArchiveNotFoundError
    If the server returns HTTP 404.
ArchiveError
    For any other HTTP error or network failure.

### `EarthScopeArchive.download_to_dir(self, file_url: 'str', dest_dir: 'Path') -> 'Path'`

Download *file_url* into *dest_dir* using the URL's basename.

Parameters
----------
file_url : str
    URL of the file to download.
dest_dir : Path
    Local directory to place the downloaded file in. Created if it
    does not already exist.

Returns
-------
Path
    Absolute path of the downloaded file inside *dest_dir*.

### `EarthScopeArchive.list_campaign_archive_urls(self, scope: 'SFGScope') -> 'list[str]'`

Enumerate every archive file URL for a campaign.

Lists each directory from :meth:`canonical_campaign_urls`, plus the
legacy ``metadata/ctd`` subdirectory, and returns the concatenated
list of file URLs. Missing directories are silently skipped — partial
campaigns (e.g. RINEX 10Hz absent) are common.

This is the side-effect-free equivalent of
``Ingestor.discover_campaign``: it returns URLs without writing
anything to the catalog.

Parameters
----------
scope : SFGScope
    Network, station, and campaign identifiers for the target campaign.

Returns
-------
list of str
    All file URLs found across the campaign's archive directories.

### `EarthScopeArchive.list_files(self, directory_url: 'str') -> 'list[ArchiveFile]'`

List files at *directory_url* via the archive's ``?list&uris=1`` endpoint.

Parameters
----------
directory_url : str
    URL of the archive directory to enumerate.

Returns
-------
list of ArchiveFile
    One :class:`~model.ArchiveFile` per file found at *directory_url*.

Raises
------
ArchiveAuthError
    If the server returns HTTP 401.
ArchiveNotFoundError
    If the server returns HTTP 404.
ArchiveError
    For any other HTTP error or network failure.

### `EarthScopeArchive.load_site_metadata(self, scope: 'SFGScope' = None, *, network: 'str' = None, station: 'str' = None) -> 'Site'`

Load a :class:`Site` from the archive, populating per-campaign vessels.

Downloads the station's site JSON, parses it, then attempts to attach
a :class:`Vessel` object to each campaign. Vessel fetch failures are
silently ignored (``campaign.vessel`` is set to ``None``).

Parameters
----------
scope : SFGScope or None, optional
    Network and station identifiers. Either *scope* or both *network*
    and *station* must be provided.
network : str or None, optional
    Network code, used when *scope* is not provided.
station : str or None, optional
    Station code, used when *scope* is not provided.

Returns
-------
Site
    Parsed site metadata with vessel objects attached where available.

Raises
------
ValueError
    If neither *scope* nor both *network* and *station* are provided.

### `EarthScopeArchive.load_vessel_metadata(self, vessel_code: 'str') -> 'Vessel'`

Load a :class:`Vessel` from the archive.

Downloads the vessel JSON to the current directory, parses it, then
deletes the temporary file.

Parameters
----------
vessel_code : str
    Short vessel identifier (e.g. ``"R_ENDEAVOR"``).

Returns
-------
Vessel
    Parsed vessel metadata object.

### `EarthScopeArchive.site_metadata_url(self, scope: 'SFGScope') -> 'str'`

Return the archive URL for a station's site metadata JSON.

Parameters
----------
scope : SFGScope
    Network and station identifiers (campaign is not used).

Returns
-------
str
    URL of the station's ``<station>.json`` metadata file.

### `EarthScopeArchive.vessel_json_url(self, vessel_code: 'str') -> 'str'`

Return the archive URL for a vessel metadata JSON file.

Parameters
----------
vessel_code : str
    Short vessel identifier (e.g. ``"R_ENDEAVOR"``).

Returns
-------
str
    URL of the vessel's ``<vessel_code>.json`` metadata file.


## `canonical_campaign_urls(scope: 'SFGScope') -> 'tuple[str, str, str, str]'`

Return ``(raw_url, metadata_url, rinex_1hz_url, rinex_10hz_url)`` for a campaign.

## `list_campaign_archive_urls(archive: 'object', scope: 'SFGScope') -> 'list[str]'`

List every file URL for a campaign without writing to any catalog.

Lists raw, metadata, metadata/ctd, rinex_1Hz, and rinex_10Hz; skips
missing directories silently.
