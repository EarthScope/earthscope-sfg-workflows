"""Public surface of the data_mgmt adapters package.

Production adapters:
- :class:`EarthScopeArchive` — EarthScope SDK archive source
- :class:`AssetCatalog` — SQLite-backed asset catalog
- :class:`LocalFileStore` — local filesystem store

Test / in-memory adapters are re-exported from ``test_adapters``.
"""

from earthscope_sfg_workflows.data_mgmt.adapters.memory import (
    FakeArchive,
    InMemoryAssetStore,
    InMemoryFileStore,
)
from earthscope_sfg_workflows.data_mgmt.archives.earthscope_archive import EarthScopeArchive
from earthscope_sfg_workflows.data_mgmt.catalog.sql_asset_catalog import AssetCatalog

__all__ = [
    "AssetCatalog",
    "EarthScopeArchive",
    "FakeArchive",
    "InMemoryAssetStore",
    "InMemoryFileStore",
]
