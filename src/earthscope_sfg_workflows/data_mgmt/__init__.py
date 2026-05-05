"""Public surface of the data_mgmt package.

Phase 1 of RFC A — the new ports & adapters layer lives alongside the
existing ``assetcatalog/``, ``directorymgmt/``, and ``ingestion/``
sub-packages. Callers can opt in to the new API without breaking the old
one.

See ``plans/rfc-a-data-mgmt-ports-and-adapters.md``.
"""

from .core import DEFAULT_PATTERNS, FileTypeDetector, Ingestor, TreeBuilder
from .model import (
    ArchiveFile,
    AssetEntry,
    AssetKind,
    CampaignLayout,
    CampaignScope,
    DirectoryTree,
    FileInfo,
    GARPOSLayout,
    IngestReport,
    TileDBLayout,
)
from .ports import (
    ArchiveAuthError,
    ArchiveError,
    ArchiveNotFoundError,
    ArchiveSource,
    AssetStore,
    FileStore,
)

__all__ = [
    # model
    "AssetKind",
    "AssetEntry",
    "CampaignScope",
    "DirectoryTree",
    "TileDBLayout",
    "CampaignLayout",
    "GARPOSLayout",
    "IngestReport",
    "FileInfo",
    "ArchiveFile",
    # ports
    "AssetStore",
    "FileStore",
    "ArchiveSource",
    "ArchiveError",
    "ArchiveAuthError",
    "ArchiveNotFoundError",
    # core
    "FileTypeDetector",
    "DEFAULT_PATTERNS",
    "TreeBuilder",
    "Ingestor",
]
