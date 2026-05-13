"""Domain core for the data_mgmt package.
Pure orchestration over the ports defined in ``data_mgmt.ports``. No I/O
happens directly here; everything is delegated to injected adapters. This
module is fully testable with the in-memory adapters in
``data_mgmt.adapters.memory``.
"""

from __future__ import annotations

import concurrent.futures
import re
import tarfile
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

import fsspec

import boto3
from tqdm.auto import tqdm
from upath import UPath

from .model import (
    ArchiveFile,
    AssetEntry,
    AssetKind,
    CampaignLayout,
    NetworkLayout,
    SFGScope,
    DirectoryTree,
    StationLayout,
    FileInfo,
    GARPOSLayout,
    IngestReport,
    SurveyLayout,
    TileDBLayout,
)
from .ports import ArchiveError, ArchiveSourcePort, AssetCatalogPort, FileStorePort


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


# ---------------------------------------------------------------------------
# FileTypeDetector — pure file-name → AssetKind classifier
# ---------------------------------------------------------------------------


class FileTypeDetector:
    """Classifies filenames to :class:`AssetKind` using regex patterns.

    Wraps :data:`DEFAULT_PATTERNS` by default; inject a custom list to
    override.
    """

    def __init__(
        self,
        patterns: Iterable[tuple[re.Pattern[str], AssetKind]] | None = None,
    ) -> None:
        self._patterns = tuple(patterns if patterns is not None else DEFAULT_PATTERNS)

    def detect(self, filename: str) -> AssetKind | None:
        """Return the first matching :class:`AssetKind`, or ``None``."""
        for pattern, kind in self._patterns:
            if pattern.search(filename):
                return kind
        return None


# ---------------------------------------------------------------------------
# FileManager — materializes a DirectoryTree on a FileStore
# ---------------------------------------------------------------------------


