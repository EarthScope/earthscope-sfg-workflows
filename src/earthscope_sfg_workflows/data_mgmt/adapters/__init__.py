"""Adapter implementations for the data_mgmt ports.
In-memory adapters always import. The production archive adapter
(:class:`EarthScopeArchive`) requires the optional ``earthscope-sdk``
dependency and is imported lazily via attribute access so a missing install
doesn't break ``import earthscope_sfg_workflows.data_mgmt``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from .local_fs import FsspecFileStore, LocalFileStore, S3FileStore
from .memory import (
    FakeArchive,
    InMemoryAssetStore,
    InMemoryFileStore,
)
from ..assetcatalog.sql import AssetCatalog

if TYPE_CHECKING:  # pragma: no cover
    from earthscope_sfg_workflows.data_mgmt.archives.earthscope_archive import EarthScopeArchive


_LAZY = {
    "EarthScopeArchive": (
        "earthscope_sfg_workflows.data_mgmt.archives.earthscope_archive",
        "EarthScopeArchive",
    ),
}


def __getattr__(name: str) -> Any:
    if name in _LAZY:
        module_path, attr = _LAZY[name]
        import importlib

        module = importlib.import_module(module_path)
        value = getattr(module, attr)
        globals()[name] = value
        return value
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = [
    "FakeArchive",
    "FsspecFileStore",
    "InMemoryAssetStore",
    "InMemoryFileStore",
    "LocalFileStore",
    "S3FileStore",
    "AssetCatalog",
    "EarthScopeArchive",
]
