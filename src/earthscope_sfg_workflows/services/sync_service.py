"""SyncService — remote sync operations for a StationSession."""
from __future__ import annotations

from typing import TYPE_CHECKING

from upath import UPath

from earthscope_sfg_workflows.logging import GarposLogger as logger

if TYPE_CHECKING:
    from earthscope_sfg_workflows.workflows.session import StationSession


class SyncService:
    """Remote push/pull operations scoped to a :class:`StationSession`.

    Requires that the session's remote backend has been configured via
    :meth:`~earthscope_sfg_workflows.workflows.session.StationSession.configure_remote`
    before calling any method.
    """

    def __init__(self, session: "StationSession") -> None:
        self._s = session

    # ------------------------------------------------------------------
    # Push
    # ------------------------------------------------------------------

    def push_station(self, overwrite: bool = False) -> None:
        """Upload TileDB arrays for the current station to the remote backend."""
        if not self._s._file_manager.has_remote:
            logger.warning("push_station: no remote configured, skipping.")
            return
        tiledb = self._s.tiledb_layout()
        for path in tiledb.all_paths:
            count = self._s._file_manager.push_dir(UPath(path), overwrite=overwrite)
            logger.info(f"Pushed {count} files from {path}")

    def push_campaign(self, overwrite: bool = False) -> None:
        """Upload SVP, RINEX, and log files for the active campaign to the remote backend."""
        if not self._s._file_manager.has_remote:
            logger.warning("push_campaign: no remote configured, skipping.")
            return
        campaign = self._s.ensure_campaign()
        self._compress_rinex(UPath(campaign.intermediate))
        for dir_path in (campaign.processed, campaign.intermediate, campaign.logs):
            count = self._s._file_manager.push_dir(UPath(dir_path), overwrite=overwrite)
            logger.info(f"Pushed {count} files from {dir_path}")

    # ------------------------------------------------------------------
    # Pull
    # ------------------------------------------------------------------

    def pull(self, overwrite: bool = False) -> None:
        """Download TileDB arrays and active campaign files from the remote mirror."""
        if not self._s._file_manager.has_remote:
            logger.warning("pull: no remote configured, skipping.")
            return
        tiledb = self._s.tiledb_layout()
        for path in tiledb.all_paths:
            count = self._s._file_manager.pull_dir(UPath(path), overwrite=overwrite)
            logger.info(f"Pulled {count} files to {path}")
        if self._s.scope.campaign:
            campaign = self._s.ensure_campaign()
            count = self._s._file_manager.pull_dir(UPath(campaign.root), overwrite=overwrite)
            logger.info(f"Pulled {count} campaign files to {campaign.root}")

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _compress_rinex(self, rinex_dir: UPath) -> None:
        """Compress uncompressed RINEX files in *rinex_dir* to CRINEX gz format."""
        from earthscope_sfg_tools.rinex_tools import crinex_compress

        rinex_dir = UPath(rinex_dir)
        if not rinex_dir.exists():
            return
        station_name = self._s.scope.station
        for rinex_file in rinex_dir.rglob(f"*{station_name}*"):
            if not rinex_file.is_file():
                continue
            if ".crx" in rinex_file.suffix:
                continue
            if any(ext in rinex_file.suffix for ext in ["S", "d", ".gz"]):
                continue
            new_suffix = rinex_file.suffix[:-1] + "d"
            compressed = rinex_file.with_suffix(new_suffix + ".gz")
            if not compressed.exists():
                try:
                    crinex_compress(rinex_file, compressed, gzip=True, logger=logger.logger)
                except Exception as e:
                    logger.error(f"Failed to compress {rinex_file}: {e}")


__all__ = ["SyncService"]

