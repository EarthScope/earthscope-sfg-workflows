"""EarthScope archive :class:`ArchiveSource` adapter.

Wraps the token retrieval, directory listing (via ``?list&uris=1``), and
authenticated download logic behind the :class:`~ports.ArchiveSourcePort`.
Network and auth failures are translated to :class:`~ports.ArchiveError`
subclasses so callers can handle them uniformly.
"""

from __future__ import annotations

import urllib.error
import urllib.request
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

import requests
from earthscope_sfg_tools.datamodels import Site, Vessel

from earthscope_sfg_workflows.logging import ProcessLogger as logger

from ..model import ArchiveFile, SFGScope
from ..ports import ArchiveAuthError, ArchiveError, ArchiveNotFoundError

ARCHIVE_PREFIX = "https://data.earthscope.org/archive/seafloor"


def _campaign_year(campaign: str) -> str:
    """Extract the four-digit year prefix from a campaign identifier.

    Parameters
    ----------
    campaign : str
        Campaign identifier in the form ``YYYY_<suffix>``, e.g.
        ``"2026_A_FOO"``.

    Returns
    -------
    str
        The ``YYYY`` year portion of *campaign*.
    """
    return campaign.split("_", 1)[0]


def canonical_campaign_urls(scope: SFGScope) -> tuple[str, str, str, str]:
    """Return ``(raw_url, metadata_url, rinex_1hz_url, rinex_10hz_url)`` for a campaign."""
    year = _campaign_year(scope.campaign)
    base = f"{ARCHIVE_PREFIX}/{scope.network}/{year}/{scope.station}/{scope.campaign}"
    return (
        f"{base}/raw",
        f"{base}/metadata",
        f"{base}/rinex_1Hz",
        f"{base}/rinex_10Hz",
    )


def campaign_qc_zip_url(scope: SFGScope) -> str:
    """Return the archive URL for a campaign's ``qc.zip`` bundle."""
    year = _campaign_year(scope.campaign)
    base = f"{ARCHIVE_PREFIX}/{scope.network}/{year}/{scope.station}/{scope.campaign}"
    return f"{base}/qc.zip"


def campaign_qc_dir_url(scope: SFGScope) -> str:
    """Return the archive URL for a campaign's ``qc`` directory (sibling to ``qc.zip``).

    Some campaigns publish individual ``.tar.gz`` QC bundles in this
    directory instead of (or in addition to) a single ``qc.zip``.
    """
    year = _campaign_year(scope.campaign)
    base = f"{ARCHIVE_PREFIX}/{scope.network}/{year}/{scope.station}/{scope.campaign}"
    return f"{base}/qc"


