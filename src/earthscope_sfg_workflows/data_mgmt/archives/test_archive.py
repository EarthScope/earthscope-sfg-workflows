class FakeArchive:
    """In-memory :class:`ArchiveSource`. Seed with ``url -> bytes`` mappings.
    Directory listings are computed by URL prefix (treating any URL whose
    parent prefix matches ``directory_url`` as a child).
    """

    def __init__(self, files: dict[str, bytes] | None = None) -> None:
        """Seed the archive with an optional `url -> bytes` mapping."""
        self._files: dict[str, bytes] = dict(files or {})
        self._authenticated = False

    def seed(self, url: str, data: bytes) -> None:
        """Add or overwrite a single archive file."""
        self._files[url] = data

    def list_files(self, directory_url: str) -> list[ArchiveFile]:
        """List direct children of `directory_url` (no recursion)."""
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
        """Copy seeded bytes for `file_url` to `dest_path`.

        Raises:
            ArchiveNotFoundError: If `file_url` was not seeded.
        """
        if file_url not in self._files:
            raise ArchiveNotFoundError(file_url)
        dest_path.parent.mkdir(parents=True, exist_ok=True)
        dest_path.write_bytes(self._files[file_url])

    def load_site_metadata(self, *, network: str, station: str) -> None:
        """No-op stub; raises so callers fall back to disk or skip gracefully."""
        from ..ports import ArchiveNotFoundError

        raise ArchiveNotFoundError(
            f"FakeArchive has no site metadata for {network}/{station}"
        )

    def authenticate(self, profile: str | None = None) -> bool:
        """Mark the archive as authenticated (always succeeds for the fake)."""
        self._authenticated = True
        return True

    def close(self) -> None:
        """No-op for the fake archive; present for port parity."""
        return None
