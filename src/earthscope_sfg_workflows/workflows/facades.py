"""Façade dataclasses for :class:`Workspace` data-mgmt access.

Subclasses of :class:`workflows.base.WorkflowBase` interact with the
data layer **only** through the four façades exposed on
``self.workspace``: ``layout``, ``metadata``, ``assets``, ``ingest``. The
underlying ports (:class:`AssetStore`, :class:`FileStore`,
:class:`ArchiveSource`) stay encapsulated.

Each façade is a small frozen dataclass constructed on every property
access. This is intentional: façades observe the workspace's *current*
scope at construction time and never drift if the caller mutates scope
between calls.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from pathlib import Path

from earthscope_sfg_workflows.data_mgmt.core import (
    Ingestor,
    LayoutInspector,
    TreeBuilder,
)
from earthscope_sfg_workflows.data_mgmt.model import (
    AssetEntry,
    AssetKind,
    CampaignLayout,
    CampaignScope,
    DirectoryTree,
    GARPOSLayout,
    IngestReport,
    TileDBLayout,
)
from earthscope_sfg_workflows.data_mgmt.ports import AssetStore


# ---------------------------------------------------------------------------
# LayoutFacade — paths and materialization
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class LayoutFacade:
    """Path resolution and directory materialization for the active scope.

    Wraps :class:`DirectoryTree` (pure paths), :class:`TreeBuilder`
    (materialization), and :class:`LayoutInspector` (I/O probes).
    """

    _tree: DirectoryTree
    _builder: TreeBuilder
    _inspector: LayoutInspector
    _scope: CampaignScope

    @property
    def root(self) -> Path:
        return self._tree.root

    @property
    def network(self) -> Path:
        return self._tree.network_dir(self._scope.network)

    @property
    def station(self) -> Path:
        return self._tree.station_dir(self._scope)

    def campaign(self) -> CampaignLayout:
        return self._tree.campaign(self._scope)

    def tiledb(self) -> TileDBLayout:
        return self._tree.tiledb(self._scope)

    def garpos_survey(self) -> GARPOSLayout:
        if self._scope.survey is None:
            raise ValueError(
                "scope.survey must be set to access garpos_survey()"
            )
        return self._tree.garpos(self._scope)

    @property
    def survey(self) -> Path:
        if self._scope.survey is None:
            raise ValueError("scope.survey must be set to access survey path")
        return self._tree.survey_dir(self._scope)

    # -- materialization ---------------------------------------------------

    def ensure_workspace(self) -> None:
        self._builder.ensure_workspace()

    def ensure_station(self) -> TileDBLayout:
        return self._builder.ensure_station(self._scope)

    def ensure_campaign(self) -> CampaignLayout:
        return self._builder.ensure_campaign(self._scope)

    def ensure_garpos_survey(self) -> GARPOSLayout:
        if self._scope.survey is None:
            raise ValueError(
                "scope.survey must be set to materialize garpos survey"
            )
        return self._builder.ensure_garpos_survey(self._scope)

    # -- inspection (I/O probes via LayoutInspector) -----------------------

    def is_garpos_directory(self) -> bool:
        return self._inspector.is_garpos_directory(self.garpos_survey())

    def find_rectified_shotdata(self) -> Path | None:
        return self._inspector.find_rectified_shotdata(self.garpos_survey())

    def find_filtered_shotdata(self) -> Path | None:
        return self._inspector.find_filtered_shotdata(self.survey)

    def find_master_xml(self) -> Path | None:
        return self._inspector.find_master_xml(self.campaign())


# ---------------------------------------------------------------------------
# AssetQueryFacade — scoped catalog reads + writeback
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class AssetQueryFacade:
    """Scoped catalog access. The only sanctioned write path for
    :class:`AssetEntry` updates.
    """

    _catalog: AssetStore
    _scope: CampaignScope

    def all(self, kind: AssetKind | None = None) -> list[AssetEntry]:
        """All cataloged assets in the active scope (optionally by kind)."""
        return self._catalog.assets_for(self._scope, kind)

    def by_id(self, asset_id: int) -> AssetEntry | None:
        return self._catalog.by_id(asset_id)

    def by_local_path(self, path: Path) -> list[AssetEntry]:
        return self._catalog.by_local_path(path)

    def count_by_kind(self) -> dict[AssetKind, int]:
        return self._catalog.count_by_kind(self._scope)

    def add(self, asset: AssetEntry) -> AssetEntry:
        """Insert a new asset. Returns the entry with ``id`` populated."""
        return self._catalog.add(asset)

    def update(self, entry: AssetEntry, **changes: object) -> AssetEntry:
        """Persist ``changes`` against ``entry`` and return the new entry.

        Frozen :class:`AssetEntry` is never mutated; this is the *only*
        sanctioned way to change a stored asset. Raises ``LookupError`` if
        no row matched ``entry.id``.
        """
        if entry.id is None:
            raise ValueError(
                "AssetQueryFacade.update requires a persisted entry "
                "(entry.id must not be None)"
            )
        new_entry = replace(entry, **changes)  # type: ignore[arg-type]
        if not self._catalog.update(new_entry):
            raise LookupError(f"No catalog row for asset id={entry.id}")
        return new_entry


# ---------------------------------------------------------------------------
# IngestFacade — discover/ingest/download orchestration
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class IngestFacade:
    """Workflow-friendly orchestration over :class:`Ingestor`."""

    _ingestor: Ingestor
    _scope: CampaignScope

    def local(self, source_dir: Path) -> IngestReport:
        return self._ingestor.ingest_local(self._scope, source_dir)

    def discover_archive(self, archive_url: str) -> IngestReport:
        return self._ingestor.discover_archive(self._scope, archive_url)

    def discover_campaign(self) -> IngestReport:
        return self._ingestor.discover_campaign(self._scope)

    def list_archive_urls(self) -> list[str]:
        """Enumerate every archive file URL for the active campaign scope.

        Side-effect-free counterpart to :meth:`discover_campaign`. Returns
        a flat URL list across raw / metadata / metadata/ctd / RINEX 1Hz /
        RINEX 10Hz.
        """
        return self._ingestor.list_archive_urls(self._scope)

    def download(
        self,
        kinds: list[AssetKind] | None = None,
        dest_dir: Path | None = None,
    ) -> IngestReport:
        return self._ingestor.download(self._scope, kinds, dest_dir)


# ---------------------------------------------------------------------------
# MetadataFacade — lazy-loaded Site/Campaign/Survey metadata
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class MetadataFacade:
    """Read-only view of any metadata previously loaded into the workspace.

    Returns ``None`` until the corresponding ``workspace.load_*_metadata``
    setter has been called. The base class never auto-loads metadata —
    mid-process workflows are expected to call the loaders explicitly.
    """

    site: object | None = None
    campaign: object | None = None
    survey: object | None = None


__all__ = [
    "LayoutFacade",
    "AssetQueryFacade",
    "IngestFacade",
    "MetadataFacade",
]
