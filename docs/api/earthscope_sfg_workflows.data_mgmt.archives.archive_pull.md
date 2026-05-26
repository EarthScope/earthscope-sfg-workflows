# archive_pull

`earthscope_sfg_workflows.data_mgmt.archives.archive_pull`

HTTP and S3 helpers for downloading files from the EarthScope seafloor archive.

## `download_file_from_archive(url, dest_dir='./', profile=None, show_details: bool = True) -> None`

Download a file from the public archive using the EarthScope SDK.

Parameters
----------
url : str
    The URL of the file to download.
dest_dir : str, optional
    The directory to save the downloaded file, by default "./".
profile : str, optional
    The profile to use for authentication (e.g., 'dev'), by default None (prod).
show_details : bool, optional
    Log the file details, by default True.

## `download_file_list_from_archive(file_urls: list, dest_dir='./files') -> None`

Download a list of files from the public archive.

Parameters
----------
file_urls : list
    A list of URLs to download.
dest_dir : str, optional
    The directory to save the downloaded files, by default "./files".

## `generate_archive_campaign_metadata_url(network, station, campaign)`

Generate a URL for campaign metadata in the public archive.

Parameters
----------
network : str
    The network name.
station : str
    The station name.
campaign : str
    The campaign name (e.g YYYY_A_WVGL).

Returns
-------
str
    The URL of the campaign directory.

## `generate_archive_campaign_url(network, station, campaign)`

Generate a URL for a campaign in the public archive.

Parameters
----------
network : str
    The network name.
station : str
    The station name.
campaign : str
    The campaign name (e.g YYYY_A_WVGL).

Returns
-------
str
    The URL of the campaign directory.

## `generate_archive_rinex_url(network, station, campaign, hz)`

Generate a URL for campaign RINEX files in the public archive.

Parameters
----------
network : str
    The network name.
station : str
    The station name.
campaign : str
    The campaign name (e.g YYYY_A_WVGL).
hz : str
    The RINEX frequency (e.g., '1Hz', '10Hz').

Returns
-------
str
    The URL of the campaign RINEX directory.

## `generate_archive_site_json_url(network, station, profile: str = None) -> str`

Generate a URL for the site JSON file in the public archive.

Parameters
----------
network : str
    The network name.
station : str
    The station name.
profile : str, optional
    The profile to use for the archive (e.g., 'prod', 'dev'), by default None (prod).

Returns
-------
str
    The URL of the site JSON file.

## `generate_archive_vessel_json_url(vessel_code, profile: str = None) -> str`

Generate a URL for the vessel JSON file in the public archive.

Parameters
----------
vessel_code : str
    The vessel code.
profile : str, optional
    The profile to use for the archive (e.g., 'prod', 'dev'), by default None (prod).

Returns
-------
str
    The URL of the vessel JSON file.

## `get_campaign_file_dict(url: str) -> dict`

Get a dictionary of campaign files by type.

Parameters
----------
url : str
    Location in archive.

Returns
-------
dict
    Dictionary of file locations by type.

## `list_campaign_files(network: str, station: str, campaign: str) -> list`

Returns a list of files for a given campaign in the archive.

Optionally displays a summary of file counts by type.

Parameters
----------
network : str
    Network name.
station : str
    Station name.
campaign : str
    Campaign name.

Returns
-------
list
    List of file locations in archive.

## `list_campaign_files_by_type(network: str, station: str, campaign: str, show_logs: bool = True) -> dict`

List campaign files by type.

Parameters
----------
network : str
    Network name.
station : str
    Station name.
campaign : str
    Campaign name.
show_logs : bool, optional
    Whether to show logs containing file counts, by default True.

Returns
-------
dict
    Dictionary of file locations by type.

## `list_file_counts_by_type(file_list: list, url: str | None = None, show_logs=True) -> dict`

Counts files by type, and builds a dictionary.

Parameters
----------
file_list : list
    List of files from the archive.
url : str, optional
    URL of where in the archive the files were found, by default None.
show_logs : bool, optional
    Whether to show logs containing file counts, by default True.

Returns
-------
dict
    Dictionary of files by type.

## `list_files_from_archive(url) -> list`

List files from the public archive using urllib.

Parameters
----------
url : str
    The URL of the directory to list. This must be a directory that
    contains files.

Returns
-------
list
    A list of files.

## `list_s3_directory_files(bucket_name: str, prefix: str) -> list[str]`

Returns a list all files in a given S3 bucket.

This is under a specified prefix and return absolute S3 paths.

Parameters
----------
bucket_name : str
    Name of the S3 bucket.
prefix : str
    S3 prefix (folder path) to filter the files.

Returns
-------
List[str]
    List of absolute S3 file paths.

## `load_site_metadata(network: str, station: str, profile: str = None) -> earthscope_sfg_tools.datamodels.metadata.earthscope.site.Site`

Load the site metadata from the s3 archive.

Note
----
To access the dev archive, you must:
1. set up ~/.earthscope/config.toml
2. run `es login --profile dev`
3. be on the earthscope vpn

Parameters
----------
network : str
    The network name.
station : str
    The station name.
profile : str, optional
    The profile to use for the archive (e.g., 'prod', 'dev'), by default
    None (prod).

Returns
-------
Site
    An instance of the Site class with the metadata loaded.

## `load_vessel_metadata(vessel_code: str, profile: str = None, local_path: pathlib.Path | str = None) -> earthscope_sfg_tools.datamodels.metadata.earthscope.vessel.Vessel`

Load the vessel metadata from the s3 archive.

Note
----
To access the dev archive, you must:
1. set up ~/.earthscope/config.toml
2. run `es login --profile dev`
3. be on the earthscope vpn

Parameters
----------
vessel_code : str
    The vessel code.
profile : str, optional
    The profile to use for the archive (e.g., 'prod', 'dev'), by default
    None (prod).
local_path : Path | str, optional
    Local path to a JSON file containing vessel metadata. If provided,
    this will be used instead of downloading from the archive.

Returns
-------
Vessel
    An instance of the Vessel class with the metadata loaded.

## `retrieve_token(profile=None)`

Retrieve or generate a token for the public archive.

This uses the EarthScope SDK (new method).

Parameters
----------
profile : str, optional
    The profile to use for authentication (e.g., 'dev'), by default None (prod).

Returns
-------
str
    The access token.
