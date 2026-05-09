"""The :class:`Workspace` — single object owning ports + scope + metadata.
All data-layer access is provided by methods directly on this class.

See ``plans/prds/2026-05-05-workflow-base-and-facades.md``.
"""

from __future__ import annotations

import json
import os
import warnings
from contextlib import AbstractContextManager
from dataclasses import dataclass, replace
from pathlib import Path
from typing import TYPE_CHECKING

from earthscope_sfg_workflows.data_mgmt.adapters.test_adapters import InMemoryAssetStore
from earthscope_sfg_workflows.data_mgmt.core import (
    FileManager,
    FileTypeDetector,
    Ingestor,
    LayoutInspector,
)
from earthscope_sfg_workflows.data_mgmt.model import (
    AssetEntry,
    AssetKind,
    CampaignLayout,
    CampaignScope,
    DirectoryTree,
    GARPOSLayout,
    IngestReport,
    TileDBLayout,
)
from earthscope_sfg_workflows.data_mgmt.ports import (
    ArchiveSourcePort,
    AssetCatalogPort,
    FileStorePort,
)
from earthscope_sfg_workflows.data_mgmt.adapters.test_adapters import (
    FakeArchive,
    InMemoryAssetStore,
    InMemoryFileStore,
)

from earthscope_sfg_tools.datamodels.metadata import Site

if TYPE_CHECKING:  # pragma: no cover
    from earthscope_sfg_tools.datamodels.metadata import Campaign, Site, Survey
    from earthscope_sfg_tools.tiledb_integration import (
        TDBAcousticArray,
        TDBGNSSObsArray,
        TDBIMUPositionArray,
        TDBKinPositionArray,
        TDBShotDataArray,
    )


# ---------------------------------------------------------------------------
# Workspace factory helpers
# ---------------------------------------------------------------------------


def _to_asset_kind(t: object) -> AssetKind:
    """Translate a user-facing AssetType enum/str to :class:`AssetKind`."""
    from earthscope_sfg_workflows.config.file_config import AssetType

    if isinstance(t, AssetType):
        return AssetKind(t.value)
    return AssetKind(str(t).lower())


def _build_default_workspace(directory: Path | str) -> "Workspace":
    """Construct a production :class:`Workspace` rooted at ``directory``.
    Picks the file store based on the directory scheme: ``s3://`` URIs use
    :class:`S3FileStore`; everything else uses :class:`LocalFileStore`.
    """
    from earthscope_sfg_workflows.data_mgmt.adapters.disk_filestore import (
        LocalFileStore,
        S3FileStore,
    )
    from earthscope_sfg_workflows.data_mgmt.adapters.earthscope_archive import EarthScopeArchive
    from earthscope_sfg_workflows.data_mgmt.adapters.sql_asset_catalog import AssetCatalog

    is_s3 = str(directory).startswith("s3://")
    if is_s3:
        files = S3FileStore()
        catalog_db = Path(os.environ.get("MAIN_DIRECTORY", ".")) / "catalog.sqlite"
        root: Path | str = directory
    else:
        files = LocalFileStore()
        root = Path(directory)
        root.mkdir(parents=True, exist_ok=True)
        catalog_db = root / "catalog.sqlite"

    catalog = AssetCatalog.sqlite(catalog_db)
    archive = EarthScopeArchive()
    return Workspace(root_dir=root, catalog=catalog, files=files, archive=archive)


@dataclass
class TileDBRegistry:

    acoustic: TDBAcousticArray
    kin_position: TDBKinPositionArray
    imu_position: TDBIMUPositionArray
    shotdata: TDBShotDataArray
    shotdata_pre: TDBShotDataArray
    gnss_obs: TDBGNSSObsArray
    gnss_obs_secondary: TDBGNSSObsArray


