"""IngestService — data ingest operations for a StationSession."""
from __future__ import annotations

import concurrent.futures
import re
import tarfile
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING

import boto3
import fsspec
from tqdm.auto import tqdm
from upath import UPath

from earthscope_sfg_workflows.data_mgmt.core import DEFAULT_PATTERNS, FileTypeDetector
from earthscope_sfg_workflows.data_mgmt.model import AssetEntry, AssetKind, IngestReport
from earthscope_sfg_workflows.data_mgmt.ports import ArchiveError

if TYPE_CHECKING:
    from earthscope_sfg_workflows.data_mgmt.model import SFGScope
    from earthscope_sfg_workflows.data_mgmt.ports import (
        ArchiveSourcePort,
        AssetCatalogPort,
        FileStorePort,
    )
    from earthscope_sfg_workflows.workflows.session import StationSession


def _now() -> datetime:
    return datetime.now(tz=timezone.utc)


class IngestService:
    """Ingest operations (local, remote, download) scoped to a :class:`StationSession`.

    Holds the catalog, file-backend, and archive ports directly so all ingest
    orchestration lives here without an intermediate helper object.

    *override* is stored at construction and applied to every operation that
    supports it (e.g. :meth:`qcpin_tarballs` and :meth:`download_remote`).
    """

    def __init__(self, session: "StationSession", *, override: bool = False) -> None:
        self._s = session
        self.override = override
        self._catalog: AssetCatalogPort = session._catalog
        self._file_backend: FileStorePort = session._file_manager.file_backend
        self._archive: ArchiveSourcePort = session._archive
        self._detector = FileTypeDetector()

    # ------------------------------------------------------------------
    # File-type detection
    # ------------------------------------------------------------------

    def detect(self, filename: str) -> AssetKind | None:
        """Return the first matching :class:`AssetKind`, or ``None``."""
        return self._detector.detect(filename)

    # ------------------------------------------------------------------
    # Local ingest
    # ------------------------------------------------------------------

    def local(self, source_dir: Path) -> IngestReport:
        """Catalog every recognized file under *source_dir*."""
        scope = self._s.scope
        if not self._file_backend.is_dir(source_dir):
            return IngestReport(errors=(f"Not a directory: {source_dir}",))

        cataloged = 0
        skipped = 0
        errors: list[str] = []

        for info in self._file_backend.list_files(source_dir, recursive=True):
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

        return IngestReport(cataloged=cataloged, skipped=skipped, errors=tuple(errors))

    # ------------------------------------------------------------------
    # Tarball ingest (QC pin/sta files)
    # ------------------------------------------------------------------

    def qcpin_tarballs(
        self,
        tarball_dir: Path | None = None,
        *,
        override: bool | None = None,
    ) -> IngestReport:
        """Extract ``.pin``/``.sta`` files from ``.tar.gz`` tarballs and catalog them.

        *override* defaults to the value set at construction when not passed.
        """
        effective_override = self.override if override is None else override
        scope = self._s.scope
        if tarball_dir is None:
            layout = self._s.active_campaign_layout
            if layout is None:
                raise ValueError("qcpin_tarballs requires a campaign with a layout")
            tarball_dir = Path(layout.qc)

        tarball_dir = Path(tarball_dir)
        if not tarball_dir.is_dir():
            return IngestReport(errors=(f"Not a directory: {tarball_dir}",))

        cataloged = 0
        skipped = 0
        errors: list[str] = []

        tarballs = sorted(
            p for p in tarball_dir.glob("*.tar.gz") if not p.name.startswith("._")
        )

        for tb in tarballs:
            extract_dir = tarball_dir / tb.name.removesuffix(".tar.gz")
            try:
                with fsspec.open(str(tb), "rb") as fo:
                    with tarfile.open(fileobj=fo, mode="r:*") as tf:
                        pin_members = [
                            m for m in tf.getmembers()
                            if m.isfile()
                            and self.detect(m.name) in (AssetKind.QCPIN, AssetKind.QCSTA)
                        ]
                        if not pin_members:
                            skipped += 1
                            continue
                        extract_dir.mkdir(parents=True, exist_ok=True)
                        for member in pin_members:
                            pin_name = Path(member.name).name
                            dest = extract_dir / pin_name
                            if not effective_override and self._catalog.by_local_path(UPath(dest)):
                                skipped += 1
                                continue
                            reader = tf.extractfile(member)
                            if reader is None:
                                skipped += 1
                                continue
                            dest.write_bytes(reader.read())
                            asset = AssetEntry(
                                kind=self.detect(member.name),
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

        return IngestReport(cataloged=cataloged, skipped=skipped, errors=tuple(errors))

    # ------------------------------------------------------------------
    # Remote discovery
    # ------------------------------------------------------------------

    def discover_remote(self) -> IngestReport:
        """Discover canonical EarthScope archive URLs and catalog them."""
        from earthscope_sfg_workflows.data_mgmt.archives.earthscope._archive_urls import (
            canonical_campaign_urls,
        )

        scope = self._s.scope
        cataloged = 0
        skipped = 0
        errors: list[str] = []

        for dir_url in canonical_campaign_urls(scope):
            sub = self._discover_archive(scope, dir_url)
            cataloged += sub.cataloged
            skipped += sub.skipped
            errors.extend(sub.errors)

        return IngestReport(cataloged=cataloged, skipped=skipped, errors=tuple(errors))

    def _discover_archive(self, scope: "SFGScope", directory_url: str) -> IngestReport:
        """List *directory_url* and catalog every recognized file."""
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

        return IngestReport(cataloged=cataloged, skipped=skipped, errors=tuple(errors))

    def list_archive_urls(self, scope=None) -> list[str]:
        """Enumerate every archive file URL for the scope without writing to the catalog."""
        from earthscope_sfg_workflows.data_mgmt.archives.earthscope._archive_urls import (
            list_campaign_archive_urls,
        )

        active_scope = scope if scope is not None else self._s.scope
        return list_campaign_archive_urls(self._archive, active_scope)

    # ------------------------------------------------------------------
    # Download
    # ------------------------------------------------------------------

    def download_remote(
        self,
        kinds: "list[AssetKind] | None" = None,
        *,
        override: bool | None = None,
        rinex_1hz: bool = False,
    ) -> IngestReport:
        """Download cataloged remote assets to local storage.

        *override* defaults to the value set at construction when not passed.
        """
        effective_override = self.override if override is None else override
        scope = self._s.scope
        layout = self._s.active_campaign_layout

        candidates = self._collect_remote_candidates(scope, kinds)
        if not effective_override:
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
            report = self._download_s3_files(s3_assets, layout)
            downloaded += report.downloaded
            skipped += report.skipped
            errors.extend(report.errors)

        if http_assets:
            report = self._download_http_files(http_assets, layout)
            downloaded += report.downloaded
            skipped += report.skipped
            errors.extend(report.errors)

        return IngestReport(downloaded=downloaded, skipped=skipped, errors=tuple(errors))

    def _collect_remote_candidates(
        self,
        scope: "SFGScope",
        kinds: "list[AssetKind] | None",
    ) -> "list[AssetEntry]":
        if kinds is None:
            return [a for a in self._catalog.assets_for(scope) if a.remote_path]
        out: list[AssetEntry] = []
        for kind in kinds:
            out.extend(a for a in self._catalog.assets_for(scope, kind) if a.remote_path)
        return out

    def _download_s3_files(
        self,
        s3_assets: list[AssetEntry],
        layout,
    ) -> IngestReport:
        plan: list[dict] = []
        for asset in s3_assets:
            assert asset.remote_path is not None
            _path = Path(asset.remote_path)
            local_dir = layout.intermediate if asset.kind is AssetKind.RINEX2 else layout.raw
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

    def _download_http_files(
        self,
        http_assets: list[AssetEntry],
        layout,
    ) -> IngestReport:
        downloaded = 0
        errors: list[str] = []

        for asset in tqdm(http_assets, desc="Downloading files"):
            local_dir = layout.intermediate if asset.kind is AssetKind.RINEX2 else layout.raw
            self._file_backend.mkdir(UPath(local_dir))
            assert asset.remote_path is not None
            local_path = self._download_http_file(asset.remote_path, Path(str(local_dir)))
            if local_path is not None and asset.id is not None:
                self._catalog.update(asset.with_local_path(UPath(local_path)))
                downloaded += 1
            else:
                errors.append(f"HTTP download failed for {asset.remote_path}")

        return IngestReport(downloaded=downloaded, errors=tuple(errors))

    def _download_http_file(self, remote_url: str, local_dir: Path) -> Path | None:
        local_path = local_dir / Path(remote_url).name
        try:
            self._archive.download_file(remote_url, local_path)
            if not local_path.exists():
                raise FileNotFoundError(f"{local_path} not created after download")
            return local_path
        except Exception:
            return None


__all__ = ["IngestService"]

