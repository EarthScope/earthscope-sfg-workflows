"""Domain core for the data_mgmt package.
Pure orchestration over the ports defined in ``data_mgmt.ports``. No I/O
happens directly here; everything is delegated to injected adapters. This
module is fully testable with the in-memory adapters in
``data_mgmt.adapters.memory``.
"""

from __future__ import annotations

import concurrent.futures
import re
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

import boto3
from tqdm.auto import tqdm
from upath import UPath

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


DEFAULT_PATTERNS: tuple[tuple[re.Pattern[str], AssetKind], ...] = (
    (re.compile(r"\.\d{2}o$", re.IGNORECASE), AssetKind.RINEX2),
    (re.compile(r"\.\d{2}n$", re.IGNORECASE), AssetKind.RINEX3),
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
    """Maps filenames to :class:`AssetKind` using regex patterns."""

    def __init__(
        self,
        patterns: Iterable[tuple[re.Pattern[str], AssetKind]] | None = None,
    ) -> None:
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
# FileManager — materializes a DirectoryTree on a FileStore
# ---------------------------------------------------------------------------


class FileManager:
    """Creates and validates workspace directories on a :class:`FileStore`."""

    def __init__(self, tree: DirectoryTree, files: FileStore) -> None:
        self._tree = tree
        self._files = files

    @property
    def directory_tree(self) -> DirectoryTree:
        return self._tree

    def ensure_workspace(self) -> None:
        """Create the workspace root and Pride directory."""
        self._files.mkdir(self._tree.root)
        self._files.mkdir(self._tree.pride_dir)

    def ensure_station(self, scope: CampaignScope) -> TileDBLayout:
        """Materialize the station and TileDB array directories; return the layout."""
        self._files.mkdir(self._tree.station_dir(scope))
        layout = self._tree.tiledb(scope)
        for path in layout.all_paths:
            self._files.mkdir(path)
        return layout

    def ensure_campaign(self, scope: CampaignScope) -> CampaignLayout:
        """Materialize the campaign directory tree (top-down); return the layout."""
        self._files.mkdir(self._tree.network_dir(scope.network))
        self._files.mkdir(self._tree.station_dir(scope))
        layout = self._tree.campaign(scope)
        for path in layout.standard_dirs:
            self._files.mkdir(path)
        return layout

    def ensure_garpos_survey(self, scope: CampaignScope) -> GARPOSLayout:
        """Materialize the GARPOS survey directory tree; requires ``scope.survey``."""
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
    """End-to-end ingestion: discover → detect → catalog → download.
    All I/O is delegated to injected ports. The orchestration logic itself
    is pure and lives here.
    """

    def __init__(
        self,
        catalog: AssetStore,
        file_manager: FileManager,
        archive: ArchiveSource,
        patterns: Iterable[tuple[re.Pattern[str], AssetKind]] | None = None,
    ) -> None:
        self._catalog = catalog
        self._file_manager = file_manager
        self._archive = archive
        self._patterns = tuple(patterns if patterns is not None else DEFAULT_PATTERNS)

    def detect(self, filename: str) -> AssetKind | None:
        """Return the first matching kind, or ``None`` if no pattern matches."""
        for pattern, kind in self._patterns:
            if pattern.search(filename):
                return kind
        return None

    # -- local ingest ------------------------------------------------------

    def ingest_local(self, scope: CampaignScope, source_dir: Path) -> IngestReport:
        """Catalog every recognized file under ``source_dir``."""
        if not self._file_manager._files.is_dir(source_dir):
            return IngestReport(errors=(f"Not a directory: {source_dir}",))

        cataloged = 0
        skipped = 0
        errors: list[str] = []

        for info in self._file_manager._files.list_files(source_dir, recursive=True):
            if not info.is_file or info.path.name.startswith("._"):
                skipped += 1
                continue
            kind = self.detect(info.path.name)
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
            except Exception as exc:
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
        directory_url: str,
    ) -> IngestReport:
        """List ``directory_url`` on the archive and catalog every recognized file."""
        cataloged = 0
        skipped = 0
        errors: list[str] = []

        try:
            archive_files = self._archive.list_files(directory_url)
        except ArchiveError as exc:
            return IngestReport(errors=(f"listing failed for {directory_url}: {exc}",))

        for af in archive_files:
            kind = self.detect(af.filename)
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
        """Discover the canonical EarthScope campaign URLs and catalog them."""
        from .archives.earthscope._archive_urls import canonical_campaign_urls

        cataloged = 0
        skipped = 0
        errors: list[str] = []

        for dir_url in canonical_campaign_urls(scope):
            sub = self.discover_archive(scope, dir_url)
            cataloged += sub.cataloged
            skipped += sub.skipped
            errors.extend(sub.errors)

        return IngestReport(
            cataloged=cataloged,
            skipped=skipped,
            errors=tuple(errors),
        )

    def list_archive_urls(self, scope: CampaignScope) -> list[str]:
        """Enumerate every archive file URL for ``scope`` without writing to the catalog."""
        from .archives.earthscope._archive_urls import list_campaign_archive_urls

        return list_campaign_archive_urls(self._archive, scope)

    # -- download cataloged remotes ---------------------------------------

    def download(
        self,
        scope: CampaignScope,
        kinds: list[AssetKind] | None = None,
        dest_dir: Path | None = None,
        *,
        override: bool = False,
        rinex_1hz: bool = False,
    ) -> IngestReport:
        """Download every cataloged remote asset that lacks a local path.

        Supports both S3 (parallel via boto3 ThreadPoolExecutor) and HTTP
        (sequential with tqdm). RINEX2 files land in ``intermediate/``; all
        other types land in ``raw/``.

        Args:
            scope: Active network/station/campaign scope.
            kinds: Restrict to these asset kinds. ``None`` downloads all kinds.
            dest_dir: Override the default destination directory.
            override: Re-download even if a local path already exists.
            rinex_1hz: When ``True``, keep only 1-Hz RINEX files; when
                ``False`` (default), keep only higher-rate RINEX files.
        """
        layout = self._file_manager.directory_tree.campaign(scope)
        candidates = self._collect_remote_candidates(scope, kinds)

        if not override:
            candidates = [
                a for a in candidates
                if a.local_path is None or not Path(str(a.local_path)).exists()
            ]

        # RINEX 1Hz / high-rate filtering
        rinex = [a for a in candidates if a.kind is AssetKind.RINEX2]
        if rinex:
            if rinex_1hz:
                rinex = [a for a in rinex if a.remote_path and "1hz" in a.remote_path.lower()]
            else:
                rinex = [a for a in rinex if a.remote_path and "1hz" not in a.remote_path.lower()]
        non_rinex = [a for a in candidates if a.kind is not AssetKind.RINEX2]
        to_download = rinex + non_rinex

        if not to_download:
            return IngestReport()

        s3_assets = [a for a in to_download if a.remote_type == "s3"]
        http_assets = [a for a in to_download if a.remote_type == "http"]

        downloaded = 0
        skipped = 0
        errors: list[str] = []

        if s3_assets:
            with threading.Lock():
                boto3.client("s3")
            report = self._download_s3_files(s3_assets, layout, dest_dir)
            downloaded += report.downloaded
            skipped += report.skipped
            errors.extend(report.errors)

        if http_assets:
            report = self._download_http_files(http_assets, layout, dest_dir)
            downloaded += report.downloaded
            skipped += report.skipped
            errors.extend(report.errors)

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

    # -- S3 download ------------------------------------------------------

    def _download_s3_files(
        self,
        s3_assets: list[AssetEntry],
        layout: CampaignLayout,
        dest_dir: Path | None,
    ) -> IngestReport:
        """Download S3 assets in parallel and update the catalog with local paths."""
        plan: list[dict] = []
        for asset in s3_assets:
            assert asset.remote_path is not None
            _path = Path(asset.remote_path)
            local_dir = dest_dir or (
                layout.intermediate if asset.kind is AssetKind.RINEX2 else layout.raw
            )
            bucket = _path.root
            plan.append({
                "bucket": bucket,
                "prefix": str(_path.relative_to(bucket)),
                "local_dir": local_dir,
            })

        downloaded = 0
        errors: list[str] = []

        with concurrent.futures.ThreadPoolExecutor(max_workers=10) as executor:
            local_results = list(executor.map(self._download_s3_file, plan))

        for local_path, asset in zip(local_results, s3_assets, strict=False):
            if local_path is not None and asset.id is not None:
                self._catalog.update(asset.with_local_path(UPath(local_path)))
                downloaded += 1
            else:
                errors.append(f"S3 download failed for {asset.remote_path}")

        return IngestReport(downloaded=downloaded, errors=tuple(errors))

    def _download_s3_file(self, plan: dict) -> Path | None:
        """Download one S3 object using a fresh boto3 client."""
        bucket = plan["bucket"]
        prefix = plan["prefix"]
        local_dir: Path = plan["local_dir"]
        local_path = local_dir / Path(prefix).name
        try:
            client = boto3.client("s3")
            client.download_file(Bucket=bucket, Key=str(prefix), Filename=str(local_path))
            return local_path
        except Exception:
            return None

    # -- HTTP download ----------------------------------------------------

    def _download_http_files(
        self,
        http_assets: list[AssetEntry],
        layout: CampaignLayout,
        dest_dir: Path | None,
    ) -> IngestReport:
        """Download HTTP assets sequentially with a tqdm progress bar."""
        downloaded = 0
        errors: list[str] = []

        for asset in tqdm(http_assets, desc="Downloading files"):
            local_dir = dest_dir or (
                layout.intermediate if asset.kind is AssetKind.RINEX2 else layout.raw
            )
            self._file_manager._files.mkdir(UPath(local_dir))
            assert asset.remote_path is not None
            local_path = self._download_http_file(asset.remote_path, Path(str(local_dir)))
            if local_path is not None and asset.id is not None:
                self._catalog.update(asset.with_local_path(UPath(local_path)))
                downloaded += 1
            else:
                errors.append(f"HTTP download failed for {asset.remote_path}")

        return IngestReport(downloaded=downloaded, errors=tuple(errors))

    def _download_http_file(self, remote_url: str, local_dir: Path) -> Path | None:
        """Download one HTTP file via the injected :class:`ArchiveSource`."""
        local_path = local_dir / Path(remote_url).name
        try:
            self._archive.download_file(remote_url, local_path)
            if not local_path.exists():
                raise FileNotFoundError(f"{local_path} not created after download")
            return local_path
        except Exception:
            return None


# ---------------------------------------------------------------------------
# LayoutInspector — FileStore-backed I/O queries over pure layouts
# ---------------------------------------------------------------------------


class LayoutInspector:
    """File-store-backed introspection of pure :mod:`data_mgmt.model` layouts."""

    def __init__(self, files: FileStore) -> None:
        self._files = files

    def is_garpos_directory(self, layout: GARPOSLayout) -> bool:
        return self._files.is_file(layout.obs_file) and self._files.is_file(layout.settings_file)

    def find_rectified_shotdata(self, layout: GARPOSLayout) -> Path | None:
        return self._first_match(layout.root, "_rectified.csv")

    def find_filtered_shotdata(self, survey_dir: Path) -> Path | None:
        return self._first_match(survey_dir, "_filtered.csv")

    def is_campaign_directory(self, layout: CampaignLayout) -> bool:
        if not self._files.is_dir(layout.root):
            return False
        return all(self._files.is_dir(p) for p in (layout.raw, layout.processed))

    def list_kind(
        self,
        directory: Path,
        suffix: str | None = None,
        contains: str | None = None,
    ) -> list[Path]:
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
    "FileManager",
    "Ingestor",
    "LayoutInspector",
]
