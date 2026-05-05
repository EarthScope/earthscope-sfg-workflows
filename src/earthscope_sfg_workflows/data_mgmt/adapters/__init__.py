"""Adapter implementations for the data_mgmt ports.

In-memory adapters always import. Production adapters
(:class:`EarthScopeArchive`, :class:`S3FileStore`) require optional
dependencies and are imported lazily via attribute access so a missing
``earthscope-sdk`` or ``cloudpathlib`` install doesn't break ``import
earthscope_sfg_workflows.data_mgmt``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from .local_fs import LocalFileStore
from .memory import (
    FakeArchive,
    InMemoryAssetStore,
    InMemoryFileStore,
)
from .sql import SqlAssetStore

if TYPE_CHECKING:  # pragma: no cover - typing only
    from .earthscope_archive import EarthScopeArchive
    from .s3_fs import S3FileStore


_LAZY = {
    "EarthScopeArchive": ("earthscope_archive", "EarthScopeArchive"),
    "S3FileStore": ("s3_fs", "S3FileStore"),
}


def __getattr__(name: str) -> Any:
    if name in _LAZY:
        module_name, attr = _LAZY[name]
        import importlib

        module = importlib.import_module(f"{__name__}.{module_name}")
        value = getattr(module, attr)
        globals()[name] = value
        return value
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = [
    "FakeArchive",
    "InMemoryAssetStore",
    "InMemoryFileStore",
    "LocalFileStore",
    "SqlAssetStore",
    "EarthScopeArchive",
    "S3FileStore",
]
