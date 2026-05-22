# sync_service

`earthscope_sfg_workflows.services.sync_service`

SyncService — remote sync operations for a StationSession.

## class `SyncService`

Remote push/pull operations scoped to a :class:`StationSession`.

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

**Methods**

### `SyncService.pull(self, overwrite: 'bool' = False) -> 'None'`

Download TileDB arrays and active campaign files from the remote mirror.

Parameters
----------
overwrite : bool, optional
    When ``True``, overwrite files that already exist locally.
    Default is ``False``.

### `SyncService.push_campaign(self, overwrite: 'bool' = False) -> 'None'`

Upload SVP, RINEX, and log files for the active campaign to the remote backend.

Parameters
----------
overwrite : bool, optional
    When ``True``, overwrite files that already exist on the remote.
    Default is ``False``.

### `SyncService.push_station(self, overwrite: 'bool' = False) -> 'None'`

Upload TileDB arrays for the current station to the remote backend.

Parameters
----------
overwrite : bool, optional
    When ``True``, overwrite files that already exist on the remote.
    Default is ``False``.

