"""In-memory adapters for the data_mgmt ports.

Used by the test suite and by callers that want to exercise the data_mgmt
core without touching disk, network, or a database. They implement the same
public contracts as the production adapters and are interchangeable at the
``Workflow`` / ``Ingestor`` construction site.
"""

from __future__ import annotations

import threading
from collections import defaultdict
from dataclasses import replace
from itertools import count
from pathlib import Path

from ..model import ArchiveFile, AssetEntry, AssetKind, CampaignScope, FileInfo
from ..ports import ArchiveNotFoundError


# ---------------------------------------------------------------------------
# InMemoryAssetStore
# ---------------------------------------------------------------------------


class InMemoryAssetStore:
    """Thread-safe in-memory implementation of :class:`AssetStore`."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._rows: dict[int, AssetEntry] = {}
        self._ids = count(start=1)

    def add(self, asset: AssetEntry) -> AssetEntry:
        with self._lock:
            new_id = next(self._ids)
            stored = asset.with_id(new_id)
            self._rows[new_id] = stored
            return stored

    def update(self, asset: AssetEntry) -> bool:
        if asset.id is None:
            return False
        with self._lock:
            if asset.id not in self._rows:
                return False
            self._rows[asset.id] = asset
            return True

    def by_id(self, asset_id: int) -> AssetEntry | None:
        with self._lock:
            return self._rows.get(asset_id)

    def by_local_path(self, path: Path) -> list[AssetEntry]:
        with self._lock:
            return [a for a in self._rows.values() if a.local_path == path]

    def assets_for(
        self,
        scope: CampaignScope,
        kind: AssetKind | None = None,
    ) -> list[AssetEntry]:
        with self._lock:
            out: list[AssetEntry] = []
            for a in self._rows.values():
                if a.scope.tuple != scope.tuple:
                    continue
                if kind is not None and a.kind != kind:
                    continue
                out.append(a)
            out.sort(key=lambda a: a.id or 0)
            return out

    def delete(
        self,
        scope: CampaignScope,
        kind: AssetKind | None = None,
    ) -> int:
        with self._lock:
            doomed = [
                aid
                for aid, a in self._rows.items()
                if a.scope.tuple == scope.tuple and (kind is None or a.kind == kind)
            ]
            for aid in doomed:
                del self._rows[aid]
            return len(doomed)

    def count_by_kind(self, scope: CampaignScope) -> dict[AssetKind, int]:
        with self._lock:
            counts: dict[AssetKind, int] = defaultdict(int)
            for a in self._rows.values():
                if a.scope.tuple == scope.tuple:
                    counts[a.kind] += 1
            return dict(counts)

    def close(self) -> None:  # no-op
        return None


# ---------------------------------------------------------------------------
# InMemoryFileStore
# ---------------------------------------------------------------------------


class InMemoryFileStore:
    """Tree-shaped in-memory filesystem.

    Paths are normalized to absolute via ``Path.resolve(strict=False)`` only
    when they're already absolute; otherwise stored as-is. Directories are
    tracked separately from files so ``mkdir`` and ``write_bytes`` semantics
    line up with a real filesystem.
    """

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._files: dict[Path, bytes] = {}
        self._dirs: set[Path] = set()

    # -- query -------------------------------------------------------------

    def exists(self, path: Path) -> bool:
        with self._lock:
            return path in self._files or path in self._dirs

    def is_file(self, path: Path) -> bool:
        with self._lock:
            return path in self._files

    def is_dir(self, path: Path) -> bool:
        with self._lock:
            return path in self._dirs

    def list_files(self, directory: Path, recursive: bool = False) -> list[FileInfo]:
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

    def get_size(self, path: Path) -> int | None:
        with self._lock:
            data = self._files.get(path)
            return None if data is None else len(data)

    # -- mutation ----------------------------------------------------------

    def mkdir(self, path: Path, parents: bool = True) -> None:
        with self._lock:
            if parents:
                cur = path
                while cur != cur.parent:
                    self._dirs.add(cur)
                    cur = cur.parent
            else:
                self._dirs.add(path)

    def read_bytes(self, path: Path) -> bytes:
        with self._lock:
            try:
                return self._files[path]
            except KeyError as exc:
                raise FileNotFoundError(path) from exc

    def write_bytes(self, path: Path, data: bytes) -> None:
        with self._lock:
            # Auto-create parents to mirror typical "open(..., 'wb')" + mkdir
            # callsites.
            self.mkdir(path.parent, parents=True)
            self._files[path] = data

    def remove(self, path: Path) -> bool:
        with self._lock:
            return self._files.pop(path, None) is not None

    def close(self) -> None:
        return None


# ---------------------------------------------------------------------------
# FakeArchive
# ---------------------------------------------------------------------------


class FakeArchive:
    """In-memory :class:`ArchiveSource`. Seed with ``url -> bytes`` mappings.

    Directory listings are computed by URL prefix (treating any URL whose
    parent prefix matches ``directory_url`` as a child).
    """

    def __init__(self, files: dict[str, bytes] | None = None) -> None:
        self._files: dict[str, bytes] = dict(files or {})
        self._authenticated = False

    def seed(self, url: str, data: bytes) -> None:
        self._files[url] = data

    def list_files(self, directory_url: str) -> list[ArchiveFile]:
        prefix = directory_url.rstrip("/") + "/"
        out: list[ArchiveFile] = []
        for url, data in self._files.items():
            if not url.startswith(prefix):
                continue
            tail = url[len(prefix) :]
            if "/" in tail:  # nested; not a direct child
                continue
            out.append(ArchiveFile(url=url, size_bytes=len(data)))
        out.sort(key=lambda af: af.url)
        return out

    def download_file(self, file_url: str, dest_path: Path) -> None:
        if file_url not in self._files:
            raise ArchiveNotFoundError(file_url)
        dest_path.parent.mkdir(parents=True, exist_ok=True)
        dest_path.write_bytes(self._files[file_url])

    def authenticate(self, profile: str | None = None) -> bool:
        self._authenticated = True
        return True

    def close(self) -> None:
        return None


__all__ = [
    "InMemoryAssetStore",
    "InMemoryFileStore",
    "FakeArchive",
]
