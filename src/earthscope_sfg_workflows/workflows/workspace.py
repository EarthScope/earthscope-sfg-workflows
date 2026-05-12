"""Backwards-compatibility shim for the former :class:`Workspace` class.

.. deprecated::
    Import :class:`~earthscope_sfg_workflows.workflows.session.StationSession` directly.
    This module will be removed in a future release.

``Workspace`` is an alias for :class:`StationSession`.  All production
code should import from :mod:`.session` directly.  This module is retained
so that existing ``from .workspace import Workspace`` statements continue to
work without modification.

``_build_default_workspace`` now returns a :class:`_Ports` bundle (not a
session, since a session requires network/station/campaign at construction).
"""

from __future__ import annotations

import os
import warnings
from dataclasses import dataclass
from pathlib import Path

warnings.warn(
    "earthscope_sfg_workflows.workflows.workspace is deprecated. "
    "Import StationSession from earthscope_sfg_workflows.workflows.session directly.",
    DeprecationWarning,
    stacklevel=2,
)

from earthscope_sfg_workflows.data_mgmt.ports import (
    ArchiveSourcePort,
    AssetCatalogPort,
    FileStorePort,
)

from .session import StationSession, TileDBRegistry  # noqa: F401  (re-export)

# ---------------------------------------------------------------------------
# Type alias — "Workspace" is now just a CampaignSession.
# ---------------------------------------------------------------------------

Workspace = StationSession

# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------


def _to_asset_kind(t: object):
    """Translate a user-facing AssetType enum/str to :class:`~earthscope_sfg_workflows.data_mgmt.model.AssetKind`."""
    from earthscope_sfg_workflows.config.file_config import AssetType
    from earthscope_sfg_workflows.data_mgmt.model import AssetKind

    if isinstance(t, AssetType):
        return AssetKind(t.value)
    return AssetKind(str(t).lower())


# ---------------------------------------------------------------------------
# Ports bundle (replaces old Workspace as the pre-session container)
# ---------------------------------------------------------------------------


@dataclass
class _Ports:
    """Lightweight container holding the three injected ports and root dir."""

    root: Path
    catalog: AssetCatalogPort
    files: FileStorePort
    archive: ArchiveSourcePort


def _build_ports(directory: "Path | str") -> _Ports:
    """Construct the production set of port adapters for *directory*."""
    from earthscope_sfg_workflows.data_mgmt.filestore.disk_filestore import (
        LocalFileStore,
        S3FileStore,
    )
    from earthscope_sfg_workflows.data_mgmt.archives.earthscope_archive import EarthScopeArchive
    from earthscope_sfg_workflows.data_mgmt.catalog.sql_asset_catalog import AssetCatalog

    is_s3 = str(directory).startswith("s3://")
    if is_s3:
        files: FileStorePort = S3FileStore()
        catalog_db = Path(os.environ.get("MAIN_DIRECTORY", ".")) / "catalog.sqlite"
        root: Path = Path(str(directory))
    else:
        files = LocalFileStore()
        root = Path(directory)
        root.mkdir(parents=True, exist_ok=True)
        catalog_db = root / "catalog.sqlite"

    catalog = AssetCatalog.sqlite(catalog_db)
    archive = EarthScopeArchive()
    return _Ports(root=root, catalog=catalog, files=files, archive=archive)


def _build_default_workspace(directory: "Path | str") -> _Ports:
    """Legacy name for :func:`_build_ports`.  Returns a :class:`_Ports` bundle."""
    return _build_ports(directory)


__all__ = [
    "StationSession",
    "TileDBRegistry",
    "Workspace",
    "_Ports",
    "_build_default_workspace",
    "_build_ports",
    "_to_asset_kind",
]
