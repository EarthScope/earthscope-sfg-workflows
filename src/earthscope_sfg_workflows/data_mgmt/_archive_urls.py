"""Archive URL composition for the EarthScope public archive.

Private to ``data_mgmt``. Callers reach this through
:meth:`data_mgmt.core.Ingestor.discover_campaign`, which composes the four
canonical campaign URLs (raw, metadata, RINEX 1Hz, RINEX 10Hz), or through
:func:`list_campaign_archive_urls` for plain URL enumeration.

Mirrors the templates in the legacy ``data_mgmt/ingestion/archive_pull.py``
so URLs remain stable across the migration.
"""

from __future__ import annotations

from .model import CampaignScope
from .ports import ArchiveError, ArchiveSource

ARCHIVE_PREFIX = "https://data.earthscope.org/archive/seafloor"


def _campaign_year(campaign: str) -> str:
    """Extract the YYYY year prefix from a campaign id like ``2026_A_FOO``."""
    return campaign.split("_", 1)[0]


def campaign_raw_url(scope: CampaignScope) -> str:
    year = _campaign_year(scope.campaign)
    return f"{ARCHIVE_PREFIX}/{scope.network}/{year}/{scope.station}/{scope.campaign}/raw"


def campaign_metadata_url(scope: CampaignScope) -> str:
    year = _campaign_year(scope.campaign)
    return f"{ARCHIVE_PREFIX}/{scope.network}/{year}/{scope.station}/{scope.campaign}/metadata"


def campaign_rinex_url(scope: CampaignScope, hz: str) -> str:
    """Compose a RINEX directory URL. ``hz`` is ``"1Hz"`` or ``"10Hz"``."""
    year = _campaign_year(scope.campaign)
    return f"{ARCHIVE_PREFIX}/{scope.network}/{year}/{scope.station}/{scope.campaign}/rinex_{hz}"


def site_metadata_url(network: str, station: str) -> str:
    return f"{ARCHIVE_PREFIX}/metadata/{network}/{station}.json"


def vessel_metadata_url(vessel_code: str) -> str:
    return f"{ARCHIVE_PREFIX}/metadata/vessels/{vessel_code}.json"


def canonical_campaign_urls(scope: CampaignScope) -> tuple[str, str, str, str]:
    """The four canonical archive URLs for a campaign.

    Order: raw, metadata, RINEX 1Hz, RINEX 10Hz. ``Ingestor.discover_campaign``
    consumes them in that order.
    """
    return (
        campaign_raw_url(scope),
        campaign_metadata_url(scope),
        campaign_rinex_url(scope, "1Hz"),
        campaign_rinex_url(scope, "10Hz"),
    )


def list_campaign_archive_urls(
    archive: ArchiveSource,
    scope: CampaignScope,
) -> list[str]:
    """Enumerate every archive file URL for a campaign.

    Lists each of :func:`canonical_campaign_urls`, plus the legacy
    ``metadata/ctd`` subdirectory, and returns the concatenated list of
    file URLs. Missing directories are silently skipped (partial campaigns
    are common — e.g., RINEX 10Hz absent).

    This is the no-side-effects equivalent of
    :meth:`data_mgmt.core.Ingestor.discover_campaign`: it returns URLs
    without writing anything to the catalog.
    """
    urls: list[str] = []
    raw_url, metadata_url, rinex_1hz_url, rinex_10hz_url = canonical_campaign_urls(scope)
    for dir_url in (raw_url, metadata_url, f"{metadata_url}/ctd", rinex_1hz_url, rinex_10hz_url):
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
    "site_metadata_url",
    "vessel_metadata_url",
    "canonical_campaign_urls",
]
