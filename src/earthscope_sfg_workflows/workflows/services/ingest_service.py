"""IngestService — data ingest operations for a StationSession."""
from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from earthscope_sfg_workflows.data_mgmt.model import AssetKind, IngestReport
    from earthscope_sfg_workflows.workflows.session import StationSession


class IngestService:
    """Ingest operations (local, remote, download) scoped to a :class:`StationSession`.

    Each method builds or reuses an ``Ingestor`` that is lazily cached on
    the service.  The cache is invalidated automatically when the session's
    campaign changes (detected by comparing scope at call time).
    """

    def __init__(self, session: "StationSession") -> None:
        self._s = session

    def local(self, source_dir: Path) -> "IngestReport":
        """Catalog files from *source_dir* into the session's asset catalog."""
        return self._s._ingestor.ingest_local(self._s.scope, source_dir)

    def qcpin_tarballs(
        self,
        tarball_dir: Path | None = None,
        *,
        override: bool = False,
    ) -> "IngestReport":
        """Extract ``.pin`` files from tarballs and register them as QCPIN assets."""
        if tarball_dir is None:
            if self._s.campaign.layout is None:
                raise ValueError("qcpin_tarballs requires a campaign with a layout")
            tarball_dir = Path(self._s.campaign.layout.qc)
        return self._s._ingestor.ingest_qcpin_tarballs(self._s.scope, tarball_dir, override=override)

    def discover_remote(self) -> "IngestReport":
        """Discover and catalog remote archive assets for the current scope."""
        return self._s._ingestor.discover_archive(self._s.scope)

    def download_remote(
        self,
        kinds: "list[AssetKind] | None" = None,
        *,
        override: bool = False,
        rinex_1hz: bool = False,
    ) -> "IngestReport":
        """Download cataloged remote assets to local storage."""
        dest_dir = self._s.campaign.layout.raw if self._s.campaign.layout else None
        return self._s._ingestor.download(
            self._s.scope,
            kinds=kinds,
            dest_dir=dest_dir,
            override=override,
            rinex_1hz=rinex_1hz,
        )

    def list_archive_urls(self, scope=None) -> list[str]:
        """List remote archive URLs without writing to the catalog."""
        from earthscope_sfg_workflows.data_mgmt.archives.earthscope._archive_urls import (
            list_campaign_archive_urls,
        )
        active_scope = scope if scope is not None else self._s.scope
        return list_campaign_archive_urls(self._s._archive, active_scope)


__all__ = ["IngestService"]

