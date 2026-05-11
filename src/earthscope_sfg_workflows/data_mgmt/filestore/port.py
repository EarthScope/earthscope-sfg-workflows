from typing import runtime_checkable, Protocol
from upath import UPath
from ..model import ArchiveFile, AssetEntry, AssetKind, SFGScope, FileInfo

@runtime_checkable
class FileStorePort(Protocol):
    """Filesystem abstraction. Implementations: local, S3, in-memory."""

    def exists(self, path: UPath) -> bool: ...
    def is_file(self, path: UPath) -> bool: ...
    def is_dir(self, path: UPath) -> bool: ...

    def list_files(
        self,
        directory: UPath,
        recursive: bool = False,
    ) -> list[FileInfo]:
        """List files under ``directory``. Hidden ``._*`` entries are excluded."""
        ...

    def get_remote(self, source: str, target: UPath) -> None:
        """Copy the file at ``source`` URL/path to the local ``target``."""
        ...

    def put_remote(self, source: UPath, target: str) -> None:
        """Copy the local ``source`` file to the remote ``target`` URL/path."""
        ...

    def mkdir(self, path: UPath, parents: bool = True) -> None:
        """Create ``path`` (and parents). Idempotent."""
        ...

    def remove(self, path: UPath) -> bool:
        """Delete a file. Return True if a file was deleted."""
        ...

    def get_size(self, path: UPath) -> int | None:
        """Size in bytes, or ``None`` if not a file."""
        ...

    def close(self) -> None: ...
