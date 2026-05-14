"""Test utilities for earthscope-sfg-workflows.

All production code depends on three ports — AssetStore, FileStore, and
ArchiveSource. This module provides in-memory doubles for each so tests can
construct any domain object (StationSession, Workspace, …) without touching
disk, a database, or a network.

Typical usage
-------------
    from tests.utils import make_session, make_workspace, FakeArchive

    def test_session():
        sess = make_session(network="CAS", station="NCB1", campaign="2026_A")
        ...

    def test_workspace():
        ws = make_workspace()
        ws.set_active("CAS", "NCB1")
        ...
"""

from __future__ import annotations

from pathlib import Path

from earthscope_sfg_workflows.data_mgmt.adapters.test_adapters import (
    FakeArchive,
    InMemoryAssetStore,
    InMemoryFileStore,
)
from earthscope_sfg_workflows.data_mgmt.core import FileManager
from earthscope_sfg_workflows.data_mgmt.model import DirectoryTree, SFGScope
from earthscope_sfg_workflows.data_mgmt.ports import (
    ArchiveSourcePort,
    AssetCatalogPort,
    FileStorePort,
)
from earthscope_sfg_workflows.workflows.session import StationSession
from earthscope_sfg_workflows.workflows.workspace import Workspace


def make_session(
    network: str,
    station: str,
    *,
    root: str | Path | None = None,
    campaign: str | None = None,
    survey: str | None = None,
    catalog: AssetCatalogPort | None = None,
    archive: ArchiveSourcePort | None = None,
) -> StationSession:
    """Build a ``StationSession`` backed by in-memory adapters (no disk/network).

    Suitable for unit tests. Optional *campaign* / *survey* set the
    corresponding slots after construction.
    """
    root_path = Path(root) if root else Path("/ws")
    in_catalog = catalog or InMemoryAssetStore()
    in_files = InMemoryFileStore()
    in_archive = archive or FakeArchive()
    file_manager = FileManager(DirectoryTree(root=root_path), in_files)

    # Bypass the real __init__ network/station materialisation side effects
    # by calling __new__ and wiring manually.
    self = StationSession.__new__(StationSession)
    object.__setattr__(self, "_catalog", in_catalog)
    object.__setattr__(self, "_file_manager", file_manager)
    object.__setattr__(self, "_archive", in_archive)

    network_layout = file_manager.ensure_network(network)
    station_layout = file_manager.ensure_station(network=network, station=station)

    object.__setattr__(self, "_scope", SFGScope(network=network, station=station))
    object.__setattr__(self, "_network_layout", network_layout)
    object.__setattr__(self, "_station_layout", station_layout)
    object.__setattr__(self, "_site", None)
    object.__setattr__(self, "_campaign_layout", None)
    object.__setattr__(self, "_survey_layout", None)
    object.__setattr__(self, "_campaign_meta", None)
    object.__setattr__(self, "_ingest_service", None)
    object.__setattr__(self, "_pipeline_service", None)
    object.__setattr__(self, "_sync_service", None)

    if campaign is not None:
        self.set_campaign(campaign)
    if survey is not None:
        self.set_survey(survey)

    return self


def make_workspace(
    *,
    root: str | Path | None = None,
    catalog: AssetCatalogPort | None = None,
    files: FileStorePort | None = None,
    archive: ArchiveSourcePort | None = None,
) -> Workspace:
    """Build a ``Workspace`` orchestrator backed by in-memory adapters."""
    return Workspace(
        root_dir=root or Path("/ws"),
        catalog=catalog or InMemoryAssetStore(),
        files=files or InMemoryFileStore(),
        archive=archive or FakeArchive(),
    )


def make_scope(
    network: str = "NET",
    station: str = "STA",
    campaign: str = "2024_A",
) -> SFGScope:
    """Return an ``SFGScope`` with sensible defaults for tests."""
    return SFGScope(network=network, station=station, campaign=campaign)


__all__ = [
    # in-memory doubles
    "InMemoryAssetStore",
    "InMemoryFileStore",
    "FakeArchive",
    # factories
    "make_session",
    "make_workspace",
    "make_scope",
]
