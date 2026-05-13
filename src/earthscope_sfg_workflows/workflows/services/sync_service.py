"""SyncService — remote sync operations for a StationSession."""
from __future__ import annotations

from typing import TYPE_CHECKING

from earthscope_sfg_workflows.logging import GarposLogger as logger

if TYPE_CHECKING:
    from earthscope_sfg_workflows.workflows.session import StationSession


class SyncService:
    """Remote push/pull operations scoped to a :class:`StationSession`."""

    def __init__(self, session: "StationSession") -> None:
        self._s = session

    def configure(self, remote_root: str | None) -> None:
        """Set or clear the remote S3 root for this session."""
        self._s.configure_remote(remote_root)

    def push_station(self, *, overwrite: bool = False) -> None:
        """Upload TileDB arrays for the current station to the remote backend."""
        self._s.push_station_to_remote(overwrite=overwrite)

    def push_campaign(self, *, overwrite: bool = False) -> None:
        """Upload campaign files (SVP, RINEX, logs) to the remote backend."""
        self._s.push_campaign_to_remote(overwrite=overwrite)

    def pull(self, *, overwrite: bool = False) -> None:
        """Download TileDB arrays and campaign files from the remote mirror."""
        self._s.pull_from_remote(overwrite=overwrite)


__all__ = ["SyncService"]

