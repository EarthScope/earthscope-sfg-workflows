"""EarthScope archive :class:`ArchiveSource` adapter.
Wraps the existing logic in
``data_mgmt.ingestion.archive_pull`` (token retrieval, directory listing via
``?list&uris=1``, authenticated download) behind the port. Network / auth
failures are translated to :class:`ArchiveError` subclasses so callers can
handle them uniformly.
"""

from __future__ import annotations

import urllib.error
import urllib.request
from pathlib import Path

from earthscope_sfg_tools.datamodels import Site, Vessel
import requests

from ..model import ArchiveFile, SFGScope
from ..ports import ArchiveAuthError, ArchiveError, ArchiveNotFoundError

def _campaign_year(campaign: str) -> str:
    """Extract the YYYY year prefix from a campaign id like ``2026_A_FOO``."""
    return campaign.split("_", 1)[0]


class EarthScopeArchive:
    """Production :class:`ArchiveSource` backed by the EarthScope public archive.
    Tokens are acquired lazily on first use and refreshed via the EarthScope
    SDK. A ``profile`` (e.g. ``"dev"``) selects a non-prod settings profile.
    """
    ARCHIVE_PREFIX = "https://data.earthscope.org/archive/seafloor"

    def __init__(self, profile: str = "prod") -> None:
        """Bind to an EarthScope SDK `profile` (defaults to the active profile)."""
        self._profile = profile
        self._token: str | None = None

    # -- auth --------------------------------------------------------------

    def authenticate(self, profile: str = 'prod') -> bool:
        """Acquire/refresh an access token from the EarthScope SDK; return True on success."""
        # Imported lazily so the data_mgmt package stays importable in
        # environments that don't have earthscope-sdk installed.
        from earthscope_cli.login import login as es_login
        from earthscope_sdk import EarthScopeClient
        from earthscope_sdk.config.settings import SdkSettings

        prof = profile if profile is not None else self._profile
        settings = SdkSettings(profile_name=prof) if prof else SdkSettings()
        client = EarthScopeClient(settings=settings)

        try:
            client.ctx.auth_flow.refresh_if_necessary()
        except Exception:
            try:
                es_login(sdk=client)
            except Exception as exc:
                raise ArchiveAuthError(f"EarthScope login failed: {exc}") from exc

        token = client.ctx.auth_flow.access_token
        if not token:
            raise ArchiveAuthError("EarthScope auth flow returned an empty token")
        self._token = token
        return True

    def _ensure_token(self) -> str:
        if self._token is None:
            self.authenticate()
        assert self._token is not None  # for type-checkers
        return self._token

    # -- listing -----------------------------------------------------------

    def list_files(self, directory_url: str) -> list[ArchiveFile]:
        """List files at `directory_url` via the archive's `?list&uris=1` endpoint."""
        token = self._ensure_token()
        list_url = directory_url.rstrip("/") + "/?list&uris=1"
        req = urllib.request.Request(list_url)
        req.add_header("Authorization", f"Bearer {token}")
        try:
            with urllib.request.urlopen(req) as response:
                body = response.read().decode("utf-8")
        except urllib.error.HTTPError as exc:
            if exc.code == 401:
                raise ArchiveAuthError(f"Unauthorized listing {directory_url}") from exc
            if exc.code == 404:
                raise ArchiveNotFoundError(directory_url) from exc
            raise ArchiveError(f"HTTP {exc.code} listing {directory_url}") from exc
        except urllib.error.URLError as exc:
            raise ArchiveError(f"Failed to list {directory_url}: {exc}") from exc

        urls = [line.strip() for line in body.splitlines() if line.strip()]
        return [ArchiveFile(url=u) for u in urls]

    # -- download ----------------------------------------------------------

    def download_file(self, file_url: str, dest_path: Path) -> None:
        """Download `file_url` to `dest_path`, creating parents as needed."""
        token = self._ensure_token()
        dest_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            response = requests.get(
                file_url,
                headers={"authorization": f"Bearer {token}"},
                stream=True,
                timeout=60,
            )
        except requests.RequestException as exc:
            raise ArchiveError(f"Network error downloading {file_url}: {exc}") from exc

        if response.status_code == 401:
            raise ArchiveAuthError(f"Unauthorized: {file_url}")
        if response.status_code == 404:
            raise ArchiveNotFoundError(file_url)
        if response.status_code != requests.codes.ok:
            raise ArchiveError(
                f"Failed to download {file_url}: HTTP {response.status_code} ({response.reason})"
            )

        with open(dest_path, "wb") as f:
            for chunk in response.iter_content(chunk_size=8192):
                if chunk:
                    f.write(chunk)

    def download_to_dir(self, file_url: str, dest_dir: Path) -> Path:
        """Download ``file_url`` into ``dest_dir`` using the URL's basename.
        Returns the resulting local path. ``dest_dir`` is created if missing.
        """
        dest_dir = Path(dest_dir)
        dest_dir.mkdir(parents=True, exist_ok=True)
        dest_path = dest_dir / Path(file_url).name
        self.download_file(file_url, dest_path)
        return dest_path


    def canonical_campaign_urls(self,scope: SFGScope) -> tuple[str, str, str, str]:
        """The four canonical archive URLs for a campaign.
        Order: raw, metadata, RINEX 1Hz, RINEX 10Hz. ``Ingestor.discover_campaign``
        consumes them in that order.
        """
        return (
            self.campaign_raw_url(scope),
            self.campaign_metadata_url(scope),
            self.campaign_rinex_url(scope, "1Hz"),
            self.campaign_rinex_url(scope, "10Hz"),
        )


    def list_campaign_archive_urls(
        self,
        scope: SFGScope,
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
        raw_url, metadata_url, rinex_1hz_url, rinex_10hz_url = self.canonical_campaign_urls(
            scope
        )
        for dir_url in (
            raw_url,
            metadata_url,
            f"{metadata_url}/ctd",
            rinex_1hz_url,
            rinex_10hz_url,
        ):
            try:
                urls.extend(af.url for af in self.list_files(dir_url))
            except ArchiveError:
                continue
        return urls

    def campaign_raw_url(self,scope: SFGScope) -> str:
        """Return the archive URL for the raw data directory of a campaign."""
        year = _campaign_year(scope.campaign)
        return (
            f"{self.ARCHIVE_PREFIX}/{scope.network}/{year}/{scope.station}/{scope.campaign}/raw"
        )

    def campaign_metadata_url(self,scope: SFGScope) -> str:
        """Return the archive URL for the metadata directory of a campaign."""
        year = _campaign_year(scope.campaign)
        return f"{self.ARCHIVE_PREFIX}/{scope.network}/{year}/{scope.station}/{scope.campaign}/metadata"

    def campaign_rinex_url(self,scope: SFGScope, hz: str) -> str:
        """Compose a RINEX directory URL. ``hz`` is ``"1Hz"`` or ``"10Hz"``."""
        year = _campaign_year(scope.campaign)
        return f"{self.ARCHIVE_PREFIX}/{scope.network}/{year}/{scope.station}/{scope.campaign}/rinex_{hz}"

    def site_metadata_url(self, scope: SFGScope) -> str:
        """Return the archive URL for a station's site metadata JSON."""
        return f"{self.ARCHIVE_PREFIX}/metadata/{scope.network}/{scope.station}.json"

    # -- metadata ----------------------------------------------------------

    def vessel_json_url(self,vessel_code: str) -> str:

        return f"{self.ARCHIVE_PREFIX}/metadata/vessels/{vessel_code}.json"

    def load_vessel_metadata(self, vessel_code: str) -> Vessel:
        """Load a :class:`Vessel` from the archive (or a local JSON file)."""

        url = self.vessel_json_url(vessel_code)
        local = self.download_to_dir(url, Path("./"))
        try:
            vessel = Vessel.from_json(local)
        finally:
            try:
                local.unlink()
            except Exception:
                pass
        return vessel

    def load_site_metadata(self, scope: SFGScope=None,*,network: str=None,station:str=None) -> Site:
        """Load a :class:`Site` from the archive, populating per-campaign vessels."""

        if scope is None:
            if network is None or station is None:
                raise ValueError("Must provide either scope or both network and station")
            scope = SFGScope(network=network, station=station, campaign="")
            
        url = self.site_metadata_url(scope)
        local = self.download_to_dir(url, Path("./"))
        try:
            site = Site.from_json(local)
        finally:
            try:
                local.unlink()
            except Exception:
                pass

        for campaign in site.campaigns:
            try:
                campaign.vessel = self.load_vessel_metadata(campaign.vesselCode)
            except (ArchiveError, FileNotFoundError, ValueError):
                campaign.vessel = None
        return site

    def close(self) -> None:
        """Discard the cached access token; new requests will re-authenticate."""
        self._token = None


__all__ = ["EarthScopeArchive"]
