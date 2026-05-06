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

import requests

from ..model import ArchiveFile
from ..ports import ArchiveAuthError, ArchiveError, ArchiveNotFoundError


class EarthScopeArchive:
    """Production :class:`ArchiveSource` backed by the EarthScope public archive.

    Tokens are acquired lazily on first use and refreshed via the EarthScope
    SDK. A ``profile`` (e.g. ``"dev"``) selects a non-prod settings profile.
    """

    def __init__(self, profile: str | None = None) -> None:
        self._profile = profile
        self._token: str | None = None

    # -- auth --------------------------------------------------------------

    def authenticate(self, profile: str | None = None) -> bool:
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

    # -- metadata ----------------------------------------------------------

    @staticmethod
    def _site_json_url(network: str, station: str) -> str:
        from .._archive_urls import ARCHIVE_PREFIX

        return f"{ARCHIVE_PREFIX}/metadata/{network}/{station}.json"

    @staticmethod
    def _vessel_json_url(vessel_code: str) -> str:
        from .._archive_urls import ARCHIVE_PREFIX

        return f"{ARCHIVE_PREFIX}/metadata/vessels/{vessel_code}.json"

    def load_vessel_metadata(self, vessel_code: str, local_path: Path | str | None = None):
        """Load a :class:`Vessel` from the archive (or a local JSON file)."""
        from earthscope_sfg_tools.datamodels.metadata import import_vessel

        if local_path is not None:
            json_file_path = Path(local_path)
            if not json_file_path.exists():
                raise FileNotFoundError(
                    f"Local vessel metadata file {json_file_path} does not exist."
                )
            return import_vessel(json_file_path)

        url = self._vessel_json_url(vessel_code)
        local = self.download_to_dir(url, Path("./"))
        try:
            return import_vessel(local)
        finally:
            try:
                local.unlink()
            except Exception:
                pass

    def load_site_metadata(self, network: str, station: str, local_path: Path | str | None = None):
        """Load a :class:`Site` from the archive, populating per-campaign vessels."""
        from earthscope_sfg_tools.datamodels.metadata import import_site

        if local_path is not None:
            json_file_path = Path(local_path)
            if not json_file_path.exists():
                raise FileNotFoundError(
                    f"Local site metadata file {json_file_path} does not exist."
                )
            site = import_site(json_file_path)
        else:
            url = self._site_json_url(network, station)
            local = self.download_to_dir(url, Path("./"))
            try:
                site = import_site(local)
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
        self._token = None


__all__ = ["EarthScopeArchive"]
