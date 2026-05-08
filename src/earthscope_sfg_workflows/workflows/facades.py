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
    FileManager,
    Ingestor,
    LayoutInspector,
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
from earthscope_sfg_workflows.data_mgmt.ports import AssetCatalogPort


# ---------------------------------------------------------------------------
# LayoutFacade — paths and materialization
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class LayoutFacade:
    """Path resolution and directory materialization for the active scope.
    Wraps :class:`DirectoryTree` (pure paths), :class:`FileManager`
    (materialization), and :class:`LayoutInspector` (I/O probes).
    """

    _tree: DirectoryTree
    _builder: FileManager
    _inspector: LayoutInspector
    _scope: CampaignScope

    @property
    def root(self) -> Path:
        """Workspace root directory."""
        return self._tree.root

    @property
    def network(self) -> Path:
        """Path to the active network directory."""
        return self._tree.network_dir(self._scope.network)

    @property
    def station(self) -> Path:
        """Path to the active station directory."""
        return self._tree.station_dir(self._scope)

    def campaign(self) -> CampaignLayout:
        """Return the `CampaignLayout` for the active campaign."""
        return self._tree.campaign(self._scope)

    def tiledb(self) -> TileDBLayout:
        """Return the `TileDBLayout` for the active station."""
        return self._tree.tiledb(self._scope)

    def garpos_survey(self) -> GARPOSLayout:
        """Return the `GARPOSLayout` for the active survey; requires `scope.survey`."""
        if self._scope.survey is None:
            raise ValueError("scope.survey must be set to access garpos_survey()")
        return self._tree.garpos(self._scope)

    @property
    def survey(self) -> Path:
        """Path to the active survey directory; requires `scope.survey`."""
        if self._scope.survey is None:
            raise ValueError("scope.survey must be set to access survey path")
        return self._tree.survey_dir(self._scope)

    # -- materialization ---------------------------------------------------

    def ensure_workspace(self) -> None:
        """Create the workspace root and shared subdirectories on disk."""
        self._builder.ensure_workspace()

    def ensure_station(self) -> TileDBLayout:
        """Materialize the station/TileDB layout on disk and return it."""
        return self._builder.ensure_station(self._scope)

    def ensure_campaign(self) -> CampaignLayout:
        """Materialize the active campaign layout on disk and return it."""
        return self._builder.ensure_campaign(self._scope)

    def ensure_garpos_survey(self) -> GARPOSLayout:
        """Materialize the GARPOS survey layout on disk; requires `scope.survey`."""
        if self._scope.survey is None:
            raise ValueError("scope.survey must be set to materialize garpos survey")
        return self._builder.ensure_garpos_survey(self._scope)

    # -- inspection (I/O probes via LayoutInspector) -----------------------

    def is_garpos_directory(self) -> bool:
        """Return True iff the active survey directory looks like a GARPOS layout."""
        return self._inspector.is_garpos_directory(self.garpos_survey())

    def find_rectified_shotdata(self) -> Path | None:
        """Locate rectified shotdata under the active GARPOS survey, if any."""
        return self._inspector.find_rectified_shotdata(self.garpos_survey())

    def find_filtered_shotdata(self) -> Path | None:
        """Locate filtered shotdata under the active survey, if any."""
        return self._inspector.find_filtered_shotdata(self.survey)

    # -- discovery: list children of station / campaign --------------------

    def list_campaigns(self) -> list[str]:
        """Names of campaign directories under the active station (year-prefixed)."""
        import re

        station_dir = self.station
        try:
            return sorted(
                p.name for p in station_dir.iterdir() if p.is_dir() and re.match(r"^\d{4}", p.name)
            )
        except (OSError, AttributeError):
            return []

    def list_surveys(self) -> list[str]:
        """Names of survey directories under the active campaign."""
        campaign_dir = self.campaign().root
        try:
            return sorted(p.name for p in campaign_dir.iterdir() if p.is_dir())
        except (OSError, AttributeError):
            return []

    # -- legacy-named accessors (paths) -----------------------------------

    @property
    def site_metadata_file(self) -> Path:
        """Path to the site metadata JSON for the active station."""
        return self.station / "site_metadata.json"

    @property
    def campaign_metadata_file(self) -> Path:
        """Path to the campaign metadata JSON for the active campaign."""
        return self.campaign().metadata_file

    @property
    def survey_metadata_file(self) -> Path:
        """Path to the survey metadata JSON for the active survey."""
        return self.survey / "survey_meta.json"

    @property
    def pride_directory(self) -> Path:
        """Path to the workspace-level Pride PPP working directory."""
        return self._tree.pride_dir


