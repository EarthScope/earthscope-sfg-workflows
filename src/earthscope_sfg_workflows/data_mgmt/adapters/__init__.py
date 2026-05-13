"""Production adapters for the data_mgmt ports.

- :class:`EarthScopeArchive` — EarthScope SDK archive source
- :class:`AssetCatalog` — SQLite-backed asset catalog
- :class:`FsspecFileStore` — unified local and S3 filesystem store

For in-memory test doubles, import from :mod:`test_adapters` instead::

    from earthscope_sfg_workflows.data_mgmt.adapters.test_adapters import (
        FakeArchive, InMemoryAssetStore, InMemoryFileStore
    )
"""

from earthscope_sfg_workflows.data_mgmt.archives.earthscope_archive import EarthScopeArchive
from earthscope_sfg_workflows.data_mgmt.catalog.sql_asset_catalog import AssetCatalog
from earthscope_sfg_workflows.data_mgmt.filestore.disk_filestore import FsspecFileStore

__all__ = [
    "AssetCatalog",
    "EarthScopeArchive",
    "FsspecFileStore",
]
