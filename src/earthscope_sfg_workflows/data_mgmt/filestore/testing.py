import threading
from upath import UPath

from ..model import FileInfo

class InMemoryFileStore:
    """Tree-shaped in-memory filesystem.
    Paths are normalized to absolute via ``Path.resolve(strict=False)`` only
    when they're already absolute; otherwise stored as-is. Directories are
    tracked separately from files so ``mkdir`` and ``write_bytes`` semantics
    line up with a real filesystem.
    """

    def __init__(self) -> None:
        """Initialize empty file and directory tables."""
        self._lock = threading.RLock()
        self._files: dict[UPath, bytes] = {}
        self._dirs: set[UPath] = set()

    # -- query -------------------------------------------------------------

    def exists(self, path: UPath) -> bool:
        """Return True iff `path` names a known file or directory."""
        with self._lock:
            return path in self._files or path in self._dirs

    def is_file(self, path: UPath) -> bool:
        """Return True iff `path` names a known file."""
        with self._lock:
            return path in self._files

    def is_dir(self, path: UPath) -> bool:
        """Return True iff `path` names a known directory."""
        with self._lock:
            return path in self._dirs

    def list_files(self, directory: UPath, recursive: bool = False) -> list[FileInfo]:
        """List files under `directory`; recurse when `recursive` is True."""
        with self._lock:
            if directory not in self._dirs:
                # Permissive: also enumerate if any child files exist.
                has_children = any(p.parent == directory for p in self._files)
                if not has_children:
                    return []
            out: list[FileInfo] = []
            for p, data in self._files.items():
                if p.name.startswith("._"):
                    continue
                if recursive:
                    try:
                        p.relative_to(directory)
                    except ValueError:
                        continue
                else:
                    if p.parent != directory:
                        continue
                out.append(FileInfo(path=p, size_bytes=len(data), is_file=True))
            out.sort(key=lambda fi: fi.path.as_posix())
            return out

    def get_size(self, path: UPath) -> int | None:
        """Return the size of the file at `path`, or None if not a file."""
        with self._lock:
            data = self._files.get(path)
            return None if data is None else len(data)

    # -- mutation ----------------------------------------------------------

    def mkdir(self, path: UPath, parents: bool = True) -> None:
        """Create directory `path`; create parent directories iff `parents`."""
        with self._lock:
            if parents:
                cur = path
                while cur != cur.parent:
                    self._dirs.add(cur)
                    cur = cur.parent
            else:
                self._dirs.add(path)

    def read_bytes(self, path: UPath) -> bytes:
        """Return the bytes stored at `path`. Raises FileNotFoundError if missing."""
        with self._lock:
            try:
                return self._files[path]
            except KeyError as exc:
                raise FileNotFoundError(path) from exc

    def write_bytes(self, path: UPath, data: bytes) -> None:
        """Write `data` to `path`, auto-creating parent directories."""
        with self._lock:
            # Auto-create parents to mirror typical "open(..., 'wb')" + mkdir
            # callsites.
            self.mkdir(path.parent, parents=True)
            self._files[path] = data

    def get_remote(self, source: str, target: UPath) -> None:
        """Copy seeded bytes for ``source`` to the real filesystem at ``target``."""
        with self._lock:
            data = self._files.get(UPath(source))
        if data is None:
            raise FileNotFoundError(source)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)

    def put_remote(self, source: UPath, target: str) -> None:
        """Read ``source`` from the real filesystem and seed it under ``target``."""
        self.write_bytes(UPath(target), source.read_bytes())

    def remove(self, path: UPath) -> bool:
        """Remove the file at `path`; return True iff it existed."""
        with self._lock:
            return self._files.pop(path, None) is not None

    def close(self) -> None:
        """No-op for the in-memory store; present for port parity."""
        return None
