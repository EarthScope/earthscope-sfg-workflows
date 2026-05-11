# ---------------------------------------------------------------------------
# InMemoryAssetStore
# ---------------------------------------------------------------------------


class InMemoryAssetStore:
    """Thread-safe in-memory implementation of :class:`AssetStore`."""

    def __init__(self) -> None:
        """Initialize an empty store with a fresh ID counter."""
        self._lock = threading.RLock()
        self._rows: dict[int, AssetEntry] = {}
        self._ids = count(start=1)
        self._merge_jobs: set[tuple[str, str, str]] = set()

    def add(self, asset: AssetEntry) -> AssetEntry:
        """Insert `asset`, assigning it a new auto-increment id."""
        with self._lock:
            new_id = next(self._ids)
            stored = asset.with_id(new_id)
            self._rows[new_id] = stored
            return stored

    def update(self, asset: AssetEntry) -> bool:
        """Replace an existing row by id. Returns True iff the row existed."""
        if asset.id is None:
            return False
        with self._lock:
            if asset.id not in self._rows:
                return False
            self._rows[asset.id] = asset
            return True

    def by_id(self, asset_id: int) -> AssetEntry | None:
        """Look up an asset by its primary id, or None if missing."""
        with self._lock:
            return self._rows.get(asset_id)

    def by_local_path(self, path: Path) -> list[AssetEntry]:
        """Return all assets whose `local_path` equals `path`."""
        with self._lock:
            return [a for a in self._rows.values() if a.local_path == path]

    def assets_for(
        self,
        scope: CampaignScope,
        kind: AssetKind | None = None,
    ) -> list[AssetEntry]:
        """Return assets within `scope`, optionally filtered by `kind`."""
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
        """Delete assets in `scope` (optionally filtered by `kind`); return count."""
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
        """Return a per-`AssetKind` row count for assets in `scope`."""
        with self._lock:
            counts: dict[AssetKind, int] = defaultdict(int)
            for a in self._rows.values():
                if a.scope.tuple == scope.tuple:
                    counts[a.kind] += 1
            return dict(counts)

    def delete_by_id(self, asset_id: int) -> bool:
        """Delete a single asset by id; return True iff the row existed."""
        with self._lock:
            return self._rows.pop(asset_id, None) is not None

    # -- merge job tracking -----------------------------------------------

    @staticmethod
    def _merge_signature(parent_ids: list[int] | list[str]) -> str:
        return "-".join(sorted(str(x) for x in parent_ids))

    def add_merge_job(
        self,
        parent_type: str,
        child_type: str,
        parent_ids: list[int] | list[str],
    ) -> None:
        """Record that a merge job for `(parent_type, child_type, parents)` ran."""
        sig = (parent_type, child_type, self._merge_signature(parent_ids))
        with self._lock:
            self._merge_jobs.add(sig)

    def is_merge_complete(
        self,
        parent_type: str,
        child_type: str,
        parent_ids: list[int] | list[str],
    ) -> bool:
        """Return True iff a matching merge job has been recorded."""
        sig = (parent_type, child_type, self._merge_signature(parent_ids))
        with self._lock:
            return sig in self._merge_jobs

    def close(self) -> None:  # no-op
        """No-op for the in-memory store; present for port parity."""
        return None
