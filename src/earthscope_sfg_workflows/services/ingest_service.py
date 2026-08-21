"""IngestService — data ingest operations for a StationSession."""

from __future__ import annotations

import concurrent.futures
import tarfile
import threading
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING

import boto3
import fsspec
from rich.progress import track
from upath import UPath

from earthscope_sfg_workflows.data_mgmt.core import FileTypeDetector
from earthscope_sfg_workflows.data_mgmt.model import AssetEntry, AssetKind, IngestReport, SFGScope
from earthscope_sfg_workflows.data_mgmt.ports import ArchiveError, ArchiveNotFoundError
from earthscope_sfg_workflows.logging import ProcessLogger

if TYPE_CHECKING:
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

    ``override`` is stored at construction and applied to every operation that
    supports it (e.g. :meth:`qcpin_tarballs` and :meth:`download_remote`).

    Attributes
    ----------
    override : bool
        When ``True``, re-ingest assets that are already cataloged or already
        exist on disk.

    Methods
    -------
    detect(filename)
        Return the first matching ``AssetKind`` for *filename*, or ``None``.
    local(source_dir)
        Catalog every recognized file under *source_dir*.
    qcpin_tarballs(tarball_dir, override)
        Extract ``.pin``/``.sta`` files from ``.tar.gz`` tarballs and catalog them.
    download_qc_zip(dest_dir, override)
        Download the campaign's qc.zip bundle from the EarthScope archive.
    extract_qc_zip(zip_path, override)
        Extract nested .tar.gz members from qc.zip and catalog QC pin/sta files.
    ingest_qc_zip(download, override)
        Download, extract, and catalog a campaign's qc.zip bundle end-to-end.
    discover_remote()
        Discover canonical EarthScope archive URLs and catalog them.
    discover_ctd()
        Discover and catalog only CTD files from the campaign's metadata/ctd directory.
    ingest_ctd_only(override)
        Discover and download only CTD files for the active campaign.
    list_archive_urls(scope)
        Enumerate every archive file URL for the scope without writing to the catalog.
    download_remote(kinds, override, rinex_1hz)
        Download cataloged remote assets to local storage.
    """

    def __init__(self, session: "StationSession", *, override: bool = False) -> None:
        """Initialize the service.

        Parameters
        ----------
        session : StationSession
            The active station session providing catalog, file, and archive ports.
        override : bool, optional
            When ``True``, skip deduplication checks so existing assets are
            re-ingested or re-downloaded. Default is ``False``.
        """
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
        """Return the first matching :class:`AssetKind`, or ``None``.

        Parameters
        ----------
        filename : str
            Bare filename (no directory component) to classify.

        Returns
        -------
        AssetKind or None
            The matched asset kind, or ``None`` if no pattern matches.
        """
        return self._detector.detect(filename)

    # ------------------------------------------------------------------
    # Local ingest
    # ------------------------------------------------------------------

    def local(self, source_dir: Path) -> IngestReport:
        """Catalog every recognized file under *source_dir*.

        Parameters
        ----------
        source_dir : Path
            Root directory to scan recursively for ingestable files.

        Returns
        -------
        IngestReport
            Summary of cataloged, skipped, and errored items.
        """
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

        Parameters
        ----------
        tarball_dir : Path or None, optional
            Directory containing ``.tar.gz`` tarballs. When ``None`` the
            campaign layout's ``qc`` directory is used. Default is ``None``.
        override : bool or None, optional
            When ``True``, re-extract and re-catalog assets that already exist.
            Defaults to the ``override`` value set at construction.

        Returns
        -------
        IngestReport
            Summary of cataloged, skipped, and errored items.

        Raises
        ------
        ValueError
            If *tarball_dir* is ``None`` and no campaign with a layout is active.
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

        tarballs = sorted(p for p in tarball_dir.glob("*.tar.gz") if not p.name.startswith("._"))

        for tb in tarballs:
            extract_dir = tarball_dir / tb.name.removesuffix(".tar.gz")
            try:
                with fsspec.open(str(tb), "rb") as fo:
                    with tarfile.open(fileobj=fo, mode="r:*") as tf:
                        pin_members = [
                            m
                            for m in tf.getmembers()
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
    # QC zip ingest (download + extract + catalog)
    # ------------------------------------------------------------------

    def download_qc_zip(
        self,
        dest_dir: Path | None = None,
        *,
        override: bool | None = None,
    ) -> Path | None:
        """Download the campaign's ``qc.zip`` bundle from the EarthScope archive.

        Parameters
        ----------
        dest_dir : Path or None, optional
            Directory to download ``qc.zip`` into. When ``None`` the campaign
            layout's ``qc`` directory is used. Default is ``None``.
        override : bool or None, optional
            When ``True``, re-download even if ``qc.zip`` already exists at
            the destination. Defaults to the ``override`` value set at
            construction.

        Returns
        -------
        Path or None
            Local path of the downloaded (or already-present) ``qc.zip``, or
            ``None`` if ``qc.zip`` could not be fetched from the archive —
            either because it doesn't exist (HTTP 404) or because the
            request otherwise failed (e.g. HTTP 403). In the latter case a
            warning is logged so the failure isn't silently swallowed.

        Raises
        ------
        ValueError
            If *dest_dir* is ``None`` and no campaign with a layout is active.
        """
        from earthscope_sfg_workflows.data_mgmt.archives.earthscope_archive import (
            campaign_qc_zip_url,
        )

        effective_override = self.override if override is None else override
        scope = self._s.scope
        if dest_dir is None:
            layout = self._s.active_campaign_layout
            if layout is None:
                raise ValueError("download_qc_zip requires a campaign with a layout")
            dest_dir = Path(layout.qc)

        dest_dir = Path(dest_dir)
        dest_path = dest_dir / "qc.zip"

        if not effective_override and dest_path.exists():
            return dest_path

        url = campaign_qc_zip_url(scope)
        try:
            self._archive.download_file(url, dest_path)
        except ArchiveNotFoundError:
            return None
        except ArchiveError as exc:
            ProcessLogger.warning(
                f"Failed to download {url}: {exc}; falling back to individual "
                "qc tarballs."
            )
            return None
        return dest_path

    def extract_qc_zip(
        self,
        zip_path: Path | None = None,
        *,
        override: bool | None = None,
    ) -> IngestReport:
        """Extract nested ``.tar.gz`` members from ``qc.zip`` and catalog QC files.

        Opens *zip_path*, extracts every member whose name ends in
        ``.tar.gz`` (case-insensitive) into the campaign's ``qc`` directory —
        flattening any internal directory structure — then delegates to
        :meth:`qcpin_tarballs` to extract and catalog the ``.pin``/``.sta``
        members of each tarball. All other zip members are ignored (counted
        as skipped).

        Parameters
        ----------
        zip_path : Path or None, optional
            Path to a local ``qc.zip`` file. When ``None`` defaults to the
            campaign layout's ``qc`` directory joined with ``"qc.zip"``.
            Default is ``None``.
        override : bool or None, optional
            When ``True``, re-extract tarballs and re-catalog assets that
            already exist. Defaults to the ``override`` value set at
            construction.

        Returns
        -------
        IngestReport
            Summary of cataloged, skipped, and errored items (from tarball
            extraction plus zip-member skips).

        Raises
        ------
        ValueError
            If *zip_path* is ``None`` and no campaign with a layout is active.
        """
        effective_override = self.override if override is None else override
        if zip_path is None:
            layout = self._s.active_campaign_layout
            if layout is None:
                raise ValueError("extract_qc_zip requires a campaign with a layout")
            zip_path = Path(layout.qc) / "qc.zip"

        zip_path = Path(zip_path)
        if not zip_path.is_file():
            return IngestReport(errors=(f"Not a file: {zip_path}",))

        tarball_dir = zip_path.parent
        skipped = 0
        errors: list[str] = []

        try:
            with zipfile.ZipFile(zip_path) as zf:
                members = [m for m in zf.infolist() if not m.is_dir()]
                tar_members = [m for m in members if m.filename.lower().endswith(".tar.gz")]
                skipped += len(members) - len(tar_members)
                for member in tar_members:
                    dest = tarball_dir / Path(member.filename).name
                    if not effective_override and dest.exists():
                        skipped += 1
                        continue
                    with zf.open(member) as src:
                        dest.write_bytes(src.read())
        except Exception as exc:
            errors.append(f"failed to open qc.zip {zip_path}: {exc}")
            return IngestReport(skipped=skipped, errors=tuple(errors))

        report = self.qcpin_tarballs(tarball_dir=tarball_dir, override=override)
        return IngestReport(skipped=skipped, errors=tuple(errors)) + report

    def ingest_qc_zip(
        self,
        *,
        download: bool = True,
        override: bool | None = None,
    ) -> IngestReport:
        """Download (optionally), extract, and catalog a campaign's ``qc.zip`` bundle.

        Parameters
        ----------
        download : bool, optional
            When ``True`` (default), fetch ``qc.zip`` from the archive first
            via :meth:`download_qc_zip`. When ``False``, extract whatever
            ``qc.zip`` already exists locally (useful for re-running
            extraction/cataloging without a network round-trip).
        override : bool or None, optional
            When ``True``, re-download, re-extract, and re-catalog even if
            already present. Defaults to the ``override`` value set at
            construction.

        Returns
        -------
        IngestReport
            Summary of cataloged, skipped, and errored items. Returns an
            empty (all-zero) report if the campaign has no ``qc.zip`` on the
            archive.
        """
        if download:
            zip_path = self.download_qc_zip(override=override)
            if zip_path is None:
                return IngestReport()
        else:
            layout = self._s.active_campaign_layout
            if layout is None:
                raise ValueError("ingest_qc_zip requires a campaign with a layout")
            zip_path = Path(layout.qc) / "qc.zip"

        return self.extract_qc_zip(zip_path=zip_path, override=override)

    def download_qc_tarballs(
        self,
        dest_dir: Path | None = None,
        *,
        override: bool | None = None,
    ) -> IngestReport:
        """Download individual ``.tar.gz`` QC bundles from the campaign's ``qc`` directory.

        Lists the archive's ``qc`` directory (sibling to ``qc.zip``) and
        downloads any ``.tar.gz`` file not already present in *dest_dir*.
        Rerunning only fetches tarballs that weren't previously downloaded.

        Parameters
        ----------
        dest_dir : Path or None, optional
            Directory to download tarballs into. When ``None`` the campaign
            layout's ``qc`` directory is used. Default is ``None``.
        override : bool or None, optional
            When ``True``, re-download tarballs that already exist locally.
            Defaults to the ``override`` value set at construction.

        Returns
        -------
        IngestReport
            ``downloaded`` is the number of tarballs fetched, ``skipped`` the
            number already present locally. Returns an empty report if the
            campaign has no ``qc`` directory on the archive.

        Raises
        ------
        ValueError
            If *dest_dir* is ``None`` and no campaign with a layout is active.
        """
        from earthscope_sfg_workflows.data_mgmt.archives.earthscope_archive import (
            campaign_qc_dir_url,
        )

        effective_override = self.override if override is None else override
        scope = self._s.scope
        if dest_dir is None:
            layout = self._s.active_campaign_layout
            if layout is None:
                raise ValueError("download_qc_tarballs requires a campaign with a layout")
            dest_dir = Path(layout.qc)

        dest_dir = Path(dest_dir)
        try:
            archive_files = self._archive.list_files(campaign_qc_dir_url(scope))
        except ArchiveNotFoundError:
            return IngestReport()

        downloaded = 0
        skipped = 0
        errors: list[str] = []
        for af in archive_files:
            if not af.filename.lower().endswith(".tar.gz"):
                continue
            dest_path = dest_dir / af.filename
            if not effective_override and dest_path.exists():
                skipped += 1
                continue
            try:
                self._archive.download_file(af.url, dest_path)
                downloaded += 1
            except ArchiveError as exc:
                errors.append(f"failed to download {af.url}: {exc}")

        return IngestReport(downloaded=downloaded, skipped=skipped, errors=tuple(errors))

    def ingest_qc(
        self,
        *,
        download: bool = True,
        override: bool | None = None,
    ) -> IngestReport:
        """Ingest a campaign's QC bundle, preferring ``qc.zip`` with a tarball fallback.

        Tries :meth:`ingest_qc_zip` first. If the campaign has no ``qc.zip``
        on the archive, falls back to downloading individual ``.tar.gz``
        files from the campaign's ``qc`` directory (via
        :meth:`download_qc_tarballs`) and cataloging them (via
        :meth:`qcpin_tarballs`).

        Rerunning is safe and incremental: already-downloaded tarballs and
        already-cataloged ``.pin``/``.sta`` files are skipped, while newly
        published tarballs are picked up and cataloged without re-fetching
        or re-processing anything already found.

        Parameters
        ----------
        download : bool, optional
            When ``True`` (default), fetch from the archive first (``qc.zip``
            or new tarballs). When ``False``, only process what's already
            present in the campaign's ``qc`` directory.
        override : bool or None, optional
            When ``True``, re-download, re-extract, and re-catalog even if
            already present. Defaults to the ``override`` value set at
            construction.

        Returns
        -------
        IngestReport
            Summary of cataloged, downloaded, skipped, and errored items.

        Raises
        ------
        ValueError
            If no campaign with a layout is active.
        """
        layout = self._s.active_campaign_layout
        if layout is None:
            raise ValueError("ingest_qc requires a campaign with a layout")
        qc_dir = Path(layout.qc)

        if download:
            zip_path = self.download_qc_zip(override=override)
            if zip_path is not None:
                return self.extract_qc_zip(zip_path=zip_path, override=override)
            return self.download_qc_tarballs(override=override) + self.qcpin_tarballs(
                override=override
            )

        zip_path = qc_dir / "qc.zip"
        if zip_path.is_file():
            return self.extract_qc_zip(zip_path=zip_path, override=override)
        return self.qcpin_tarballs(override=override)

    # ------------------------------------------------------------------
    # Remote discovery
    # ------------------------------------------------------------------

    def discover_remote(self) -> IngestReport:
        """Discover canonical EarthScope archive URLs and catalog them.

        Walks ``raw``, ``metadata``, the legacy ``metadata/ctd`` subdirectory,
        ``rinex_1Hz``, and ``rinex_10Hz``. Missing directories (e.g. a
        campaign with no CTD data) contribute an error entry to the returned
        report rather than raising.

        Returns
        -------
        IngestReport
            Summary of cataloged, skipped, and errored items.
        """
        from earthscope_sfg_workflows.data_mgmt.archives.earthscope_archive import (
            canonical_campaign_urls,
        )

        scope = self._s.scope
        raw_url, metadata_url, rinex_1hz_url, rinex_10hz_url = canonical_campaign_urls(scope)
        cataloged = 0
        skipped = 0
        errors: list[str] = []

        for dir_url in (
            raw_url,
            metadata_url,
            f"{metadata_url}/ctd",
            rinex_1hz_url,
            rinex_10hz_url,
        ):
            sub = self._discover_archive(scope, dir_url)
            cataloged += sub.cataloged
            skipped += sub.skipped
            errors.extend(sub.errors)

        return IngestReport(cataloged=cataloged, skipped=skipped, errors=tuple(errors))

    def discover_ctd(self) -> IngestReport:
        """Discover and catalog CTD files for the active campaign.

        If the active campaign has no CTD data on the archive, falls back to
        the most recent earlier campaign for the same station that does, and
        catalogs its CTD files under the active campaign's scope so
        downstream SVP processing picks them up transparently. Logs which
        campaign's CTD was used when the fallback triggers.

        Returns
        -------
        IngestReport
            Summary of cataloged, skipped, and errored items.
        """
        from earthscope_sfg_workflows.data_mgmt.archives.earthscope_archive import (
            canonical_campaign_urls,
        )

        scope = self._s.scope
        _, metadata_url, _, _ = canonical_campaign_urls(scope)
        report = self._discover_archive(scope, f"{metadata_url}/ctd")
        if report.cataloged > 0:
            return report

        return report + self._discover_ctd_from_previous_campaign(scope)

    def _discover_ctd_from_previous_campaign(self, scope: "SFGScope") -> IngestReport:
        """Search earlier campaigns for the same station for CTD data.

        Tries each earlier campaign for the station (most recent year first,
        using the site metadata already cached on the session), stopping at
        the first one that yields CTD files. Matches are cataloged under
        *scope* (the active campaign), not the campaign they were found in.

        Parameters
        ----------
        scope : SFGScope
            Active campaign scope; found CTD files are cataloged under this
            scope regardless of which earlier campaign they came from.

        Returns
        -------
        IngestReport
            Summary of cataloged and errored items, or an empty report if no
            earlier campaign has CTD data.
        """
        from earthscope_sfg_workflows.data_mgmt.archives.earthscope_archive import (
            canonical_campaign_urls,
        )

        site = self._s.site
        if site is None:
            return IngestReport()

        def _year(campaign_name: str) -> str:
            return campaign_name.split("_", 1)[0]

        current_year = _year(scope.campaign)
        candidates = sorted(
            (
                c
                for c in site.campaigns
                if c.name != scope.campaign and _year(c.name) < current_year
            ),
            key=lambda c: (_year(c.name), c.start),
            reverse=True,
        )

        for candidate in candidates:
            candidate_scope = SFGScope(
                network=scope.network, station=scope.station, campaign=candidate.name
            )
            _, metadata_url, _, _ = canonical_campaign_urls(candidate_scope)
            try:
                archive_files = self._archive.list_files(f"{metadata_url}/ctd")
            except ArchiveError:
                continue

            cataloged = 0
            errors: list[str] = []
            for af in archive_files:
                if self.detect(af.filename) is not AssetKind.CTD:
                    continue
                asset = AssetEntry(
                    kind=AssetKind.CTD,
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

            if cataloged:
                ProcessLogger.info(
                    f"No CTD found for {scope.network} {scope.station} {scope.campaign}; "
                    f"using {cataloged} CTD file(s) from earlier campaign {candidate.name} "
                    f"instead."
                )
                return IngestReport(cataloged=cataloged, errors=tuple(errors))

        return IngestReport()

    def ingest_ctd_only(self, *, override: bool | None = None) -> IngestReport:
        """Discover and download only CTD files for the active campaign.

        Parameters
        ----------
        override : bool or None, optional
            When ``True``, re-download files that already exist locally.
            Defaults to the ``override`` value set at construction.

        Returns
        -------
        IngestReport
            Combined summary of the discovery and download steps.
        """
        discover_report = self.discover_ctd()
        download_report = self.download_remote(kinds=[AssetKind.CTD], override=override)
        return discover_report + download_report

    def _discover_archive(self, scope: "SFGScope", directory_url: str) -> IngestReport:
        """List *directory_url* and catalog every recognized file.

        Parameters
        ----------
        scope : SFGScope
            Active scope used to tag each cataloged asset.
        directory_url : str
            Archive directory URL to enumerate.

        Returns
        -------
        IngestReport
            Summary of cataloged, skipped, and errored items.
        """
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
        """Enumerate every archive file URL for the scope without writing to the catalog.

        Parameters
        ----------
        scope : SFGScope or None, optional
            Scope to enumerate. Defaults to the session's active scope when
            ``None``.

        Returns
        -------
        list of str
            All archive file URLs found for the given scope.
        """
        from earthscope_sfg_workflows.data_mgmt.archives.earthscope_archive import (
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

        Parameters
        ----------
        kinds : list of AssetKind or None, optional
            Asset kinds to restrict the download to. When ``None`` all
            cataloged remote assets are considered. Default is ``None``.
        override : bool or None, optional
            When ``True``, re-download files that already exist locally.
            Defaults to the ``override`` value set at construction.
        rinex_1hz : bool, optional
            When ``True``, download only high-rate (1 Hz) RINEX files.
            When ``False``, skip 1 Hz RINEX files. Default is ``False``.

        Returns
        -------
        IngestReport
            Summary of downloaded, skipped, and errored items.
        """
        effective_override = self.override if override is None else override
        scope = self._s.scope
        layout = self._s.active_campaign_layout

        candidates = self._collect_remote_candidates(scope, kinds)
        if not effective_override:
            candidates = [
                a
                for a in candidates
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
            return [
                a
                for a in self._catalog.assets_for(
                    network=scope.network,
                    station=scope.station,
                    campaign=scope.campaign,
                )
                if a.remote_path
            ]
        out: list[AssetEntry] = []
        for kind in kinds:
            out.extend(
                a
                for a in self._catalog.assets_for(
                    kind,
                    network=scope.network,
                    station=scope.station,
                    campaign=scope.campaign,
                )
                if a.remote_path
            )
        return out

    def _download_s3_files(
        self,
        s3_assets: list[AssetEntry],
        layout,
    ) -> IngestReport:
        """Download a list of S3-backed assets using a thread pool.

        Parameters
        ----------
        s3_assets : list of AssetEntry
            Assets whose ``remote_type`` is ``"s3"``.
        layout : CampaignLayout
            Active campaign layout providing ``raw`` and ``intermediate``
            destination directories.

        Returns
        -------
        IngestReport
            Summary of downloaded and errored items.
        """
        plan: list[dict] = []
        for asset in s3_assets:
            assert asset.remote_path is not None
            _path = Path(asset.remote_path)
            local_dir = layout.intermediate if asset.kind is AssetKind.RINEX2 else layout.raw
            bucket = _path.root
            plan.append(
                {
                    "bucket": bucket,
                    "prefix": str(_path.relative_to(bucket)),
                    "local_dir": local_dir,
                }
            )

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
        """Download a single S3 object described by *plan*.

        Parameters
        ----------
        plan : dict
            Mapping with keys ``"bucket"``, ``"prefix"``, and ``"local_dir"``.

        Returns
        -------
        Path or None
            Local path of the downloaded file, or ``None`` on failure.
        """
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
        """Download a list of HTTP-backed assets sequentially with a progress bar.

        Parameters
        ----------
        http_assets : list of AssetEntry
            Assets whose ``remote_type`` is ``"http"``.
        layout : CampaignLayout
            Active campaign layout providing ``raw`` and ``intermediate``
            destination directories.

        Returns
        -------
        IngestReport
            Summary of downloaded and errored items.
        """
        downloaded = 0
        errors: list[str] = []

        for asset in track(http_assets, description="Downloading files"):
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
        """Download a single HTTP file into *local_dir*.

        Parameters
        ----------
        remote_url : str
            Fully-qualified HTTP URL of the file to download.
        local_dir : Path
            Directory where the file will be saved; the filename is taken from
            the URL.

        Returns
        -------
        Path or None
            Local path of the downloaded file, or ``None`` on failure.
        """
        local_path = local_dir / Path(remote_url).name
        try:
            self._archive.download_file(remote_url, local_path)
            if not local_path.exists():
                raise FileNotFoundError(f"{local_path} not created after download")
            return local_path
        except Exception:
            return None


__all__ = ["IngestService"]