class Workspace(AbstractContextManager["Workspace"]):
    """Active-scope object bundling ports, scope state, and metadata cache.
    Holds the three injected ports (catalog/files/archive), the pure
    :class:`DirectoryTree`, and the current network/station/campaign/survey
    selection. Exposes data-layer access only through four façades.

    Scope mutators cascade resets down the hierarchy: changing station
    clears campaign+survey+all metadata except the site (and the site is
    cleared too when the *new* station id differs from the *previous*
    one — see ``set_station``).
    """

    def __init__(
        self,
        root_dir: Path | str,
        catalog: AssetCatalogPort,
        files: FileStorePort,
        archive: ArchiveSourcePort,
        *,
        detector: FileTypeDetector | None = None,
    ) -> None:
        """Construct a `Workspace` rooted at `root_dir`, wiring the three ports."""
        self._root_dir = Path(root_dir)
        self._catalog = catalog
        self._files = files
        self._archive = archive
        self._detector = detector or FileTypeDetector()

        # Pure & port-backed services.
        self._tree = DirectoryTree(root=self._root_dir)
        self._builder = FileManager(self._tree, self._files)
        self._inspector = LayoutInspector(self._files)
        self._ingestor = Ingestor(
            catalog=self._catalog,
            file_manager=self._builder,
            archive=self._archive,
            detector=self._detector,
        )

        # Mutable scope state.
        self._network: str | None = None
        self._station: str | None = None
        self._campaign: str | None = None
        self._survey: str | None = None

        # Metadata cache (explicit-load semantics).
        self._site: Site | None = None
        self._campaign_meta: Campaign | None = None
        self._survey_meta: Survey | None = None

    # ------------------------------------------------------------------
    # Test factory
    # ------------------------------------------------------------------

    @classmethod
    def for_test(
        cls,
        *,
        root: Path | str = Path("/virtual"),
        network: str | None = None,
        station: str | None = None,
        campaign: str | None = None,
        survey: str | None = None,
    ) -> "Workspace":
        """Build a Workspace backed entirely by in-memory adapters."""

        ws = cls(
            root_dir=root,
            catalog=InMemoryAssetStore(),
            files=InMemoryFileStore(),
            archive=FakeArchive(),
        )
        if network is not None:
            ws.set_network(network)
        if station is not None:
            ws.set_station(station)
        if campaign is not None:
            ws.set_campaign(campaign)
        if survey is not None:
            ws.set_survey(survey)
        return ws

    # ------------------------------------------------------------------
    # Scope readers
    # ------------------------------------------------------------------

    @property
    def root(self) -> Path:
        """Workspace root directory."""
        return self._root_dir

    @property
    def network_name(self) -> str | None:
        """Currently active network name, or None if unset."""
        return self._network

    @property
    def station_name(self) -> str | None:
        """Currently active station name, or None if unset."""
        return self._station

    @property
    def campaign_name(self) -> str | None:
        """Currently active campaign name, or None if unset."""
        return self._campaign

    @property
    def survey_name(self) -> str | None:
        """Currently active survey name, or None if unset."""
        return self._survey

    @property
    def has_network_station_campaign(self) -> bool:
        """Return True iff network, station, and campaign are all set."""
        return all(x is not None for x in (self._network, self._station, self._campaign))

    @property
    def scope(self) -> CampaignScope:
        """Return the active scope. Raises if any of N/S/C is unset."""
        missing = [
            level
            for level, value in (
                ("network", self._network),
                ("station", self._station),
                ("campaign", self._campaign),
            )
            if value is None
        ]
        if missing:
            raise ValueError(f"Incomplete scope; missing: {', '.join(missing)}")
        # mypy/pyright: at this point all three are non-None
        return CampaignScope(
            network=self._network,  # type: ignore[arg-type]
            station=self._station,  # type: ignore[arg-type]
            campaign=self._campaign,  # type: ignore[arg-type]
            survey=self._survey,
        )

    # ------------------------------------------------------------------
    # Scope mutators (cascading resets)
    # ------------------------------------------------------------------

    # Ordered hierarchy: each level's index determines what gets cleared
    # when that level is set or reset.
    _LEVELS = ("network", "station", "campaign", "survey")

    # Metadata fields that are invalidated when a level is set/reset.
    # Index matches _LEVELS: setting level i clears _meta_fields[i:].
    _META_FIELDS = (
        ("_site", "_campaign_meta", "_survey_meta"),  # network reset → clear all
        ("_site", "_campaign_meta", "_survey_meta"),  # station reset → clear all (conservative)
        ("_campaign_meta", "_survey_meta"),            # campaign reset → clear campaign+survey
        ("_survey_meta",),                             # survey reset → clear survey only
    )

    # Prerequisite check: level i requires level i-1 to be set first.
    _REQUIRES = (None, "network", "station", "campaign")

    def _set_level(self, level: str, value: str) -> None:
        """Set *level* to *value*, clear all lower levels and their metadata."""
        idx = self._LEVELS.index(level)
        prereq = self._REQUIRES[idx]
        if prereq is not None and getattr(self, f"_{prereq}") is None:
            raise ValueError(f"set_{prereq}() must be called before set_{level}()")
        setattr(self, f"_{level}", value)
        for lower in self._LEVELS[idx + 1:]:
            setattr(self, f"_{lower}", None)
        for field in self._META_FIELDS[idx]:
            setattr(self, field, None)

    def set_network(self, network: str) -> None:
        """Set the network. Clears station/campaign/survey and *all* metadata."""
        self._set_level("network", network)

    def set_station(self, station: str) -> None:
        """Set the station. Requires network. Clears campaign/survey and all metadata."""
        self._set_level("station", station)

    def set_campaign(self, campaign: str) -> None:
        """Set the campaign. Requires station. Clears survey + campaign/survey metadata."""
        self._set_level("campaign", campaign)

    def set_survey(self, survey: str) -> None:
        """Set the survey. Requires campaign. Clears survey metadata."""
        self._set_level("survey", survey)

    def set_network_station_campaign(
        self,
        network: str,
        station: str | None = None,
        campaign: str | None = None,
        survey: str | None = None,
    ) -> None:
        """Convenience: set up to four levels in one call."""
        self.set_network(network)
        if station is not None:
            self.set_station(station)
        if campaign is not None:
            self.set_campaign(campaign)
        if survey is not None:
            self.set_survey(survey)

    def _reset_from(self, level: str) -> None:
        """Clear *level* and all levels below it, plus their metadata."""
        idx = self._LEVELS.index(level)
        for lower in self._LEVELS[idx:]:
            setattr(self, f"_{lower}", None)
        for field in self._META_FIELDS[idx]:
            setattr(self, field, None)

    def reset_survey(self) -> None:
        """Clear the active survey and its cached metadata."""
        self._reset_from("survey")

    def reset_campaign(self) -> None:
        """Clear the active campaign (and survey) and their cached metadata."""
        self._reset_from("campaign")

    def reset_station(self) -> None:
        """Clear the active station, site metadata, and downstream scope."""
        self._reset_from("station")

    def reset_network(self) -> None:
        """Clear the active network and all downstream scope/metadata."""
        self._reset_from("network")

    # ------------------------------------------------------------------
    # Metadata loaders (explicit; never auto-loaded)
    # ------------------------------------------------------------------

    def load_site_metadata(self, site: "Site") -> None:
        """Cache `site` as the active site metadata."""
        self._site = site

    def load_campaign_metadata(self, campaign: "Campaign") -> None:
        """Cache `campaign` as the active campaign metadata."""
        self._campaign_meta = campaign

    def load_survey_metadata(self, survey: "Survey") -> None:
        """Cache `survey` as the active survey metadata."""
        self._survey_meta = survey

    # ------------------------------------------------------------------
    # Filesystem-driven metadata bootstrapping (for mid-process workflows)
    # ------------------------------------------------------------------

    def try_load_site_metadata_from_disk(self) -> bool:
        """Read the site metadata JSON from disk into the workspace.
        Returns ``True`` if a file was found and parsed. Used by mid-process
        callers that need to set scope from a station's metadata file.

        Only requires network and station to be set; the campaign may be
        unset (this method is typically called immediately after
        :meth:`set_station`).
        """

        if self._network is None or self._station is None:
            return False
        path = self._root_dir / self._network / self._station / "site_metadata.json"
        if self._files.is_file(path):
            self._site = Site.from_json(path)
            return True
        return False

    def select_campaign_from_metadata(self, campaign_id: str) -> None:
        """Find ``campaign_id`` in the loaded site metadata and activate it.
        Sets both the campaign name and the cached campaign metadata. Caller
        must have loaded site metadata first.
        """
        if self._site is None:
            raise ValueError("Site metadata must be loaded before select_campaign_from_metadata()")
        for campaign in self._site.campaigns:
            if campaign.name == campaign_id:
                self.set_campaign(campaign.name)
                self._campaign_meta = campaign
                return
        raise ValueError(f"Campaign {campaign_id!r} not found in site metadata")

    def select_survey_from_metadata(self, survey_id: str) -> None:
        """Find ``survey_id`` in the loaded campaign metadata and activate it."""
        if self._campaign_meta is None:
            raise ValueError(
                "Campaign metadata must be loaded before select_survey_from_metadata()"
            )
        for survey in self._campaign_meta.surveys:
            if survey.id == survey_id:
                self.set_survey(survey_id)
                self._survey_meta = survey
                return
        raise ValueError(f"Survey {survey_id!r} not found in campaign {self._campaign_meta.name!r}")

    # ------------------------------------------------------------------
    # Bootstrapping (no scope required)
    # ------------------------------------------------------------------

    def bootstrap(self) -> None:
        """Materialize the workspace root and pride dir. Scope-free."""
        self._builder.ensure_workspace()

    def ensure_network_dir(self, network: str) -> Path:
        """Materialize ``<root>/<network>``. Scope-free."""
        path = self._tree.network_dir(network)
        self._files.mkdir(path)
        return path

    def ensure_station_dir(self, network: str, station: str) -> Path:
        """Materialize ``<root>/<network>/<station>``. Scope-free."""
        path = self._tree.network_dir(network) / station
        self._files.mkdir(path)
        return path

    # ------------------------------------------------------------------
    # Metadata properties (read-only views of cached metadata)
    # ------------------------------------------------------------------

    @property
    def site(self) -> "Site | None":
        """Currently-loaded site metadata, or ``None``."""
        return self._site

    @property
    def campaign_meta(self) -> "Campaign | None":
        """Currently-loaded campaign metadata, or ``None``."""
        return self._campaign_meta

    @property
    def survey_meta(self) -> "Survey | None":
        """Currently-loaded survey metadata, or ``None``."""
        return self._survey_meta

    # ------------------------------------------------------------------
    # Layout — paths and materialization
    # ------------------------------------------------------------------

    @property
    def network_dir(self) -> Path:
        """Path to the active network directory."""
        return self._tree.network_dir(self.scope.network)

    @property
    def station_dir(self) -> Path:
        """Path to the active station directory."""
        return self._tree.station_dir(self.scope)

    @property
    def survey_dir(self) -> Path:
        """Path to the active survey directory; requires ``scope.survey``."""
        if self._survey is None:
            raise ValueError("scope.survey must be set to access survey path")
        return self._tree.survey_dir(self.scope)

    def campaign_layout(self) -> CampaignLayout:
        """Return the :class:`CampaignLayout` for the active campaign."""
        return self._tree.campaign(self.scope)

    def tiledb_layout(self) -> TileDBLayout:
        """Return the :class:`TileDBLayout` for the active station."""
        return self._tree.tiledb(self.scope)

    def garpos_survey(self) -> GARPOSLayout:
        """Return the :class:`GARPOSLayout` for the active survey; requires ``scope.survey``."""
        if self._survey is None:
            raise ValueError("scope.survey must be set to access garpos_survey()")
        return self._tree.garpos(self.scope)

    def ensure_station(self) -> TileDBLayout:
        """Materialize the station/TileDB layout on disk and return it."""
        return self._builder.ensure_station(self.scope)

    def ensure_campaign(self) -> CampaignLayout:
        """Materialize the active campaign layout on disk and return it."""
        return self._builder.ensure_campaign(self.scope)

    def ensure_garpos_survey(self) -> GARPOSLayout:
        """Materialize the GARPOS survey layout on disk; requires ``scope.survey``."""
        if self._survey is None:
            raise ValueError("scope.survey must be set to materialize garpos survey")
        return self._builder.ensure_garpos_survey(self.scope)

    def is_garpos_directory(self) -> bool:
        """Return True iff the active survey directory looks like a GARPOS layout."""
        return self._inspector.is_garpos_directory(self.garpos_survey())

    def find_rectified_shotdata(self) -> Path | None:
        """Locate rectified shotdata under the active GARPOS survey, if any."""
        return self._inspector.find_rectified_shotdata(self.garpos_survey())

    def find_filtered_shotdata(self) -> Path | None:
        """Locate filtered shotdata under the active survey, if any."""
        return self._inspector.find_filtered_shotdata(self.survey_dir)

    def list_campaigns(self) -> list[str]:
        """Names of campaign directories under the active station (year-prefixed)."""
        import re
        try:
            return sorted(
                p.name
                for p in self.station_dir.iterdir()
                if p.is_dir() and re.match(r"^\d{4}", p.name)
            )
        except (OSError, AttributeError):
            return []

    def list_surveys(self) -> list[str]:
        """Names of survey directories under the active campaign."""
        try:
            return sorted(p.name for p in self.campaign_layout().root.iterdir() if p.is_dir())
        except (OSError, AttributeError):
            return []

    @property
    def site_metadata_file(self) -> Path:
        """Path to the site metadata JSON for the active station."""
        return self.station_dir / "site_metadata.json"

    @property
    def campaign_metadata_file(self) -> Path:
        """Path to the campaign metadata JSON for the active campaign."""
        return self.campaign_layout().metadata_file

    @property
    def survey_metadata_file(self) -> Path:
        """Path to the survey metadata JSON for the active survey."""
        return self.survey_dir / "survey_meta.json"

    @property
    def pride_directory(self) -> Path:
        """Path to the workspace-level Pride PPP working directory."""
        return self._tree.pride_dir

    # ------------------------------------------------------------------
    # Asset catalog — scoped reads and writes
    # ------------------------------------------------------------------

    def all_assets(self, kind: AssetKind | None = None) -> list[AssetEntry]:
        """All cataloged assets in the active scope (optionally by kind)."""
        return self._catalog.assets_for(self.scope, kind)

    def asset_by_id(self, asset_id: int) -> AssetEntry | None:
        """Return the asset with ``asset_id``, or None if absent."""
        return self._catalog.by_id(asset_id)

    def assets_by_path(self, path: Path) -> list[AssetEntry]:
        """Return all cataloged assets whose ``local_path`` matches ``path``."""
        return self._catalog.by_local_path(path)

    def count_by_kind(self) -> dict[AssetKind, int]:
        """Return per-:class:`AssetKind` counts within the active scope."""
        return self._catalog.count_by_kind(self.scope)

    def dtype_counts(self) -> dict[str, int]:
        """Count-by-kind keyed by string value (legacy ``get_dtype_counts``)."""
        return {k.value: v for k, v in self._catalog.count_by_kind(self.scope).items()}

    def add_asset(self, asset: AssetEntry) -> AssetEntry:
        """Insert a new asset. Returns the entry with ``id`` populated."""
        return self._catalog.add(asset)

    def update_asset(self, entry: AssetEntry, **changes: object) -> AssetEntry:
        """Persist ``changes`` against ``entry`` and return the updated entry.
        Raises ``ValueError`` if ``entry.id`` is None; ``LookupError`` if no
        row matched.
        """
        if entry.id is None:
            raise ValueError(
                "update_asset requires a persisted entry (entry.id must not be None)"
            )
        new_entry = replace(entry, **changes)  # type: ignore[arg-type]
        if not self._catalog.update(new_entry):
            raise LookupError(f"No catalog row for asset id={entry.id}")
        return new_entry

    def update_asset_path(self, asset_id: int, local_path: Path | str) -> bool:
        """Set the ``local_path`` of the asset with ``asset_id``."""
        existing = self._catalog.by_id(asset_id)
        if existing is None:
            return False
        new_entry = replace(existing, local_path=Path(local_path))
        return self._catalog.update(new_entry)

    def local_assets(self, kind: AssetKind) -> list[AssetEntry]:
        """Assets of ``kind`` in scope that have a non-null ``local_path``."""
        return [a for a in self._catalog.assets_for(self.scope, kind) if a.local_path]

    def remote_file_exists_locally(self, kind: AssetKind, remote_path: str) -> bool:
        """Return True iff any in-scope asset of ``kind`` has a local copy of this URL."""
        from os.path import basename
        target = basename(remote_path)
        for a in self._catalog.assets_for(self.scope, kind):
            if a.local_path and target in str(a.local_path):
                return True
        return False

    def add_or_update_asset(self, entry: AssetEntry) -> AssetEntry | None:
        """Insert if no entry with the same ``local_path`` exists; else update.
        Idempotent. Returns the persisted entry, or ``None`` if input is ``None``.
        """
        if entry is None:
            return None
        if entry.local_path is not None:
            existing = self._catalog.by_local_path(entry.local_path)
            if existing:
                replaced = replace(entry, id=existing[0].id)
                self._catalog.update(replaced)
                return replaced
        return self._catalog.add(entry)

    def assets_to_process(
        self,
        parent_kind: AssetKind,
        child_kind: AssetKind | None = None,
        *,
        override: bool = False,
        local_only: bool = False,
    ) -> list[AssetEntry]:
        """Parent-kind entries lacking a ``child_kind`` result.
        When ``child_kind`` is None, falls back to ``is_processed``.
        """
        parents = self._catalog.assets_for(self.scope, parent_kind)
        if child_kind is None:
            candidates = parents if override else [p for p in parents if not p.is_processed]
        else:
            children = self._catalog.assets_for(self.scope, child_kind)
            parent_id_map = {p.id: p for p in parents if p.id is not None}
            if not override:
                for c in children:
                    if c.parent_id in parent_id_map:
                        parent_id_map.pop(c.parent_id, None)
            candidates = list(parent_id_map.values())
        if local_only:
            candidates = [e for e in candidates if e.local_path is not None]
        seen: dict[Path | None, AssetEntry] = {}
        for e in candidates:
            seen[e.local_path] = e
        return list(seen.values())

    def add_merge_job(
        self,
        parent_type: str,
        child_type: str,
        parent_ids: list[int] | list[str],
    ) -> None:
        """Record completion of a merge from ``parent_ids`` into a ``child_type``."""
        self._catalog.add_merge_job(parent_type, child_type, parent_ids)

    def is_merge_complete(
        self,
        parent_type: str,
        child_type: str,
        parent_ids: list[int] | list[str],
    ) -> bool:
        """Return True iff a matching merge job has been recorded previously."""
        return self._catalog.is_merge_complete(parent_type, child_type, parent_ids)

    # ------------------------------------------------------------------
    # Ingest — discover/ingest/download orchestration
    # ------------------------------------------------------------------

    def ingest_local(self, source_dir: Path) -> IngestReport:
        """Ingest assets from a local directory into the active scope."""
        return self._ingestor.ingest_local(self.scope, source_dir)

    def discover_archive(self, archive_url: str) -> IngestReport:
        """Discover assets at ``archive_url`` and catalog them in the active scope."""
        return self._ingestor.discover_archive(self.scope, archive_url)

    def discover_campaign(self) -> IngestReport:
        """Discover all canonical archive locations for the active campaign."""
        return self._ingestor.discover_campaign(self.scope)

    def list_archive_urls(self) -> list[str]:
        """Enumerate every archive file URL for the active campaign scope."""
        return self._ingestor.list_archive_urls(self.scope)

    def download_assets(
        self,
        kinds: list[AssetKind] | None = None,
        dest_dir: Path | None = None,
        *,
        override: bool = False,
        rinex_1hz: bool = False,
    ) -> IngestReport:
        """Download remote assets in scope (optionally filtered by ``kinds``) to ``dest_dir``."""
        return self._ingestor.download(
            self.scope,
            kinds,
            dest_dir,
            override=override,
            rinex_1hz=rinex_1hz,
        )

    @property
    def archive(self) -> ArchiveSourcePort:
        """Return the injected :class:`ArchiveSource` adapter."""
        return self._archive

    # ------------------------------------------------------------------
    # Lower-level access (intentionally underscored — internal use)
    # ------------------------------------------------------------------

    @property
    def _tree_view(self) -> DirectoryTree:
        """Pure tree, exposed for tests and adapters that need raw paths."""
        return self._tree

    def build_tiledb(self) -> TileDBRegistry:
        """Materialize TileDB directories and return array handles for the active station."""
        from earthscope_sfg_tools.tiledb_integration import (
            TDBAcousticArray,
            TDBGNSSObsArray,
            TDBIMUPositionArray,
            TDBKinPositionArray,
            TDBShotDataArray,
        )

        layout = self._builder.ensure_station(self.scope)
        return TileDBRegistry(
            acoustic=TDBAcousticArray(layout.acoustic),
            kin_position=TDBKinPositionArray(layout.kin_position),
            imu_position=TDBIMUPositionArray(layout.imu_position),
            shotdata=TDBShotDataArray(layout.shotdata),
            shotdata_pre=TDBShotDataArray(layout.shotdata_pre),
            gnss_obs=TDBGNSSObsArray(layout.gnss_obs),
            gnss_obs_secondary=TDBGNSSObsArray(layout.gnss_obs_secondary),
        )

    def load_or_fetch_site_metadata(
        self,
        explicit: Site | Path | str | None = None,
    ) -> Site | None:
        """Three-source fallback: explicit arg → disk → archive. Persists to disk on fetch.

        Args:
            explicit: A :class:`Site` object, path to a JSON file, or ``None``.
                      When provided it takes priority over the disk copy.
        Returns:
            The loaded :class:`Site`, or ``None`` if no source succeeded.
        """
        from earthscope_sfg_tools.datamodels.metadata import Site as _Site

        if self._network is None or self._station is None:
            raise ValueError("network and station must be set before loading site metadata")

        write_dest = self._root_dir / self._network / self._station / "site_metadata.json"

        # Prefer explicit → disk → archive; flip order when explicit is absent.
        sources: list = [explicit, write_dest] if explicit is not None else [write_dest, None]

        for source in sources:
            if isinstance(source, str):
                source = Path(source)

            if isinstance(source, _Site):
                with open(write_dest, "w") as f:
                    json.dump(source.model_dump(mode="json"), f, indent=4)
                return source

            if isinstance(source, Path) and source.exists():
                try:
                    return _Site.from_json(source)
                except Exception as exc:
                    warnings.warn(f"Error loading site metadata from {source}: {exc}")

            if source is None:
                try:
                    site = self._archive.load_site_metadata(
                        network=self._network,
                        station=self._station,
                    )
                    with open(write_dest, "w") as f:
                        json.dump(site.model_dump(mode="json"), f, indent=4)
                    return site
                except Exception as exc:
                    warnings.warn(f"Error loading site metadata from the ES archive: {exc}")

        return None

    def activate_campaign(
        self,
        campaign_id: str,
        *,
        from_metadata: bool = False,
    ) -> CampaignLayout:
        """Set campaign scope and materialize campaign directories.

        Args:
            campaign_id: The campaign ID to activate.
            from_metadata: When ``True``, selects from already-loaded site
                           metadata (requires :meth:`load_site_metadata` to
                           have been called first).
        Returns:
            The materialized :class:`CampaignLayout`.
        """
        if from_metadata:
            self.select_campaign_from_metadata(campaign_id)
        else:
            self.set_campaign(campaign_id)
        return self._builder.ensure_campaign(self.scope)

    # ------------------------------------------------------------------
    # Resource lifecycle
    # ------------------------------------------------------------------

    def close(self) -> None:
        """Close all owned ports (catalog, files, archive)."""
        self._catalog.close()
        self._files.close()
        self._archive.close()

    def __exit__(self, exc_type, exc, tb) -> None:  # noqa: D401
        self.close()


__all__ = ["TileDBRegistry", "Workspace", "_build_default_workspace", "_to_asset_kind"]
