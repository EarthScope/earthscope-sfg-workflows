"""Public surface of the data_mgmt package.
Ports & adapters layer for managing seafloor-geodesy data assets.
See ``plans/rfc-a-data-mgmt-ports-and-adapters.md``.
"""

from .core import DEFAULT_PATTERNS, FileManager, Ingestor, LayoutInspector
from .model import (
    ArchiveFile,
    AssetEntry,
    AssetKind,
    CampaignLayout,
    SFGScope,
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
    ArchiveSourcePort,
    AssetCatalogPort,
    FileStorePort,
)

__all__ = [
    # model
    "AssetKind",
    "AssetEntry",
    "SFGScope",
    "DirectoryTree",
    "TileDBLayout",
    "CampaignLayout",
    "GARPOSLayout",
    "IngestReport",
    "FileInfo",
    "ArchiveFile",
    # ports
    "AssetCatalogPort",
    "FileStorePort",
    "ArchiveSourcePort",
    "ArchiveError",
    "ArchiveAuthError",
    "ArchiveNotFoundError",
    # core
    "DEFAULT_PATTERNS",
    "FileManager",
    "Ingestor",
    "LayoutInspector",
]
