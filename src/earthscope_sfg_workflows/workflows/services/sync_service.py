"""SyncService — remote sync operations for a StationSession."""
from __future__ import annotations

from typing import TYPE_CHECKING

from earthscope_sfg_workflows.logging import GarposLogger as logger

if TYPE_CHECKING:
    from earthscope_sfg_workflows.data_mgmt.model import SFGScope
    from earthscope_sfg_workflows.data_mgmt.core import FileManager
    from earthscope_sfg_workflows.workflows.session import StationSession


class SyncService:
    """Remote push/pull operations scoped to a :class:`StationSession`.

    Sync logic is self-contained: ``push(scope)`` copies files from
    *source_fm* to *target_fm*; ``pull(scope)`` copies the other direction.
    """

    def __init__(
        self,
        session: "StationSession",
        *,
        source_fm: "FileManager",
        target_fm: "FileManager",
    ) -> None:
        self._s = session
        self._source_fm = source_fm
        self._target_fm = target_fm

    def push(self, scope: "SFGScope") -> int:
        """Copy all files for *scope* from source to target.

        Returns the number of files written.
        """
        source_root = self._source_fm.directory_tree.station_dir(
            network=scope.network, station=scope.station
        )
        written = 0
        for info in self._source_fm.file_backend.list_files(source_root, recursive=True):
            if not info.is_file:
                continue
            data = self._source_fm.file_backend.read_bytes(info.path)
            self._target_fm.file_backend.write_bytes(info.path, data)
            written += 1
        logger.info(f"SyncService.push: copied {written} file(s) to target")
        return written

    def pull(self, scope: "SFGScope") -> int:
        """Copy all files for *scope* from source to target.

        Returns the number of files written.
        """
        source_root = self._source_fm.directory_tree.station_dir(
            network=scope.network, station=scope.station
        )
        written = 0
        for info in self._source_fm.file_backend.list_files(source_root, recursive=True):
            if not info.is_file:
                continue
            data = self._source_fm.file_backend.read_bytes(info.path)
            self._target_fm.file_backend.write_bytes(info.path, data)
            written += 1
        logger.info(f"SyncService.pull: copied {written} file(s) to target")
        return written


__all__ = ["SyncService"]

