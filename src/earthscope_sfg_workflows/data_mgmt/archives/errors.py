# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------


class ArchiveError(Exception):
    """Base for all archive-related errors."""


class ArchiveAuthError(ArchiveError):
    """Authentication or authorization failure against the archive."""


class ArchiveNotFoundError(ArchiveError):
    """The requested archive resource does not exist."""
