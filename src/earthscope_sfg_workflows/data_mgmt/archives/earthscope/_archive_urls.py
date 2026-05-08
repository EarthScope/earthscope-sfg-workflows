"""Standalone URL helpers for the EarthScope seafloor-geodesy archive.
These functions are pure (no I/O) or take an explicit ArchiveSource so they
can be used from both the production adapter and in-memory test doubles.
"""

from __future__ import annotations

from ..earthscope_archive import EarthScopeArchive as _EarthScopeArchive
from ...model import CampaignScope
from ...ports import ArchiveError, ArchiveSource

ARCHIVE_PREFIX: str = _EarthScopeArchive.ARCHIVE_PREFIX


def _campaign_year(campaign: str) -> str:
    """Extract the YYYY year prefix from a campaign id like ``2026_A_FOO``."""
    return campaign.split("_", 1)[0]


def canonical_campaign_urls(scope: CampaignScope) -> tuple[str, str, str, str]:
    """The four canonical archive directory URLs for a campaign.
    Returns ``(raw, metadata, rinex_1hz, rinex_10hz)`` in that order.
    Pure: no I/O, no archive needed.
    """
    year = _campaign_year(scope.campaign)
    base = f"{ARCHIVE_PREFIX}/{scope.network}/{year}/{scope.station}/{scope.campaign}"
    return (
        f"{base}/raw",
        f"{base}/metadata",
        f"{base}/rinex_1Hz",
        f"{base}/rinex_10Hz",
    )


def list_campaign_archive_urls(
    archive: ArchiveSource,
    scope: CampaignScope,
) -> list[str]:
    """Enumerate every archive file URL for a campaign without cataloging anything.
    Lists raw / metadata / metadata/ctd / RINEX 1Hz / RINEX 10Hz and returns
    the concatenated file URL list. Missing directories are silently skipped.
    """
    raw_url, metadata_url, rinex_1hz_url, rinex_10hz_url = canonical_campaign_urls(scope)
    urls: list[str] = []
    for dir_url in (
        raw_url,
        metadata_url,
        f"{metadata_url}/ctd",
        rinex_1hz_url,
        rinex_10hz_url,
    ):
        try:
            urls.extend(af.url for af in archive.list_files(dir_url))
        except (ArchiveError, Exception):
            continue
    return urls
