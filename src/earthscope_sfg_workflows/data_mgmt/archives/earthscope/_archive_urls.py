"""Standalone EarthScope archive URL helpers.

These are pure functions — no network, no auth, no ``self``.  They extract
the URL construction logic from :class:`EarthScopeArchive` so that the
``Ingestor`` and tests can call them without instantiating the full archive
adapter.
"""

from __future__ import annotations

from earthscope_sfg_workflows.data_mgmt.model import SFGScope
from earthscope_sfg_workflows.data_mgmt.ports import ArchiveError, ArchiveSourcePort

ARCHIVE_PREFIX = "https://data.earthscope.org/archive/seafloor"


def _campaign_year(campaign: str) -> str:
    """Extract the ``YYYY`` year prefix from a campaign id like ``2026_A_FOO``."""
    return campaign.split("_", 1)[0]


def campaign_raw_url(scope: SFGScope) -> str:
    """Return the archive URL for the raw data directory of ``scope``."""
    year = _campaign_year(scope.campaign)
    return f"{ARCHIVE_PREFIX}/{scope.network}/{year}/{scope.station}/{scope.campaign}/raw"


def campaign_metadata_url(scope: SFGScope) -> str:
    """Return the archive URL for the metadata directory of ``scope``."""
    year = _campaign_year(scope.campaign)
    return f"{ARCHIVE_PREFIX}/{scope.network}/{year}/{scope.station}/{scope.campaign}/metadata"


def campaign_rinex_url(scope: SFGScope, hz: str) -> str:
    """Compose a RINEX directory URL.  ``hz`` is ``"1Hz"`` or ``"10Hz"``."""
    year = _campaign_year(scope.campaign)
    return f"{ARCHIVE_PREFIX}/{scope.network}/{year}/{scope.station}/{scope.campaign}/rinex_{hz}"


def canonical_campaign_urls(scope: SFGScope) -> tuple[str, str, str, str]:
    """Return the four canonical archive directory URLs for ``scope``.

    Order: raw, metadata, RINEX 1Hz, RINEX 10Hz.
    """
    return (
        campaign_raw_url(scope),
        campaign_metadata_url(scope),
        campaign_rinex_url(scope, "1Hz"),
        campaign_rinex_url(scope, "10Hz"),
    )


def list_campaign_archive_urls(
    archive: ArchiveSourcePort,
    scope: SFGScope,
) -> list[str]:
    """Enumerate every archive file URL for ``scope`` without touching the catalog.

    Lists ``raw``, ``metadata``, ``metadata/ctd``, ``rinex_1Hz``, and
    ``rinex_10Hz`` directories and concatenates all file URLs.  Missing
    directories are silently skipped (partial campaigns are common).
    """
    raw_url, metadata_url, rinex_1hz_url, rinex_10hz_url = canonical_campaign_urls(scope)
    dirs = (
        raw_url,
        metadata_url,
        f"{metadata_url}/ctd",
        rinex_1hz_url,
        rinex_10hz_url,
    )
    urls: list[str] = []
    for dir_url in dirs:
        try:
            urls.extend(af.url for af in archive.list_files(dir_url))
        except ArchiveError:
            continue
    return urls


__all__ = [
    "ARCHIVE_PREFIX",
    "campaign_raw_url",
    "campaign_metadata_url",
    "campaign_rinex_url",
    "canonical_campaign_urls",
    "list_campaign_archive_urls",
]
