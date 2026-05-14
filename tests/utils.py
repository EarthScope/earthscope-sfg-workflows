"""Test utilities for earthscope-sfg-workflows.

All production code depends on three ports — AssetStore, FileStore, and
ArchiveSource. This module provides in-memory doubles for each so tests can
construct any domain object (Workspace, …) without touching disk, a database,
or a network.

Typical usage
-------------
    from tests.utils import make_workspace, FakeArchive

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
from earthscope_sfg_workflows.data_mgmt.model import SFGScope
from earthscope_sfg_workflows.workflows.session import StationSession as Workspace


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
) -> SFGScope:
    """Return a ``CampaignScope`` with sensible defaults for tests."""
    return SFGScope(network=network, station=station, campaign=campaign)


__all__ = [
    # in-memory doubles
    "InMemoryAssetStore",
    "InMemoryFileStore",
    "FakeArchive",
    # factories
    "make_workspace",
    "make_scope",
]