# ---------------------------------------------------------------------------
# AssetQueryFacade — scoped catalog reads + writeback
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class AssetQueryFacade:
    """Scoped catalog access. The only sanctioned write path for
    :class:`AssetEntry` updates.
    """

    _catalog: AssetCatalogPort
    _scope: CampaignScope

    def all(self, kind: AssetKind | None = None) -> list[AssetEntry]:
        """All cataloged assets in the active scope (optionally by kind)."""
        return self._catalog.assets_for(self._scope, kind)

    def by_id(self, asset_id: int) -> AssetEntry | None:
        """Return the asset with `asset_id`, or None if absent."""
        return self._catalog.by_id(asset_id)

    def by_local_path(self, path: Path) -> list[AssetEntry]:
        """Return all cataloged assets whose `local_path` matches `path`."""
        return self._catalog.by_local_path(path)

    def count_by_kind(self) -> dict[AssetKind, int]:
        """Return per-`AssetKind` counts within the active scope."""
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
                "AssetQueryFacade.update requires a persisted entry (entry.id must not be None)"
            )
        new_entry = replace(entry, **changes)  # type: ignore[arg-type]
        if not self._catalog.update(new_entry):
            raise LookupError(f"No catalog row for asset id={entry.id}")
        return new_entry

    # -- legacy-style composition helpers ---------------------------------

    def local(self, kind: AssetKind) -> list[AssetEntry]:
        """Assets of ``kind`` in scope that have a non-null local_path."""
        return [a for a in self._catalog.assets_for(self._scope, kind) if a.local_path]

    def dtype_counts(self) -> dict[str, int]:
        """:meth:`count_by_kind` keyed by string (legacy ``get_dtype_counts``)."""
        return {k.value: v for k, v in self._catalog.count_by_kind(self._scope).items()}

    def update_local_path(self, asset_id: int, local_path: Path | str) -> bool:
        """Set the local_path of the asset with ``asset_id``."""
        existing = self._catalog.by_id(asset_id)
        if existing is None:
            return False
        new_entry = replace(existing, local_path=Path(local_path))
        return self._catalog.update(new_entry)

    def remote_file_exists_locally(
        self,
        kind: AssetKind,
        remote_path: str,
    ) -> bool:
        """Has any cataloged asset already stored a local copy of this remote file?
        Mirrors legacy ``remote_file_exist``: matches by basename of the
        remote URL against ``local_path`` of in-scope, in-kind assets.
        """
        from os.path import basename

        target = basename(remote_path)
        for a in self._catalog.assets_for(self._scope, kind):
            if a.local_path and target in str(a.local_path):
                return True
        return False

    def add_or_update(self, entry: AssetEntry) -> AssetEntry | None:
        """Insert if no entry with the same ``local_path`` exists; else update.
        Mirrors legacy ``add_or_update``: idempotent ingestion. Returns the
        persisted entry, or ``None`` if the input was ``None``.
        """
        if entry is None:
            return None
        if entry.local_path is not None:
            existing = self._catalog.by_local_path(entry.local_path)
            if existing:
                # Update the first match; preserve its id.
                replaced = replace(entry, id=existing[0].id)
                self._catalog.update(replaced)
                return replaced
        return self._catalog.add(entry)

    def single_to_process(
        self,
        parent_kind: AssetKind,
        child_kind: AssetKind | None = None,
        *,
        override: bool = False,
        local_only: bool = False,
    ) -> list[AssetEntry]:
        """Parent-kind entries lacking a child-kind result.
        Mirrors legacy ``get_single_entries_to_process``. When ``child_kind``
        is None, falls back to ``is_processed``.
        """
        parents = self._catalog.assets_for(self._scope, parent_kind)
        if child_kind is None:
            if override:
                candidates = parents
            else:
                candidates = [p for p in parents if not p.is_processed]
        else:
            children = self._catalog.assets_for(self._scope, child_kind)
            parent_id_map = {p.id: p for p in parents if p.id is not None}
            if not override:
                for c in children:
                    if c.parent_id in parent_id_map:
                        parent_id_map.pop(c.parent_id, None)
            candidates = list(parent_id_map.values())

        if local_only:
            candidates = [e for e in candidates if e.local_path is not None]

        # Dedupe by local_path (same fallback as legacy).
        seen: dict[Path | None, AssetEntry] = {}
        for e in candidates:
            seen[e.local_path] = e
        return list(seen.values())

    # -- merge job tracking (delegates to AssetStore) ---------------------

    def add_merge_job(
        self,
        parent_type: str,
        child_type: str,
        parent_ids: list[int] | list[str],
    ) -> None:
        """Record completion of a merge from `parent_ids` into a `child_type`."""
        self._catalog.add_merge_job(parent_type, child_type, parent_ids)

    def is_merge_complete(
        self,
        parent_type: str,
        child_type: str,
        parent_ids: list[int] | list[str],
    ) -> bool:
        """Return True iff a matching merge job has been recorded previously."""
        return self._catalog.is_merge_complete(parent_type, child_type, parent_ids)


# ---------------------------------------------------------------------------
# IngestFacade — discover/ingest/download orchestration
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class IngestFacade:
    """Workflow-friendly orchestration over :class:`Ingestor`."""

    _ingestor: Ingestor
    _scope: CampaignScope

    def local(self, source_dir: Path) -> IngestReport:
        """Ingest assets from a local directory into the active scope."""
        return self._ingestor.ingest_local(self._scope, source_dir)

    def discover_archive(self, archive_url: str) -> IngestReport:
        """Discover assets at `archive_url` and catalog them in the active scope."""
        return self._ingestor.discover_archive(self._scope, archive_url)

    def discover_campaign(self) -> IngestReport:
        """Discover all canonical archive locations for the active campaign."""
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
        """Download remote assets in scope (optionally filtered by `kinds`) to `dest_dir`."""
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
