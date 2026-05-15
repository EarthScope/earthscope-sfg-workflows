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

    Methods
    -------
    detect(filename)
        Return the first matching ``AssetKind``, or ``None``.
    """

    def __init__(
        self,
        patterns: Iterable[tuple[re.Pattern[str], AssetKind]] | None = None,
    ) -> None:
        """Initialize the detector with the given pattern list.

        Parameters
        ----------
        patterns : Iterable[tuple[re.Pattern[str], AssetKind]] or None, optional
            Ordered sequence of ``(compiled_pattern, AssetKind)`` pairs.
            Defaults to :data:`DEFAULT_PATTERNS` when ``None``.
        """
        self._patterns = tuple(patterns if patterns is not None else DEFAULT_PATTERNS)

    def detect(self, filename: str) -> AssetKind | None:
        """Return the first matching :class:`AssetKind`, or ``None``.

        Parameters
        ----------
        filename : str
            The filename or path string to classify.

        Returns
        -------
        AssetKind or None
            The first ``AssetKind`` whose pattern matches *filename*,
            or ``None`` if no pattern matches.
        """
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

    Attributes
    ----------
    directory_tree : DirectoryTree
        The local directory layout used to derive all workspace paths.
    file_backend : FileStorePort
        The file-store implementation that performs disk or S3 I/O.
    has_remote : bool
        ``True`` when both *remote_tree* and *remote_backend* are configured.
    remote_backend : FileStorePort or None
        The remote :class:`FileStorePort`, or ``None`` when not configured.

    Methods
    -------
    remote_path_for(local_path)
        Return the theoretical remote path for *local_path*, or ``None``.
    ensure_workspace()
        Create the workspace root and Pride directory.
    ensure_network(network)
        Materialize the network directory and return the layout.
    ensure_station(*, network, station)
        Materialize the station directory and return the layout.
    ensure_campaign(scope, *, network, station, campaign)
        Materialize the campaign directory tree and return the layout.
    ensure_survey(scope, *, network, station, campaign, survey)
        Materialize the survey directory and return the layout.
    ensure_garpos_survey(scope, *, network, station, campaign, survey)
        Materialize the GARPOS survey directory tree and return the layout.
    """

    def __init__(
        self,
        directory_tree: DirectoryTree,
        file_backend: FileStorePort,
        remote_tree: DirectoryTree | None = None,
        remote_backend: FileStorePort | None = None,
    ) -> None:
        """Initialize the manager with local and optional remote backends.

        Parameters
        ----------
        directory_tree : DirectoryTree
            The local directory layout used to derive all workspace paths.
        file_backend : FileStorePort
            The file-store implementation that performs disk or S3 I/O.
        remote_tree : DirectoryTree or None, optional
            Remote directory layout for sync operations. Must be set together
            with *remote_backend* to enable remote operations.
        remote_backend : FileStorePort or None, optional
            Remote file-store implementation. Omit to disable remote sync.
        """
        self.directory_tree = directory_tree
        self.file_backend = file_backend
        self._remote_tree = remote_tree
        self._remote_backend = remote_backend

    @property
    def has_remote(self) -> bool:
        """True when both *remote_tree* and *remote_backend* are configured.

        Returns
        -------
        bool
            ``True`` if both *remote_tree* and *remote_backend* were supplied
            at construction time.
        """
        return self._remote_tree is not None and self._remote_backend is not None

    @property
    def remote_backend(self) -> "FileStorePort | None":
        """The remote :class:`FileStorePort`, or ``None`` when not configured.

        Returns
        -------
        FileStorePort or None
            The remote file-store passed at construction, or ``None``.
        """
        return self._remote_backend

    def remote_path_for(self, local_path: UPath) -> UPath | None:
        """Return the theoretical remote path for *local_path*, or ``None``.

        Maps ``local_tree.root / <relative>`` → ``remote_tree.root / <relative>``.

        Parameters
        ----------
        local_path : UPath
            A path under the local directory tree root to translate.

        Returns
        -------
        UPath or None
            The corresponding remote path, or ``None`` when no remote is
            configured or *local_path* is not under the local tree root.
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
        """Materialize the network directory; return the layout.

        Parameters
        ----------
        network : str
            Network identifier (e.g. ``"NCB1"``).

        Returns
        -------
        NetworkLayout
            The layout object for the newly created network directory.
        """
        network_layout: NetworkLayout = self.directory_tree.network(network)
        for path in network_layout.standard_dirs:
            self.file_backend.mkdir(path)
        return network_layout

    def ensure_station(self, *, network: str, station: str) -> StationLayout:
        """Materialize the station and TileDB array directories; return the layout.

        Parameters
        ----------
        network : str
            Network identifier.
        station : str
            Station identifier.

        Returns
        -------
        StationLayout
            The layout object for the newly created station directory.
        """
        self.file_backend.mkdir(self.directory_tree.station_dir(network=network, station=station))
        station_layout: StationLayout = self.directory_tree.station(
            network=network, station=station
        )
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
        """Materialize the campaign directory tree (top-down); return the layout.

        Parameters
        ----------
        scope : SFGScope or None, optional
            Scope object providing network, station, and campaign. When given,
            the keyword arguments are ignored.
        network : str or None, optional
            Network identifier. Used when *scope* is ``None``.
        station : str or None, optional
            Station identifier. Used when *scope* is ``None``.
        campaign : str or None, optional
            Campaign identifier. Used when *scope* is ``None``.

        Returns
        -------
        CampaignLayout
            The layout object for the newly created campaign directory tree.
        """
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
        """Materialize the survey directory; return the layout.

        Parameters
        ----------
        scope : SFGScope or None, optional
            Scope object providing network, station, campaign, and survey.
            When given, the keyword arguments are ignored.
        network : str or None, optional
            Network identifier. Used when *scope* is ``None``.
        station : str or None, optional
            Station identifier. Used when *scope* is ``None``.
        campaign : str or None, optional
            Campaign identifier. Used when *scope* is ``None``.
        survey : str or None, optional
            Survey identifier. Used when *scope* is ``None``.

        Returns
        -------
        SurveyLayout
            The layout object for the newly created survey directory.
        """
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
        """Materialize the GARPOS survey directory tree; requires ``scope.survey``.

        Parameters
        ----------
        scope : SFGScope or None, optional
            Scope object providing network, station, campaign, and survey.
            When given, the keyword arguments are ignored.
        network : str or None, optional
            Network identifier. Used when *scope* is ``None``.
        station : str or None, optional
            Station identifier. Used when *scope* is ``None``.
        campaign : str or None, optional
            Campaign identifier. Used when *scope* is ``None``.
        survey : str or None, optional
            Survey identifier. Used when *scope* is ``None``.

        Returns
        -------
        GARPOSLayout
            The layout object for the newly created GARPOS directory tree.

        Raises
        ------
        ValueError
            If no survey identifier can be resolved from *scope* or *survey*.
        """
        net = scope.network if scope is not None else network
        sta = scope.station if scope is not None else station
        camp = scope.campaign if scope is not None else campaign
        surv = scope.survey if scope is not None else survey
        if surv is None:
            raise ValueError("survey is required for ensure_garpos_survey")
        layout = self.directory_tree.garpos(network=net, station=sta, campaign=camp, survey=surv)
        for path in layout.standard_dirs:
            self.file_backend.mkdir(path)
        return layout


# ---------------------------------------------------------------------------
# LayoutInspector — FileStore-backed I/O queries over pure layouts
# ---------------------------------------------------------------------------


class LayoutInspector:
    """File-store-backed introspection of pure :mod:`data_mgmt.model` layouts.

    Methods
    -------
    is_garpos_directory(layout)
        Return ``True`` when the GARPOS observation and settings files are present.
    find_rectified_shotdata(layout)
        Return the first ``*_rectified.csv`` file under the GARPOS root, or ``None``.
    find_filtered_shotdata(survey_dir)
        Return the first ``*_filtered.csv`` file under *survey_dir*, or ``None``.
    is_campaign_directory(layout)
        Return ``True`` when the campaign root, raw, and processed dirs all exist.
    list_kind(directory, suffix, contains)
        List files in *directory* optionally filtered by *suffix* and/or *contains*.
    """

    def __init__(self, files: FileStorePort) -> None:
        """Initialize the inspector with the given file-store backend.

        Parameters
        ----------
        files : FileStorePort
            The file-store implementation used for existence and listing queries.
        """
        self._files = files

    def is_garpos_directory(self, layout: GARPOSLayout) -> bool:
        """Return ``True`` when the observation and settings files are present.

        Parameters
        ----------
        layout : GARPOSLayout
            The GARPOS directory layout to inspect.

        Returns
        -------
        bool
            ``True`` if both ``layout.obs_file`` and ``layout.settings_file`` exist.
        """
        return self._files.is_file(layout.obs_file) and self._files.is_file(layout.settings_file)

    def find_rectified_shotdata(self, layout: GARPOSLayout) -> Path | None:
        """Return the first ``*_rectified.csv`` file under the GARPOS root, or ``None``.

        Parameters
        ----------
        layout : GARPOSLayout
            The GARPOS directory layout whose root is searched.

        Returns
        -------
        Path or None
            Absolute path to the first matching file, or ``None`` if none found.
        """
        return self._first_match(layout.root, "_rectified.csv")

    def find_filtered_shotdata(self, survey_dir: Path) -> Path | None:
        """Return the first ``*_filtered.csv`` file under *survey_dir*, or ``None``.

        Parameters
        ----------
        survey_dir : Path
            Directory to search for a filtered shot-data CSV.

        Returns
        -------
        Path or None
            Absolute path to the first matching file, or ``None`` if none found.
        """
        return self._first_match(survey_dir, "_filtered.csv")

    def is_campaign_directory(self, layout: CampaignLayout) -> bool:
        """Return ``True`` when the campaign root, raw, and processed dirs all exist.

        Parameters
        ----------
        layout : CampaignLayout
            The campaign directory layout to inspect.

        Returns
        -------
        bool
            ``True`` if the campaign root, raw, and processed directories all exist.
        """
        if not self._files.is_dir(layout.root):
            return False
        return all(self._files.is_dir(p) for p in (layout.raw, layout.processed))

    def list_kind(
        self,
        directory: Path,
        suffix: str | None = None,
        contains: str | None = None,
    ) -> list[Path]:
        """List files in *directory* optionally filtered by *suffix* and/or *contains*.

        Parameters
        ----------
        directory : Path
            Directory to list files from (non-recursive).
        suffix : str or None, optional
            Only include files whose names end with this suffix.
        contains : str or None, optional
            Only include files whose names contain this substring (case-insensitive).

        Returns
        -------
        list[Path]
            Sorted list of matching file paths.
        """
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
