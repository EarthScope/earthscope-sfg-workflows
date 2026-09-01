"""Public surface of the data_mgmt package.
Ports & adapters layer for managing seafloor-geodesy data assets.
See ``plans/rfc-a-data-mgmt-ports-and-adapters.md``.
"""

from .core import DEFAULT_PATTERNS, FileManager, FileTypeDetector, LayoutInspector
from .model import (
    ArchiveFile,
    AssetEntry,
    AssetKind,
    CampaignLayout,
    DirectoryTree,
    FileInfo,
    GARPOSLayout,
    IngestReport,
    SFGScope,
    TileDBLayout,
)
from .ports import (
    ArchiveAuthError,
    ArchiveError,
    ArchiveNotFoundError,
    ArchiveSourcePort,
    AssetCatalogPort,
    FileStorePort,
)

__all__ = [
    # core
    "DEFAULT_PATTERNS",
    "ArchiveAuthError",
    "ArchiveError",
    "ArchiveFile",
    "ArchiveNotFoundError",
    "ArchiveSourcePort",
    # ports
    "AssetCatalogPort",
    "AssetEntry",
    # model
    "AssetKind",
    "CampaignLayout",
    "DirectoryTree",
    "FileInfo",
    "FileManager",
    "FileStorePort",
    "FileTypeDetector",
    "GARPOSLayout",
    "IngestReport",
    "LayoutInspector",
    "SFGScope",
    "TileDBLayout",
]
