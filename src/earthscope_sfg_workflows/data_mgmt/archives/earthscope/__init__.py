"""EarthScope archive sub-package.

Re-exports :class:`EarthScopeArchive` for convenience and exposes the
standalone URL helpers that live in ``_archive_urls``.
"""

from earthscope_sfg_workflows.data_mgmt.archives.earthscope_archive import EarthScopeArchive

__all__ = ["EarthScopeArchive"]
