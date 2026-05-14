"""Domain core for the data_mgmt package.
Pure orchestration over the ports defined in ``data_mgmt.ports``. No I/O
happens directly here; everything is delegated to injected adapters. This
module is fully testable with the in-memory adapters in
``data_mgmt.adapters.memory``.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Iterable

from upath import UPath

from .model import (
    AssetKind,
    CampaignLayout,
    NetworkLayout,
    DirectoryTree,
    StationLayout,
    GARPOSLayout,
    SurveyLayout,
    TileDBLayout,
)
from .ports import AssetCatalogPort, FileStorePort


# ---------------------------------------------------------------------------
# File type detection (pure)
# ---------------------------------------------------------------------------


DEFAULT_PATTERNS: tuple[tuple[re.Pattern[str], AssetKind], ...] = (
    (re.compile(r"\.\d{2}o$", re.IGNORECASE), AssetKind.RINEX2),
    (re.compile(r"\.\d{2}n$", re.IGNORECASE), AssetKind.RINEX3),
    (re.compile(r"sonardyne", re.IGNORECASE), AssetKind.SONARDYNE),
    (re.compile(r"NOV000"), AssetKind.NOVATEL000),
    (re.compile(r"NOV770"), AssetKind.NOVATEL770),
    (re.compile(r"DFOP00\.raw"), AssetKind.DFOP00),
    (re.compile(r"novatelpin", re.IGNORECASE), AssetKind.NOVATELPIN),
    (re.compile(r"novatel", re.IGNORECASE), AssetKind.NOVATEL),
    (re.compile(r"\.pin$"), AssetKind.QCPIN),
    (re.compile(r"kin", re.IGNORECASE), AssetKind.KIN),
    (re.compile(r"lever_arms", re.IGNORECASE), AssetKind.LEVERARM),
    (re.compile(r"master", re.IGNORECASE), AssetKind.MASTER),
    (re.compile(r"CTD"), AssetKind.CTD),
    (re.compile(r"svpavg", re.IGNORECASE), AssetKind.SEABIRD),
    (re.compile(r"seabird", re.IGNORECASE), AssetKind.SEABIRD),
    (re.compile(r"\.res$"), AssetKind.KINRESIDUALS),
    (re.compile(r"bcoffload", re.IGNORECASE), AssetKind.BCOFFLOAD),
    (re.compile(r"\.sta$", re.IGNORECASE), AssetKind.QCSTA),
)


# ---------------------------------------------------------------------------
# FileTypeDetector — pure file-name → AssetKind classifier
# ---------------------------------------------------------------------------


class FileTypeDetector:
    """Classifies filenames to :class:`AssetKind` using regex patterns.

    Wraps :data:`DEFAULT_PATTERNS` by default; inject a custom list to
    override.
    """

    def __init__(
        self,
        patterns: Iterable[tuple[re.Pattern[str], AssetKind]] | None = None,
    ) -> None:
        self._patterns = tuple(patterns if patterns is not None else DEFAULT_PATTERNS)

    def detect(self, filename: str) -> AssetKind | None:
        """Return the first matching :class:`AssetKind`, or ``None``."""
        for pattern, kind in self._patterns:
            if pattern.search(filename):
                return kind
        return None


# ---------------------------------------------------------------------------
# FileManager — materializes a DirectoryTree on a FileStore
# ---------------------------------------------------------------------------


class FileManager:
    """Creates and validates workspace directories on a :class:`FileStore`.

    Pass *remote_tree* and *remote_backend* to enable remote sync via
    :class:`~earthscope_sfg_workflows.services.sync_service.SyncService`.
    Both must be provided together; omitting either disables remote operations.
    """

    def __init__(
        self,
        directory_tree: DirectoryTree,
        file_backend: FileStorePort,
        remote_tree: DirectoryTree | None = None,
        remote_backend: FileStorePort | None = None,
    ) -> None:
        self.directory_tree = directory_tree
        self.file_backend = file_backend
        self._remote_tree = remote_tree
        self._remote_backend = remote_backend

    @property
    def has_remote(self) -> bool:
        """True when both *remote_tree* and *remote_backend* are configured."""
        return self._remote_tree is not None and self._remote_backend is not None

    @property
    def remote_backend(self) -> "FileStorePort | None":
        """The remote :class:`FileStorePort`, or ``None`` when not configured."""
        return self._remote_backend

    def remote_path_for(self, local_path: UPath) -> UPath | None:
        """Return the theoretical remote path for *local_path*, or ``None``.

        Maps ``local_tree.root / <relative>`` → ``remote_tree.root / <relative>``.
        Returns ``None`` when no remote is configured or *local_path* is not
        under the local tree root.
        """
        if not self.has_remote:
            return None
        try:
            relative = UPath(local_path).relative_to(self.directory_tree.root)
        except ValueError:
            return None
        return UPath(self._remote_tree.root) / relative

    def ensure_workspace(self) -> None:
        """Create the workspace root and Pride directory."""
        self.file_backend.mkdir(self.directory_tree.root)
        self.file_backend.mkdir(self.directory_tree.pride_dir)

    def ensure_network(self, network: str) -> NetworkLayout:
        network_layout: NetworkLayout = self.directory_tree.network(network)
        for path in network_layout.standard_dirs:
            self.file_backend.mkdir(path)
        return network_layout
    
    def ensure_station(self,*,network:str,station:str) -> StationLayout:
        """Materialize the station and TileDB array directories; return the layout."""
        self.file_backend.mkdir(self.directory_tree.station_dir(network=network, station=station))
        station_layout:StationLayout = self.directory_tree.station(network=network, station=station)
        for path in station_layout.standard_dirs:
            self.file_backend.mkdir(path)
        return station_layout

    def ensure_campaign(
        self,
        scope: "SFGScope | None" = None,
        *,
        network: str | None = None,
        station: str | None = None,
        campaign: str | None = None,
    ) -> CampaignLayout:
        """Materialize the campaign directory tree (top-down); return the layout."""
        net = scope.network if scope is not None else network
        sta = scope.station if scope is not None else station
        camp = scope.campaign if scope is not None else campaign
        self.file_backend.mkdir(self.directory_tree.network_dir(network=net))
        self.file_backend.mkdir(self.directory_tree.station_dir(network=net, station=sta))
        layout = self.directory_tree.campaign(network=net, station=sta, campaign=camp)
        for path in layout.standard_dirs:
            self.file_backend.mkdir(path)
        return layout

    def ensure_survey(
        self,
        scope: "SFGScope | None" = None,
        *,
        network: str | None = None,
        station: str | None = None,
        campaign: str | None = None,
        survey: str | None = None,
    ) -> SurveyLayout:
        """Materialize the survey directory; return the layout."""
        net = scope.network if scope is not None else network
        sta = scope.station if scope is not None else station
        camp = scope.campaign if scope is not None else campaign
        surv = scope.survey if scope is not None else survey
        survey_layout: SurveyLayout = self.directory_tree.survey(
            network=net, station=sta, campaign=camp, survey=surv
        )
        for path in survey_layout.standard_dirs:
            self.file_backend.mkdir(path)
        return self.directory_tree.survey(network=net, station=sta, campaign=camp, survey=surv)

    def ensure_garpos_survey(
        self,
        scope: "SFGScope | None" = None,
        *,
        network: str | None = None,
        station: str | None = None,
        campaign: str | None = None,
        survey: str | None = None,
    ) -> GARPOSLayout:
        """Materialize the GARPOS survey directory tree; requires ``scope.survey``."""
        net = scope.network if scope is not None else network
        sta = scope.station if scope is not None else station
        camp = scope.campaign if scope is not None else campaign
        surv = scope.survey if scope is not None else survey
        if surv is None:
            raise ValueError("survey is required for ensure_garpos_survey")
        layout = self.directory_tree.garpos(
            network=net, station=sta, campaign=camp, survey=surv
        )
        for path in layout.standard_dirs:
            self.file_backend.mkdir(path)
        return layout


# ---------------------------------------------------------------------------
# LayoutInspector — FileStore-backed I/O queries over pure layouts
# ---------------------------------------------------------------------------


class LayoutInspector:
    """File-store-backed introspection of pure :mod:`data_mgmt.model` layouts."""

    def __init__(self, files: FileStorePort) -> None:
        self._files = files

    def is_garpos_directory(self, layout: GARPOSLayout) -> bool:
        return self._files.is_file(layout.obs_file) and self._files.is_file(layout.settings_file)

    def find_rectified_shotdata(self, layout: GARPOSLayout) -> Path | None:
        return self._first_match(layout.root, "_rectified.csv")

    def find_filtered_shotdata(self, survey_dir: Path) -> Path | None:
        return self._first_match(survey_dir, "_filtered.csv")

    def is_campaign_directory(self, layout: CampaignLayout) -> bool:
        if not self._files.is_dir(layout.root):
            return False
        return all(self._files.is_dir(p) for p in (layout.raw, layout.processed))

    def list_kind(
        self,
        directory: Path,
        suffix: str | None = None,
        contains: str | None = None,
    ) -> list[Path]:
        if not self._files.is_dir(directory):
            return []
        out: list[Path] = []
        for info in self._files.list_files(directory, recursive=False):
            if not info.is_file:
                continue
            name = info.path.name
            if suffix is not None and not name.endswith(suffix):
                continue
            if contains is not None and contains.lower() not in name.lower():
                continue
            out.append(info.path)
        out.sort(key=lambda p: p.name)
        return out

    def _first_match(
        self,
        directory: Path,
        suffix: str,
        contains: str | None = None,
    ) -> Path | None:
        matches = self.list_kind(directory, suffix=suffix, contains=contains)
        return matches[0] if matches else None


__all__ = [
    "DEFAULT_PATTERNS",
    "FileTypeDetector",
    "FileManager",
    "LayoutInspector",
]
