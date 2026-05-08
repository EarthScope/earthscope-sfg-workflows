"""Test utilities for earthscope-sfg-workflows.

All production code depends on three ports — AssetStore, FileStore, and
ArchiveSource. This module provides in-memory doubles for each so tests can
construct any domain object (Ingestor, Workspace, …) without touching disk,
a database, or a network.

Typical usage
-------------
    from tests.utils import make_ingestor, make_workspace, FakeArchive

    def test_something():
        ing, catalog, files, archive = make_ingestor(root="/ws")
        archive.seed("https://arc/a/foo.24o", b"data")
        report = ing.discover_archive(scope, "https://arc/a")
        assert report.cataloged == 1

    def test_workspace():
        ws = make_workspace(network="CAS", station="NCB1", campaign="2026_A")
        ...
"""

from __future__ import annotations

from pathlib import Path

from earthscope_sfg_workflows.data_mgmt.adapters.test_adapters import (
    FakeArchive,
    InMemoryAssetStore,
    InMemoryFileStore,
)
from earthscope_sfg_workflows.data_mgmt.core import (
    FileManager,
    FileTypeDetector,
    Ingestor,
)
from earthscope_sfg_workflows.data_mgmt.model import CampaignScope, DirectoryTree
from earthscope_sfg_workflows.workflows.workspace import Workspace


def make_ingestor(
    root: Path | str = "/workspace",
    archive: FakeArchive | None = None,
) -> tuple[Ingestor, InMemoryAssetStore, InMemoryFileStore, FakeArchive]:
    """Build an ``Ingestor`` wired to in-memory adapters.

    Returns the ingestor plus the three backing doubles so callers can
    inspect or seed them directly.

    Args:
        root: Virtual workspace root (default ``/workspace``).
        archive: Optional pre-seeded ``FakeArchive``; a fresh one is created
            if omitted.

    Returns:
        ``(ingestor, catalog, files, archive)``
    """
    catalog = InMemoryAssetStore()
    files = InMemoryFileStore()
    arc = archive or FakeArchive()
    tree = DirectoryTree(root=Path(root))
    ingestor = Ingestor(
        catalog=catalog,
        file_manager=FileManager(tree, files),
        archive=arc,
        detector=FileTypeDetector(),
    )
    return ingestor, catalog, files, arc


def make_workspace(
    root: Path | str = "/workspace",
    network: str | None = None,
    station: str | None = None,
    campaign: str | None = None,
    survey: str | None = None,
) -> Workspace:
    """Build a ``Workspace`` backed entirely by in-memory adapters.

    Thin wrapper around ``Workspace.for_test`` exposed here so tests only
    need to import from one place.
    """
    return Workspace.for_test(
        root=root,
        network=network,
        station=station,
        campaign=campaign,
        survey=survey,
    )


def make_scope(
    network: str = "NET",
    station: str = "STA",
    campaign: str = "2024_A",
) -> CampaignScope:
    """Return a ``CampaignScope`` with sensible defaults for tests."""
    return CampaignScope(network=network, station=station, campaign=campaign)


__all__ = [
    # in-memory doubles
    "InMemoryAssetStore",
    "InMemoryFileStore",
    "FakeArchive",
    # factories
    "make_ingestor",
    "make_workspace",
    "make_scope",
]
