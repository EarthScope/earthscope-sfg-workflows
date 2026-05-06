"""Domain core for the data_mgmt package.
Pure orchestration over the ports defined in ``data_mgmt.ports``. No I/O
happens directly here; everything is delegated to injected adapters. This
module is fully testable with the in-memory adapters in
``data_mgmt.adapters.memory``.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

from ._archive_urls import canonical_campaign_urls, list_campaign_archive_urls
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
from .ports import ArchiveError, ArchiveSource, AssetStore, FileStore


# ---------------------------------------------------------------------------
# File type detection (pure)
# ---------------------------------------------------------------------------


# Default filename → AssetKind regex map. Mirrors the legacy ``pattern_map``
# in ``data_mgmt/ingestion/config.py`` so behavior is preserved during the
# migration.
DEFAULT_PATTERNS: tuple[tuple[re.Pattern[str], AssetKind], ...] = (
    (re.compile(r"\.\d{2}o$", re.IGNORECASE), AssetKind.RINEX2),
    (re.compile(r"sonardyne", re.IGNORECASE), AssetKind.SONARDYNE),
    (re.compile(r"NOV000"), AssetKind.NOVATEL000),
    (re.compile(r"NOV770"), AssetKind.NOVATEL770),
    (re.compile(r"DFOP00\.raw"), AssetKind.DFOP00),
    (re.compile(r"novatelpin", re.IGNORECASE), AssetKind.NOVATELPIN),
    (re.compile(r"novatel", re.IGNORECASE), AssetKind.NOVATEL),
    (re.compile(r"\.pin$"), AssetKind.QCPIN),
    (re.compile(r"kin", re.IGNORECASE), AssetKind.KIN),
    (re.compile(r"lever_arms", re.IGNORECASE), AssetKind.LEVERARM),
    (re.compile(r"master", re.IGNORECASE), AssetKind.MASTER),
    (re.compile(r"CTD"), AssetKind.CTD),
    (re.compile(r"svpavg", re.IGNORECASE), AssetKind.SEABIRD),
    (re.compile(r"seabird", re.IGNORECASE), AssetKind.SEABIRD),
    (re.compile(r"\.res$"), AssetKind.KINRESIDUALS),
    (re.compile(r"bcoffload", re.IGNORECASE), AssetKind.BCOFFLOAD),
)


class FileTypeDetector:
    """Stateless filename → :class:`AssetKind` classifier."""

    def __init__(
        self,
        patterns: Iterable[tuple[re.Pattern[str], AssetKind]] | None = None,
    ) -> None:
        """Build a detector with `patterns`, falling back to `DEFAULT_PATTERNS`."""
        self._patterns: tuple[tuple[re.Pattern[str], AssetKind], ...] = tuple(
            patterns if patterns is not None else DEFAULT_PATTERNS
        )

    def detect(self, filename: str) -> AssetKind | None:
        """Return the first matching kind, or ``None`` if no pattern matches."""
        for pattern, kind in self._patterns:
            if pattern.search(filename):
                return kind
        return None


# ---------------------------------------------------------------------------
# Tree builder — materializes pure layouts onto a FileStore
# ---------------------------------------------------------------------------


class TreeBuilder:
    """Materializes :class:`DirectoryTree` paths against a :class:`FileStore`.
    Separates *describing* the tree (pure) from *creating* it (I/O).
    """

    def __init__(self, tree: DirectoryTree, files: FileStore) -> None:
        """Bind a pure `DirectoryTree` to a concrete `FileStore` for materialization."""
        self._tree = tree
        self._files = files

    @property
    def tree(self) -> DirectoryTree:
        """Underlying pure `DirectoryTree`."""
        return self._tree

    def ensure_workspace(self) -> None:
        """Create the workspace root and Pride directory."""
        self._files.mkdir(self._tree.root)
        self._files.mkdir(self._tree.pride_dir)

    def ensure_station(self, scope: CampaignScope) -> TileDBLayout:
        """Materialize the station and TileDB array directories; return the layout."""
        self._files.mkdir(self._tree.station_dir(scope))
        layout = self._tree.tiledb(scope)
        for path in (
            layout.root,
            layout.acoustic,
            layout.kin_position,
            layout.imu_position,
            layout.shotdata,
            layout.shotdata_pre,
            layout.gnss_obs,
            layout.gnss_obs_secondary,
        ):
            self._files.mkdir(path)
        return layout

    def ensure_campaign(self, scope: CampaignScope) -> CampaignLayout:
        """Materialize the campaign directory tree (top-down); return the layout."""
        # Walk top-down so missing parents materialize before children.
        self._files.mkdir(self._tree.network_dir(scope.network))
        self._files.mkdir(self._tree.station_dir(scope))
        layout = self._tree.campaign(scope)
        for path in layout.standard_dirs:
            self._files.mkdir(path)
        return layout

    def ensure_garpos_survey(self, scope: CampaignScope) -> GARPOSLayout:
        """Materialize the GARPOS survey directory tree; requires `scope.survey`."""
        if scope.survey is None:
            raise ValueError("CampaignScope.survey is required for GARPOS materialization")
        self._files.mkdir(self._tree.survey_dir(scope))
        layout = self._tree.garpos(scope)
        for path in layout.standard_dirs:
            self._files.mkdir(path)
        return layout


# ---------------------------------------------------------------------------
# Ingestor — discovery + cataloging + download orchestration
# ---------------------------------------------------------------------------


def _now() -> datetime:
    return datetime.now(tz=timezone.utc)


class Ingestor:
    """End-to-end ingestion: discover → detect → catalog → optionally download.
    All I/O is delegated to :class:`AssetStore`, :class:`FileStore`, and
    :class:`ArchiveSource`. The orchestration logic itself is pure and lives
    here.
    """

    def __init__(
        self,
        catalog: AssetStore,
        files: FileStore,
        archive: ArchiveSource,
        detector: FileTypeDetector,
        tree: DirectoryTree,
    ) -> None:
        """Wire the four ports + tree used by ingestion orchestration."""
        self._catalog = catalog
        self._files = files
        self._archive = archive
        self._detector = detector
        self._tree = tree

    # -- local ingest ------------------------------------------------------

    def ingest_local(self, scope: CampaignScope, source_dir: Path) -> IngestReport:
        """Catalog every recognized file under ``source_dir``."""
        if not self._files.is_dir(source_dir):
            return IngestReport(errors=(f"Not a directory: {source_dir}",))

        cataloged = 0
        skipped = 0
        errors: list[str] = []

        for info in self._files.list_files(source_dir, recursive=True):
            if not info.is_file or info.path.name.startswith("._"):
                skipped += 1
                continue
            kind = self._detector.detect(info.path.name)
            if kind is None:
                skipped += 1
                continue

            asset = AssetEntry(
                kind=kind,
                scope=scope,
                local_path=info.path,
                timestamp_created=_now(),
            )
            try:
                self._catalog.add(asset)
                cataloged += 1
            except Exception as exc:  # adapter-specific errors
                errors.append(f"add failed for {info.path}: {exc}")

        return IngestReport(
            cataloged=cataloged,
            skipped=skipped,
            errors=tuple(errors),
        )

    # -- remote discovery (no download) -----------------------------------

    def discover_archive(
        self,
        scope: CampaignScope,
        archive_url: str,
    ) -> IngestReport:
        """List ``archive_url`` and catalog every recognized remote file.
        Sets ``remote_path`` only; ``local_path`` remains ``None`` until
        :meth:`download` is called.
        """
        cataloged = 0
        skipped = 0
        errors: list[str] = []

        try:
            files = self._archive.list_files(archive_url)
        except ArchiveError as exc:
            return IngestReport(errors=(f"list_files({archive_url}) failed: {exc}",))

        for af in files:
            kind = self._detector.detect(af.filename)
            if kind is None:
                skipped += 1
                continue
            asset = AssetEntry(
                kind=kind,
                scope=scope,
                remote_path=af.url,
                remote_type="http",
                timestamp_created=_now(),
            )
            try:
                self._catalog.add(asset)
                cataloged += 1
            except Exception as exc:
                errors.append(f"add failed for {af.url}: {exc}")

        return IngestReport(
            cataloged=cataloged,
            skipped=skipped,
            errors=tuple(errors),
        )

    # -- canonical campaign discovery -------------------------------------

    def discover_campaign(self, scope: CampaignScope) -> IngestReport:
        """Discover the four canonical EarthScope campaign URLs in one call.
        Composes raw, metadata, RINEX 1Hz, and RINEX 10Hz URLs for ``scope``
        (see :mod:`data_mgmt._archive_urls`), lists each, and catalogs every
        recognized remote file. Replaces the legacy
        ``data_mgmt.ingestion.archive_pull.list_campaign_files`` flow with a
        single domain-level operation.

        Errors from individual URLs are collected into the returned
        :class:`IngestReport` rather than raised; partial success is the
        common case (e.g. RINEX 10Hz absent for a campaign).
        """
        cataloged = 0
        skipped = 0
        errors: list[str] = []

        for url in canonical_campaign_urls(scope):
            sub = self.discover_archive(scope, url)
            cataloged += sub.cataloged
            skipped += sub.skipped
            errors.extend(sub.errors)

        return IngestReport(
            cataloged=cataloged,
            skipped=skipped,
            errors=tuple(errors),
        )

    def list_archive_urls(self, scope: CampaignScope) -> list[str]:
        """Enumerate every archive file URL for ``scope`` without writing to the catalog.
        Side-effect-free counterpart to :meth:`discover_campaign`.
        """
        return list_campaign_archive_urls(self._archive, scope)

    # -- download cataloged remotes ---------------------------------------

    def download(
        self,
        scope: CampaignScope,
        kinds: list[AssetKind] | None = None,
        dest_dir: Path | None = None,
    ) -> IngestReport:
        """Download every cataloged remote asset that lacks ``local_path``.
        Optionally restrict to ``kinds``. Destination defaults to the
        campaign's ``raw`` directory.
        """
        target = dest_dir or self._tree.campaign(scope).raw
        self._files.mkdir(target)

        downloaded = 0
        skipped = 0
        errors: list[str] = []

        candidates = self._collect_remote_candidates(scope, kinds)

        for asset in candidates:
            if asset.local_path is not None:
                skipped += 1
                continue
            if asset.remote_path is None:
                skipped += 1
                continue
            dest = target / Path(asset.remote_path).name
            try:
                self._archive.download_file(asset.remote_path, dest)
                self._catalog.update(asset.with_local_path(dest))
                downloaded += 1
            except ArchiveError as exc:
                errors.append(f"download failed for {asset.remote_path}: {exc}")

        return IngestReport(
            downloaded=downloaded,
            skipped=skipped,
            errors=tuple(errors),
        )

    def _collect_remote_candidates(
        self,
        scope: CampaignScope,
        kinds: list[AssetKind] | None,
    ) -> list[AssetEntry]:
        if kinds is None:
            return [a for a in self._catalog.assets_for(scope) if a.remote_path]
        out: list[AssetEntry] = []
        for kind in kinds:
            out.extend(a for a in self._catalog.assets_for(scope, kind) if a.remote_path)
        return out


# ---------------------------------------------------------------------------
# LayoutInspector — FileStore-backed I/O queries over pure layouts
# ---------------------------------------------------------------------------


class LayoutInspector:
    """File-store-backed introspection of pure :mod:`data_mgmt.model` layouts.
    Replaces the I/O methods that used to live on the legacy Pydantic
    directory schemas (``GARPOSSurveyDir.is_garpos_directory``,
    ``find_rectified_shotdata``, etc.). Decouples *describing* a layout
    (pure paths) from *probing* it on a backing store (local FS, S3,
    in-memory).

    All methods are total: missing files return ``None`` / ``False`` / ``[]``
    rather than raising. Exceptions propagate only when the underlying
    :class:`FileStore` itself fails.
    """

    def __init__(self, files: FileStore) -> None:
        self._files = files

    # -- GARPOS ------------------------------------------------------------

    def is_garpos_directory(self, layout: GARPOSLayout) -> bool:
        """A directory is a GARPOS dir if it has both default GARPOS files."""
        return self._files.is_file(layout.obs_file) and self._files.is_file(layout.settings_file)

    def find_rectified_shotdata(self, layout: GARPOSLayout) -> Path | None:
        """Locate the first ``*_rectified.csv`` directly under the GARPOS dir."""
        return self._first_match(layout.root, "_rectified.csv")

    def find_filtered_shotdata(self, survey_dir: Path) -> Path | None:
        """Locate the first ``*_filtered.csv`` in the parent survey directory."""
        return self._first_match(survey_dir, "_filtered.csv")

    # -- Campaign ----------------------------------------------------------

    def is_campaign_directory(self, layout: CampaignLayout) -> bool:
        """Heuristic: a campaign dir exists and has the standard subdirs."""
        if not self._files.is_dir(layout.root):
            return False
        return all(self._files.is_dir(p) for p in (layout.raw, layout.processed))

    def find_master_xml(self, layout: CampaignLayout) -> Path | None:
        """Locate the first ``master*.xml`` under the campaign metadata layout."""
        return self._first_match(layout.root, ".xml", contains="master")

    # -- Generic -----------------------------------------------------------

    def list_kind(
        self,
        directory: Path,
        suffix: str | None = None,
        contains: str | None = None,
    ) -> list[Path]:
        """List files in ``directory`` filtered by suffix and/or substring.
        Non-recursive. Returns paths sorted by name for determinism. Returns
        ``[]`` if ``directory`` does not exist.
        """
        if not self._files.is_dir(directory):
            return []
        out: list[Path] = []
        for info in self._files.list_files(directory, recursive=False):
            if not info.is_file:
                continue
            name = info.path.name
            if suffix is not None and not name.endswith(suffix):
                continue
            if contains is not None and contains.lower() not in name.lower():
                continue
            out.append(info.path)
        out.sort(key=lambda p: p.name)
        return out

    def _first_match(
        self,
        directory: Path,
        suffix: str,
        contains: str | None = None,
    ) -> Path | None:
        matches = self.list_kind(directory, suffix=suffix, contains=contains)
        return matches[0] if matches else None


__all__ = [
    "DEFAULT_PATTERNS",
    "FileTypeDetector",
    "TreeBuilder",
    "Ingestor",
    "LayoutInspector",
]
