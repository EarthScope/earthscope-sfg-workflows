# disk_filestore

`earthscope_sfg_workflows.data_mgmt.filestore.disk_filestore`

Unified filesystem :class:`FileStore` adapter backed by :mod:`fsspec`.

A single :class:`FsspecFileStore` handles both local paths and ``s3://`` URLs.
The filesystem implementation is selected automatically from the URL scheme:

- Plain or ``file://`` paths → local filesystem
- ``s3://bucket/key`` → S3 via :mod:`s3fs`

## class `FsspecFileStore`

Unified :class:`FileStore` adapter using :mod:`fsspec`.

Works transparently with local paths and ``s3://`` URLs.  Pass
*storage_options* to configure S3 credentials (``key``, ``secret``,
``token``, ``endpoint_url``, …).  Omit to use default AWS credential
resolution.

When *root* is omitted the store operates in dispatch mode: each
operation selects the right filesystem from the path scheme at call
time.

Methods
-------
exists(path)
    Return ``True`` if *path* exists on the filesystem.
is_file(path)
    Return ``True`` if *path* is an existing regular file.
is_dir(path)
    Return ``True`` if *path* is an existing directory.
list_files(directory, recursive)
    List files under *directory*; recurse if *recursive*.
write_bytes(path, data)
    Write *data* to *path*, creating parent directories as needed.
read_bytes(path)
    Read and return the raw bytes of *path*.
get_remote(source, target)
    Download a remote file to a local path.
put_remote(source, target)
    Upload a local file to a remote path.
get_remote_batch(sources, target_dir)
    Download multiple remote files to a local directory.
put_remote_batch(sources, target_dir)
    Upload multiple local files to a remote directory.
mkdir(path, parents)
    Create directory *path*; no-op if it already exists or on S3.
remove(path)
    Delete the file at *path*; return ``True`` iff it existed.
get_size(path)
    Return the size in bytes, or ``None`` if *path* is not a file.
close()
    No-op; fsspec manages its own connection pools.

**Methods**

### `FsspecFileStore.close(self) -> 'None'`

No-op; fsspec manages its own connection pools.

### `FsspecFileStore.exists(self, path: 'UPath | str') -> 'bool'`

Return ``True`` if *path* exists on the filesystem (file or directory).

Parameters
----------
path : UPath or str
    The path to test.

Returns
-------
bool
    ``True`` if *path* refers to an existing file or directory.

### `FsspecFileStore.get_remote(self, source: 'str', target: 'UPath') -> 'None'`

Download a remote file to a local path.

Parameters
----------
source : str
    Remote URL of the file to download.
target : UPath
    Local path to write the downloaded file to.

### `FsspecFileStore.get_remote_batch(self, sources: 'list[str]', target_dir: 'UPath') -> 'None'`

Download multiple remote files to a local directory.

Parameters
----------
sources : list[str]
    Remote URLs of the files to download.
target_dir : UPath
    Local directory to download the files into.

### `FsspecFileStore.get_size(self, path: 'UPath | str') -> 'int | None'`

Return the size in bytes, or ``None`` if *path* is not a file.

Parameters
----------
path : UPath or str
    Path of the file to size.

Returns
-------
int or None
    Size in bytes, or ``None`` if *path* does not exist or is not a file.

### `FsspecFileStore.is_dir(self, path: 'UPath | str') -> 'bool'`

Return ``True`` if *path* is an existing directory.

Parameters
----------
path : UPath or str
    The path to test.

Returns
-------
bool
    ``True`` if *path* is a directory.

### `FsspecFileStore.is_file(self, path: 'UPath | str') -> 'bool'`

Return ``True`` if *path* is an existing regular file.

Parameters
----------
path : UPath or str
    The path to test.

Returns
-------
bool
    ``True`` if *path* is a regular file.

### `FsspecFileStore.list_files(self, directory: 'UPath | str', recursive: 'bool' = False) -> 'list[FileInfo]'`

List files under *directory*; recurse if *recursive*. Skips ``._*`` entries.

Parameters
----------
directory : UPath or str
    The directory to list.
recursive : bool, optional
    When ``True``, descend into sub-directories. Default ``False``.

Returns
-------
list[FileInfo]
    :class:`FileInfo` objects for each matching file (directories excluded).

### `FsspecFileStore.mkdir(self, path: 'UPath | str', parents: 'bool' = True) -> 'None'`

Create directory *path*; no-op if it already exists or on S3.

Parameters
----------
path : UPath or str
    Directory path to create.
parents : bool, optional
    When ``True`` (default), create all missing parent directories.

### `FsspecFileStore.put_remote(self, source: 'UPath', target: 'str') -> 'None'`

Upload a local file to a remote path.

Parameters
----------
source : UPath
    Local path of the file to upload.
target : str
    Remote URL to upload the file to.

### `FsspecFileStore.put_remote_batch(self, sources: 'list[UPath]', target_dir: 'str') -> 'None'`

Upload multiple local files to a remote directory.

Parameters
----------
sources : list[UPath]
    Local paths of the files to upload.
target_dir : str
    Remote URL of the destination directory.

### `FsspecFileStore.read_bytes(self, path: 'UPath | str') -> 'bytes'`

Read and return the raw bytes of *path*.

Parameters
----------
path : UPath or str
    Path of the file to read.

Returns
-------
bytes
    Raw file contents.

### `FsspecFileStore.remove(self, path: 'UPath | str') -> 'bool'`

Delete the file at *path* (local or S3).

Parameters
----------
path : UPath or str
    Path of the file to delete.

Returns
-------
bool
    ``True`` if the file existed and was deleted; ``False`` otherwise.

### `FsspecFileStore.write_bytes(self, path: 'UPath | str', data: 'bytes') -> 'None'`

Write *data* to *path*, creating parent directories as needed.

Parameters
----------
path : UPath or str
    Destination path.
data : bytes
    Raw bytes to write.