def list_campaign_archive_urls(archive: object, scope: SFGScope) -> list[str]:
    """List every file URL for a campaign without writing to any catalog.

    Lists raw, metadata, metadata/ctd, rinex_1Hz, and rinex_10Hz; skips
    missing directories silently.
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
        except ArchiveNotFoundError:
            continue
    return urls


class EarthScopeArchive:
    """Production :class:`~ports.ArchiveSourcePort` backed by the EarthScope public archive.

    Tokens are acquired lazily on first use and refreshed via the EarthScope
    SDK. A *profile* (e.g. ``"dev"``) selects a non-production settings
    profile; omit to use the SDK's active default.

    Attributes
    ----------
    ARCHIVE_PREFIX : str
        Base URL for the EarthScope seafloor archive
        (``"https://data.earthscope.org/archive/seafloor"``).

    Methods
    -------
    authenticate(profile)
        Acquire or refresh an access token from the EarthScope SDK.
    list_files(directory_url)
        List files at *directory_url* via the ``?list&uris=1`` endpoint.
    download_file(file_url, dest_path)
        Download *file_url* to *dest_path*.
    download_to_dir(file_url, dest_dir)
        Download *file_url* into *dest_dir* using the URL's basename.
    canonical_campaign_urls(scope)
        Return the four canonical archive directory URLs for a campaign.
    list_campaign_archive_urls(scope)
        Enumerate every archive file URL for a campaign.
    campaign_raw_url(scope)
        Return the raw-data directory URL for a campaign.
    campaign_metadata_url(scope)
        Return the metadata directory URL for a campaign.
    campaign_rinex_url(scope, hz)
        Compose a RINEX directory URL for a given sample rate.
    campaign_qc_zip_url(scope)
        Return the archive URL for a campaign's qc.zip bundle.
    campaign_qc_dir_url(scope)
        Return the archive URL for a campaign's qc directory (sibling to qc.zip).
    site_metadata_url(scope)
        Return the archive URL for a station's site metadata JSON.
    vessel_json_url(vessel_code)
        Return the archive URL for a vessel metadata JSON file.
    load_vessel_metadata(vessel_code)
        Load a :class:`Vessel` from the archive.
    load_site_metadata(scope, network, station)
        Load a :class:`Site` from the archive, populating per-campaign vessels.
    close()
        Discard the cached access token.
    """

    ARCHIVE_PREFIX = "https://data.earthscope.org/archive/seafloor"

    def __init__(self, profile: str = "default") -> None:
        """Bind to an EarthScope SDK credential profile.

        Parameters
        ----------
        profile : str, optional
            EarthScope SDK settings profile name, by default ``"default"``.
        """
        self._profile = profile
        self._token: str | None = None

    # -- auth --------------------------------------------------------------

    def authenticate(self, profile: str = "default") -> bool:
        """Acquire or refresh an access token from the EarthScope SDK.

        Parameters
        ----------
        profile : str, optional
            EarthScope SDK settings profile to use for authentication, by
            default ``"default"``.

        Returns
        -------
        bool
            ``True`` if a valid token was obtained.

        Raises
        ------
        ArchiveAuthError
            If the SDK login flow fails or returns an empty token.
        """
        # Imported lazily so the data_mgmt package stays importable in
        # environments that don't have earthscope-sdk installed.
        from earthscope_cli.login import login as es_login
        from earthscope_sdk import EarthScopeClient
        from earthscope_sdk.auth.error import AuthFlowError
        from earthscope_sdk.config.settings import SdkSettings

        prof = profile if profile is not None else self._profile
        settings = SdkSettings(profile_name=prof) if prof else SdkSettings()
        client = EarthScopeClient(settings=settings)

        try:
            client.ctx.auth_flow.refresh_if_necessary()
        except AuthFlowError as exc:
            logger.debug(f"Token refresh failed, falling back to login: {exc}")
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
        """List files at *directory_url* via the archive's ``?list&uris=1`` endpoint.

        Parameters
        ----------
        directory_url : str
            URL of the archive directory to enumerate.

        Returns
        -------
        list of ArchiveFile
            One :class:`~model.ArchiveFile` per file found at *directory_url*.

        Raises
        ------
        ArchiveAuthError
            If the server returns HTTP 401.
        ArchiveNotFoundError
            If the server returns HTTP 404.
        ArchiveError
            For any other HTTP error or network failure.
        """
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
        # The listing endpoint's `uris=1` output points at the raw backend
        # (an execute-api host), which only serves directory listings, not
        # individual file downloads. Rewrite each URL's scheme/host to match
        # the public host we listed from so the returned URIs are actually
        # downloadable.
        public = urlsplit(directory_url)
        rewritten = [urlunsplit((public.scheme, public.netloc) + urlsplit(u)[2:]) for u in urls]
        return [ArchiveFile(url=u) for u in rewritten]

    # -- download ----------------------------------------------------------

    def download_file(self, file_url: str, dest_path: Path, *, max_attempts: int = 3) -> None:
        """Download *file_url* to *dest_path*, creating parent directories as needed.

        Streams to a ``.part`` temp file alongside *dest_path* and only
        renames it into place once the transfer completes and (when the
        server reports a ``Content-Length``) the byte count matches. This
        keeps a truncated/dropped connection from ever leaving a corrupt
        file at *dest_path*, where callers would otherwise mistake its mere
        existence for a completed download and never retry it. Transient
        network errors during the streaming read are retried up to
        *max_attempts* times before giving up.

        Parameters
        ----------
        file_url : str
            URL of the file to download.
        dest_path : Path
            Local destination path for the downloaded file.
        max_attempts : int, optional
            Number of attempts before giving up on a transient network
            error. Default is ``3``.

        Raises
        ------
        ArchiveAuthError
            If the server returns HTTP 401.
        ArchiveNotFoundError
            If the server returns HTTP 404.
        ArchiveError
            For any other HTTP error, or a network failure that persists
            across *max_attempts* retries.
        """
        token = self._ensure_token()
        dest_path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = dest_path.with_name(dest_path.name + ".part")

        last_exc: Exception | None = None
        for attempt in range(1, max_attempts + 1):
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

            expected_length = response.headers.get("content-length")
            try:
                written = 0
                with open(tmp_path, "wb") as f:
                    for chunk in response.iter_content(chunk_size=8192):
                        if chunk:
                            f.write(chunk)
                            written += len(chunk)
                if expected_length is not None and written != int(expected_length):
                    raise ArchiveError(
                        f"Truncated download of {file_url}: got {written} bytes, "
                        f"expected {expected_length}"
                    )
            except (requests.RequestException, ArchiveError) as exc:
                last_exc = exc
                tmp_path.unlink(missing_ok=True)
                if attempt < max_attempts:
                    continue
                raise ArchiveError(
                    f"Failed to download {file_url} after {max_attempts} attempts: {exc}"
                ) from exc

            tmp_path.replace(dest_path)
            return

        # Unreachable, but keeps type-checkers happy.
        raise ArchiveError(f"Failed to download {file_url}: {last_exc}")

    def download_to_dir(self, file_url: str, dest_dir: Path) -> Path:
        """Download *file_url* into *dest_dir* using the URL's basename.

        Parameters
        ----------
        file_url : str
            URL of the file to download.
        dest_dir : Path
            Local directory to place the downloaded file in. Created if it
            does not already exist.

        Returns
        -------
        Path
            Absolute path of the downloaded file inside *dest_dir*.
        """
        dest_dir = Path(dest_dir)
        dest_dir.mkdir(parents=True, exist_ok=True)
        dest_path = dest_dir / Path(file_url).name
        self.download_file(file_url, dest_path)
        return dest_path

    def canonical_campaign_urls(self, scope: SFGScope) -> tuple[str, str, str, str]:
        """Return the four canonical archive directory URLs for a campaign.

        Parameters
        ----------
        scope : SFGScope
            Network, station, and campaign identifiers for the target campaign.

        Returns
        -------
        tuple of str
            A four-element tuple ``(raw_url, metadata_url, rinex_1hz_url,
            rinex_10hz_url)`` in the order consumed by
            ``Ingestor.discover_campaign``.
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

        Lists each directory from :meth:`canonical_campaign_urls`, plus the
        legacy ``metadata/ctd`` subdirectory, and returns the concatenated
        list of file URLs. Missing directories are silently skipped — partial
        campaigns (e.g. RINEX 10Hz absent) are common.

        This is the side-effect-free equivalent of
        ``Ingestor.discover_campaign``: it returns URLs without writing
        anything to the catalog.

        Parameters
        ----------
        scope : SFGScope
            Network, station, and campaign identifiers for the target campaign.

        Returns
        -------
        list of str
            All file URLs found across the campaign's archive directories.
        """
        urls: list[str] = []
        raw_url, metadata_url, rinex_1hz_url, rinex_10hz_url = self.canonical_campaign_urls(scope)
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

    def campaign_raw_url(self, scope: SFGScope) -> str:
        """Return the archive URL for the raw data directory of a campaign.

        Parameters
        ----------
        scope : SFGScope
            Network, station, and campaign identifiers.

        Returns
        -------
        str
            URL of the campaign's ``raw`` directory on the archive.
        """
        year = _campaign_year(scope.campaign)
        return f"{self.ARCHIVE_PREFIX}/{scope.network}/{year}/{scope.station}/{scope.campaign}/raw"

    def campaign_metadata_url(self, scope: SFGScope) -> str:
        """Return the archive URL for the metadata directory of a campaign.

        Parameters
        ----------
        scope : SFGScope
            Network, station, and campaign identifiers.

        Returns
        -------
        str
            URL of the campaign's ``metadata`` directory on the archive.
        """
        year = _campaign_year(scope.campaign)
        return f"{self.ARCHIVE_PREFIX}/{scope.network}/{year}/{scope.station}/{scope.campaign}/metadata"

    def campaign_rinex_url(self, scope: SFGScope, hz: str) -> str:
        """Compose a RINEX directory URL for the given sample rate.

        Parameters
        ----------
        scope : SFGScope
            Network, station, and campaign identifiers.
        hz : str
            Sample-rate label; either ``"1Hz"`` or ``"10Hz"``.

        Returns
        -------
        str
            URL of the campaign's ``rinex_<hz>`` directory on the archive.
        """
        year = _campaign_year(scope.campaign)
        return f"{self.ARCHIVE_PREFIX}/{scope.network}/{year}/{scope.station}/{scope.campaign}/rinex_{hz}"

    def campaign_qc_zip_url(self, scope: SFGScope) -> str:
        """Return the archive URL for a campaign's ``qc.zip`` bundle.

        Parameters
        ----------
        scope : SFGScope
            Network, station, and campaign identifiers.

        Returns
        -------
        str
            URL of the campaign's ``qc.zip`` file on the archive.
        """
        year = _campaign_year(scope.campaign)
        return (
            f"{self.ARCHIVE_PREFIX}/{scope.network}/{year}/{scope.station}/{scope.campaign}/qc.zip"
        )

    def campaign_qc_dir_url(self, scope: SFGScope) -> str:
        """Return the archive URL for a campaign's ``qc`` directory (sibling to ``qc.zip``).

        Parameters
        ----------
        scope : SFGScope
            Network, station, and campaign identifiers.

        Returns
        -------
        str
            URL of the campaign's ``qc`` directory on the archive.
        """
        year = _campaign_year(scope.campaign)
        return f"{self.ARCHIVE_PREFIX}/{scope.network}/{year}/{scope.station}/{scope.campaign}/qc"

    def site_metadata_url(self, scope: SFGScope) -> str:
        """Return the archive URL for a station's site metadata JSON.

        Parameters
        ----------
        scope : SFGScope
            Network and station identifiers (campaign is not used).

        Returns
        -------
        str
            URL of the station's ``<station>.json`` metadata file.
        """
        return f"{self.ARCHIVE_PREFIX}/metadata/{scope.network}/{scope.station}.json"

    # -- metadata ----------------------------------------------------------

    def vessel_json_url(self, vessel_code: str) -> str:
        """Return the archive URL for a vessel metadata JSON file.

        Parameters
        ----------
        vessel_code : str
            Short vessel identifier (e.g. ``"R_ENDEAVOR"``).

        Returns
        -------
        str
            URL of the vessel's ``<vessel_code>.json`` metadata file.
        """
        return f"{self.ARCHIVE_PREFIX}/metadata/vessels/{vessel_code}.json"

    def load_vessel_metadata(self, vessel_code: str) -> Vessel:
        """Load a :class:`Vessel` from the archive.

        Downloads the vessel JSON to the current directory, parses it, then
        deletes the temporary file.

        Parameters
        ----------
        vessel_code : str
            Short vessel identifier (e.g. ``"R_ENDEAVOR"``).

        Returns
        -------
        Vessel
            Parsed vessel metadata object.
        """

        url = self.vessel_json_url(vessel_code)
        local = self.download_to_dir(url, Path("./"))
        try:
            vessel = Vessel.from_json(local)
        finally:
            try:
                local.unlink()
            except OSError as exc:
                logger.debug(f"Failed to remove temporary file {local}: {exc}")
        return vessel

    def load_site_metadata(
        self, scope: SFGScope = None, *, network: str | None = None, station: str | None = None
    ) -> Site:
        """Load a :class:`Site` from the archive, populating per-campaign vessels.

        Downloads the station's site JSON, parses it, then attempts to attach
        a :class:`Vessel` object to each campaign. Vessel fetch failures are
        silently ignored (``campaign.vessel`` is set to ``None``).

        Parameters
        ----------
        scope : SFGScope or None, optional
            Network and station identifiers. Either *scope* or both *network*
            and *station* must be provided.
        network : str or None, optional
            Network code, used when *scope* is not provided.
        station : str or None, optional
            Station code, used when *scope* is not provided.

        Returns
        -------
        Site
            Parsed site metadata with vessel objects attached where available.

        Raises
        ------
        ValueError
            If neither *scope* nor both *network* and *station* are provided.
        """

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
            except OSError as exc:
                logger.debug(f"Failed to remove temporary file {local}: {exc}")

        for campaign in site.campaigns:
            try:
                campaign.vessel = self.load_vessel_metadata(campaign.vesselCode)
            except (ArchiveError, FileNotFoundError, ValueError):
                campaign.vessel = None
        return site

    def close(self) -> None:
        """Discard the cached access token so subsequent requests re-authenticate."""
        self._token = None


__all__ = [
    "ARCHIVE_PREFIX",
    "EarthScopeArchive",
    "campaign_qc_dir_url",
    "campaign_qc_zip_url",
    "canonical_campaign_urls",
    "list_campaign_archive_urls",
]
