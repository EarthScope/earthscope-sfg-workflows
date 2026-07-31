"""SyncService — remote sync operations for a StationSession."""

from __future__ import annotations

from typing import TYPE_CHECKING

from upath import UPath

from earthscope_sfg_workflows.logging import GarposLogger as logger

if TYPE_CHECKING:
    from earthscope_sfg_workflows.data_mgmt.ports import FileStorePort
    from earthscope_sfg_workflows.workflows.session import StationSession


class SyncService:
    """Remote push/pull operations scoped to a :class:`StationSession`.

    Holds the file-backend ports directly so all push/pull logic lives here
    without delegating to ``FileManager``.

    Requires that the session's remote backend has been configured via
    :meth:`~earthscope_sfg_workflows.workflows.session.StationSession.configure_remote`
    before calling any method.

    Attributes
    ----------
    _s : StationSession
        The bound station session.
    _fm : FileManager
        File manager providing local and remote backend access.

    Methods
    -------
    push_station(overwrite)
        Upload TileDB arrays for the current station to the remote backend.
    push_campaign(overwrite)
        Upload SVP, RINEX, and log files for the active campaign.
    pull(overwrite)
        Download TileDB arrays and active campaign files from the remote mirror.
    """

    def __init__(self, session: "StationSession") -> None:
        """Initialize the service.

        Parameters
        ----------
        session : StationSession
            The active station session providing file manager and scope
            information.
        """
        self._s = session
        self._fm = session._file_manager

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @property
    def _has_remote(self) -> bool:
        return self._fm.has_remote

    def _remote_path_for(self, local_path: UPath) -> UPath | None:
        return self._fm.remote_path_for(local_path)

    def _push_dir(self, local_root: UPath, overwrite: bool = False) -> int:
        """Upload all files under *local_root* to the mirrored remote path.

        Parameters
        ----------
        local_root : UPath
            Local directory to upload recursively.
        overwrite : bool, optional
            When ``True``, overwrite files that already exist on the remote.
            Default is ``False``.

        Returns
        -------
        int
            Number of files uploaded.
        """
        if not self._has_remote:
            return 0
        local_root = UPath(local_root)
        remote_root = self._remote_path_for(local_root)
        if remote_root is None:
            return 0
        remote_backend: FileStorePort = self._fm.remote_backend
        count = 0
        for info in self._fm.file_backend.list_files(local_root, recursive=True):
            local_path = UPath(info.path)
            remote_path = self._remote_path_for(local_path)
            if remote_path is None:
                continue
            if overwrite or not remote_backend.exists(remote_path):
                remote_backend.put_remote(local_path, str(remote_path))
                count += 1
        return count

    def _pull_dir(self, local_root: UPath, overwrite: bool = False) -> int:
        """Download all files from the mirrored remote path into *local_root*.

        Parameters
        ----------
        local_root : UPath
            Local directory that mirrors the remote tree.
        overwrite : bool, optional
            When ``True``, overwrite files that already exist locally.
            Default is ``False``.

        Returns
        -------
        int
            Number of files downloaded.
        """
        if not self._has_remote:
            return 0
        local_root = UPath(local_root)
        remote_root = self._remote_path_for(local_root)
        if remote_root is None:
            return 0
        remote_backend: FileStorePort = self._fm.remote_backend
        count = 0
        for info in remote_backend.list_files(remote_root, recursive=True):
            remote_path = UPath(info.path)
            try:
                rel = remote_path.relative_to(remote_root)
            except ValueError:
                rel = UPath(remote_path.name)
            local_path = local_root / rel
            if overwrite or not self._fm.file_backend.exists(local_path):
                self._fm.file_backend.get_remote(str(remote_path), local_path)
                count += 1
        return count

    # ------------------------------------------------------------------
    # Push
    # ------------------------------------------------------------------

    def push_station(self, overwrite: bool = False) -> None:
        """Upload TileDB arrays for the current station to the remote backend.

        Parameters
        ----------
        overwrite : bool, optional
            When ``True``, overwrite files that already exist on the remote.
            Default is ``False``.
        """
        if not self._has_remote:
            logger.warning("push_station: no remote configured, skipping.")
            return
        tiledb = self._s.tiledb_layout()
        for path in tiledb.all_paths:
            count = self._push_dir(UPath(path), overwrite=overwrite)
            logger.info(f"Pushed {count} files from {path}")

    def push_campaign(self, overwrite: bool = False) -> None:
        """Upload SVP, RINEX, and log files for the active campaign to the remote backend.

        Parameters
        ----------
        overwrite : bool, optional
            When ``True``, overwrite files that already exist on the remote.
            Default is ``False``.
        """
        if not self._has_remote:
            logger.warning("push_campaign: no remote configured, skipping.")
            return
        campaign = self._s.ensure_campaign()
        self._compress_rinex(UPath(campaign.intermediate))
        for dir_path in (campaign.processed, campaign.intermediate, campaign.logs):
            count = self._push_dir(UPath(dir_path), overwrite=overwrite)
            logger.info(f"Pushed {count} files from {dir_path}")

    # ------------------------------------------------------------------
    # Pull
    # ------------------------------------------------------------------

    def pull(self, overwrite: bool = False) -> None:
        """Download TileDB arrays and active campaign files from the remote mirror.

        Parameters
        ----------
        overwrite : bool, optional
            When ``True``, overwrite files that already exist locally.
            Default is ``False``.
        """
        if not self._has_remote:
            logger.warning("pull: no remote configured, skipping.")
            return
        tiledb = self._s.tiledb_layout()
        for path in tiledb.all_paths:
            count = self._pull_dir(UPath(path), overwrite=overwrite)
            logger.info(f"Pulled {count} files to {path}")
        if self._s.scope.campaign:
            campaign = self._s.ensure_campaign()
            count = self._pull_dir(UPath(campaign.root), overwrite=overwrite)
            logger.info(f"Pulled {count} campaign files to {campaign.root}")

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _compress_rinex(self, rinex_dir: UPath) -> None:
        """Compress uncompressed RINEX files in *rinex_dir* to CRINEX gz format.

        Parameters
        ----------
        rinex_dir : UPath
            Directory to scan for uncompressed RINEX files matching the active
            station name. Files that are already compressed (``.crx``, ``.gz``,
            etc.) are skipped.
        """
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
            if rinex_file.suffix.lower() == ".rnx":
                # RINEX v3/v4 long name: swap the whole extension for the
                # Hatanaka-compact one (e.g. "..._MO.rnx" -> "..._MO.crx").
                new_suffix = ".crx"
            else:
                # Legacy RINEX v2 short name: swap the trailing "o" for "d"
                # (e.g. ".26o" -> ".26d").
                new_suffix = rinex_file.suffix[:-1] + "d"
            compressed = rinex_file.with_suffix(new_suffix + ".gz")
            if not compressed.exists():
                try:
                    crinex_compress(rinex_file, compressed, gzip=True, logger=logger.logger)
                except Exception as e:
                    logger.error(f"Failed to compress {rinex_file}: {e}")


__all__ = ["SyncService"]