class FileManager:
    """Creates and validates workspace directories on a :class:`FileStore`.

    Pass *remote_tree* and *remote_backend* to enable bidirectional sync via
    :meth:`push_dir`, :meth:`push_file`, and :meth:`pull_dir`.  Both must be
    provided together; omitting either disables remote operations silently.
    """

    def __init__(
        self,
        directory_tree: DirectoryTree,
        file_backend: FileStorePort,
        remote_tree: DirectoryTree | None = None,
        remote_backend: FileStorePort | None = None,
    ) -> None:
        self.directory_tree = directory_tree
        self.file_backend = file_backend
        self._remote_tree = remote_tree
        self._remote_backend = remote_backend

    @property
    def has_remote(self) -> bool:
        """True when both *remote_tree* and *remote_backend* are configured."""
        return self._remote_tree is not None and self._remote_backend is not None

    def remote_path_for(self, local_path: UPath) -> UPath | None:
        """Return the theoretical remote path for *local_path*, or ``None``.

        Maps ``local_tree.root / <relative>`` → ``remote_tree.root / <relative>``.
        Returns ``None`` when no remote is configured or *local_path* is not
        under the local tree root.
        """
        if not self.has_remote:
            return None
        try:
            relative = UPath(local_path).relative_to(self.directory_tree.root)
        except ValueError:
            return None
        return UPath(self._remote_tree.root) / relative

    def push_file(self, local_path: UPath, overwrite: bool = False) -> bool:
        """Upload a single file to its mirrored remote path.  Returns True if uploaded."""
        if not self.has_remote:
            return False
        local_path = UPath(local_path)
        remote_path = self.remote_path_for(local_path)
        if remote_path is None:
            return False
        if overwrite or not self._remote_backend.exists(remote_path):
            self._remote_backend.put_remote(local_path, str(remote_path))
            return True
        return False

    def push_dir(self, local_root: UPath, overwrite: bool = False) -> int:
        """Upload all files under *local_root* to the mirrored remote path.

        Returns the number of files actually uploaded.
        """
        if not self.has_remote:
            return 0
        local_root = UPath(local_root)
        remote_root = self.remote_path_for(local_root)
        if remote_root is None:
            return 0
        count = 0
        for info in self.file_backend.list_files(local_root, recursive=True):
            local_path = UPath(info.path)
            remote_path = self.remote_path_for(local_path)
            if remote_path is None:
                continue
            if overwrite or not self._remote_backend.exists(remote_path):
                self._remote_backend.put_remote(local_path, str(remote_path))
                count += 1
        return count

    def pull_dir(self, local_root: UPath, overwrite: bool = False) -> int:
        """Download all files from the mirrored remote path into *local_root*.

        Returns the number of files actually downloaded.
        """
        if not self.has_remote:
            return 0
        local_root = UPath(local_root)
        remote_root = self.remote_path_for(local_root)
        if remote_root is None:
            return 0
        count = 0
        for info in self._remote_backend.list_files(remote_root, recursive=True):
            remote_path = UPath(info.path)
            try:
                rel = remote_path.relative_to(remote_root)
            except ValueError:
                rel = UPath(remote_path.name)
            local_path = local_root / rel
            if overwrite or not self.file_backend.exists(local_path):
                self.file_backend.get_remote(str(remote_path), local_path)
                count += 1
        return count

    def ensure_workspace(self) -> None:
        """Create the workspace root and Pride directory."""
        self.file_backend.mkdir(self.directory_tree.root)
        self.file_backend.mkdir(self.directory_tree.pride_dir)

    def ensure_network(self, network: str) -> NetworkLayout:
        network_layout: NetworkLayout = self.directory_tree.network(network)
        for path in network_layout.standard_dirs:
            self.file_backend.mkdir(path)
        return network_layout
    
    def ensure_station(self,*,network:str,station:str) -> StationLayout:
        """Materialize the station and TileDB array directories; return the layout."""
        self.file_backend.mkdir(self.directory_tree.station_dir(network=network, station=station))
        station_layout:StationLayout = self.directory_tree.station(network=network, station=station)
        for path in station_layout.standard_dirs:
            self.file_backend.mkdir(path)
        return station_layout

    def ensure_campaign(
        self,
        scope: "SFGScope | None" = None,
        *,
        network: str | None = None,
        station: str | None = None,
        campaign: str | None = None,
    ) -> CampaignLayout:
        """Materialize the campaign directory tree (top-down); return the layout."""
        net = scope.network if scope is not None else network
        sta = scope.station if scope is not None else station
        camp = scope.campaign if scope is not None else campaign
        self.file_backend.mkdir(self.directory_tree.network_dir(network=net))
        self.file_backend.mkdir(self.directory_tree.station_dir(network=net, station=sta))
        layout = self.directory_tree.campaign(network=net, station=sta, campaign=camp)
        for path in layout.standard_dirs:
            self.file_backend.mkdir(path)
        return layout

    def ensure_survey(
        self,
        scope: "SFGScope | None" = None,
        *,
        network: str | None = None,
        station: str | None = None,
        campaign: str | None = None,
        survey: str | None = None,
    ) -> SurveyLayout:
        """Materialize the survey directory; return the layout."""
        net = scope.network if scope is not None else network
        sta = scope.station if scope is not None else station
        camp = scope.campaign if scope is not None else campaign
        surv = scope.survey if scope is not None else survey
        survey_layout: SurveyLayout = self.directory_tree.survey(
            network=net, station=sta, campaign=camp, survey=surv
        )
        for path in survey_layout.standard_dirs:
            self.file_backend.mkdir(path)
        return self.directory_tree.survey(network=net, station=sta, campaign=camp, survey=surv)

    def ensure_garpos_survey(
        self,
        scope: "SFGScope | None" = None,
        *,
        network: str | None = None,
        station: str | None = None,
        campaign: str | None = None,
        survey: str | None = None,
    ) -> GARPOSLayout:
        """Materialize the GARPOS survey directory tree; requires ``scope.survey``."""
        net = scope.network if scope is not None else network
        sta = scope.station if scope is not None else station
        camp = scope.campaign if scope is not None else campaign
        surv = scope.survey if scope is not None else survey
        if surv is None:
            raise ValueError("survey is required for ensure_garpos_survey")
        layout = self.directory_tree.garpos(
            network=net, station=sta, campaign=camp, survey=surv
        )
        for path in layout.standard_dirs:
            self.file_backend.mkdir(path)
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
        catalog: AssetCatalogPort,
        file_manager: FileManager,
        archive: ArchiveSourcePort,
        patterns: Iterable[tuple[re.Pattern[str], AssetKind]] | None = None,
        *,
        detector: FileTypeDetector | None = None,
    ) -> None:
        self._catalog = catalog
        self._file_manager = file_manager
        self._archive = archive
        if detector is not None:
            self._detector = detector
        elif patterns is not None:
            self._detector = FileTypeDetector(patterns)
        else:
            self._detector = FileTypeDetector()

    def detect(self, filename: str) -> AssetKind | None:
        """Return the first matching kind, or ``None`` if no pattern matches."""
        return self._detector.detect(filename)

    # -- local ingest ------------------------------------------------------

    def ingest_local(self, scope: SFGScope, source_dir: Path) -> IngestReport:
        """Catalog every recognized file under ``source_dir``."""
        if not self._file_manager.file_backend.is_dir(source_dir):
            return IngestReport(errors=(f"Not a directory: {source_dir}",))

        cataloged = 0
        skipped = 0
        errors: list[str] = []

        for info in self._file_manager.file_backend.list_files(source_dir, recursive=True):
            if not info.is_file or info.path.name.startswith("._"):
                skipped += 1
                continue
            kind = self.detect(info.path.name)
            if kind is None:
                skipped += 1
                continue

            if self._catalog.by_local_path(info.path):
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

    # -- tarball ingest (qc pin files) ------------------------------------

    def ingest_qcpin_tarballs(
        self,
        scope: SFGScope,
        tarball_dir: Path | str,
        *,
        override: bool = False,
    ) -> IngestReport:
        """Discover ``.tar.gz`` tarballs in *tarball_dir*, extract ``.pin``
        files in-memory, write them to a subdirectory named after each tarball,
        and register each as an :attr:`AssetKind.QCPIN` entry in the catalog.

        Skips macOS resource-fork files (``._``-prefixed). Uses
        ``tarfile.open(mode="r:*")`` for Python-3.13 compatibility.

        Args:
            scope: Active network/station/campaign scope.
            tarball_dir: Directory containing ``.tar.gz`` archives.
            override: Re-ingest (overwrite) even if the extracted file already
                exists on disk.
        """
        tarball_dir = Path(tarball_dir)
        if not tarball_dir.is_dir():
            return IngestReport(errors=(f"Not a directory: {tarball_dir}",))

        cataloged = 0
        skipped = 0
        errors: list[str] = []

        tarballs = sorted(
            p for p in tarball_dir.glob("*.tar.gz")
            if not p.name.startswith("._")
        )

        for tb in tarballs:
            extract_dir = tarball_dir / tb.name.removesuffix(".tar.gz")
            try:
                with fsspec.open(str(tb), "rb") as fo:
                    with tarfile.open(fileobj=fo, mode="r:*") as tf:
                        pin_members = [
                            m for m in tf.getmembers()
                            if m.isfile() and (self.detect(m.name) is AssetKind.QCPIN)
                        ]
                        if not pin_members:
                            skipped += 1
                            continue
                        extract_dir.mkdir(parents=True, exist_ok=True)
                        for member in pin_members:
                            pin_name = Path(member.name).name
                            dest = extract_dir / pin_name
                            if not override and self._catalog.by_local_path(UPath(dest)):
                                skipped += 1
                                continue
                            reader = tf.extractfile(member)
                            if reader is None:
                                skipped += 1
                                continue
                            dest.write_bytes(reader.read())
                            asset = AssetEntry(
                                kind=AssetKind.QCPIN,
                                scope=scope,
                                local_path=UPath(dest),
                                timestamp_created=_now(),
                            )
                            try:
                                self._catalog.add(asset)
                                cataloged += 1
                            except Exception as exc:
                                errors.append(f"add failed for {dest}: {exc}")
            except Exception as exc:
                errors.append(f"failed to open tarball {tb}: {exc}")

        return IngestReport(
            cataloged=cataloged,
            skipped=skipped,
            errors=tuple(errors),
        )

    # -- remote discovery (no download) -----------------------------------

    def discover_archive(
        self,
        scope: SFGScope,
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

    def discover_campaign(self, scope: SFGScope) -> IngestReport:
        """Discover the canonical EarthScope campaign URLs and catalog them."""
        from earthscope_sfg_workflows.data_mgmt.archives.earthscope._archive_urls import (
            canonical_campaign_urls,
        )

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

    def list_archive_urls(self, scope: SFGScope) -> list[str]:
        """Enumerate every archive file URL for ``scope`` without writing to the catalog."""
        from earthscope_sfg_workflows.data_mgmt.archives.earthscope._archive_urls import (
            list_campaign_archive_urls,
        )

        return list_campaign_archive_urls(self._archive, scope)

    # -- download cataloged remotes ---------------------------------------

    def download(
        self,
        scope: SFGScope,
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
        scope: SFGScope,
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
            self._file_manager.file_backend.mkdir(UPath(local_dir))
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

    def __init__(self, files: FileStorePort) -> None:
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
