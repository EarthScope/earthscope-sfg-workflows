"""The :class:`Workspace` — single object owning ports + scope + metadata.

Replaces the 12-field ``WorkflowContext`` and the
``DirectoryHandler`` / ``PreProcessCatalogHandler`` pair from the legacy
:class:`WorkflowABC`. Subclasses of :class:`workflows.base.WorkflowBase`
receive a single ``workspace`` argument and reach the data layer only via
the four façades exposed on this object: ``layout``, ``metadata``,
``assets``, ``ingest``.

See ``plans/prds/2026-05-05-workflow-base-and-facades.md``.
"""

from __future__ import annotations

from contextlib import AbstractContextManager
from pathlib import Path
from typing import TYPE_CHECKING

from earthscope_sfg_workflows.data_mgmt.adapters.memory import InMemoryAssetStore
from earthscope_sfg_workflows.data_mgmt.core import (
    FileTypeDetector,
    Ingestor,
    LayoutInspector,
    TreeBuilder,
)
from earthscope_sfg_workflows.data_mgmt.model import CampaignScope, DirectoryTree
from earthscope_sfg_workflows.data_mgmt.ports import (
    ArchiveSource,
    AssetStore,
    FileStore,
)
from earthscope_sfg_workflows.data_mgmt.adapters.memory import (
    FakeArchive,
    InMemoryAssetStore,
    InMemoryFileStore,
)

from .facades import (
    AssetQueryFacade,
    IngestFacade,
    LayoutFacade,
    MetadataFacade,
)

from earthscope_sfg_tools.datamodels.metadata import Site

if TYPE_CHECKING:  # pragma: no cover
    from earthscope_sfg_tools.datamodels.metadata import Campaign, Site, Survey


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
        catalog: AssetStore,
        files: FileStore,
        archive: ArchiveSource,
        *,
        detector: FileTypeDetector | None = None,
    ) -> None:
        self._root_dir = Path(root_dir)
        self._catalog = catalog
        self._files = files
        self._archive = archive
        self._detector = detector or FileTypeDetector()

        # Pure & port-backed services.
        self._tree = DirectoryTree(root=self._root_dir)
        self._builder = TreeBuilder(self._tree, self._files)
        self._inspector = LayoutInspector(self._files)
        self._ingestor = Ingestor(
            catalog=self._catalog,
            files=self._files,
            archive=self._archive,
            detector=self._detector,
            tree=self._tree,
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
        return self._root_dir

    @property
    def network_name(self) -> str | None:
        return self._network

    @property
    def station_name(self) -> str | None:
        return self._station

    @property
    def campaign_name(self) -> str | None:
        return self._campaign

    @property
    def survey_name(self) -> str | None:
        return self._survey

    @property
    def has_network_station_campaign(self) -> bool:
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

    def set_network(self, network: str) -> None:
        """Set the network. Clears station/campaign/survey and *all* metadata."""
        self._network = network
        self._station = None
        self._campaign = None
        self._survey = None
        self._site = None
        self._campaign_meta = None
        self._survey_meta = None

    def set_station(self, station: str) -> None:
        """Set the station. Requires network. Clears campaign/survey
        and all metadata. Site metadata is **always** cleared on station
        change (PRD decision: conservative — mismatched site metadata
        silently breaks GARPOS).
        """
        if self._network is None:
            raise ValueError("set_network() must be called before set_station()")
        self._station = station
        self._campaign = None
        self._survey = None
        self._site = None
        self._campaign_meta = None
        self._survey_meta = None

    def set_campaign(self, campaign: str) -> None:
        """Set the campaign. Requires station. Clears survey + campaign/survey metadata."""
        if self._station is None:
            raise ValueError("set_station() must be called before set_campaign()")
        self._campaign = campaign
        self._survey = None
        self._campaign_meta = None
        self._survey_meta = None

    def set_survey(self, survey: str) -> None:
        """Set the survey. Requires campaign. Clears survey metadata."""
        if self._campaign is None:
            raise ValueError("set_campaign() must be called before set_survey()")
        self._survey = survey
        self._survey_meta = None

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

    def reset_survey(self) -> None:
        self._survey = None
        self._survey_meta = None

    def reset_campaign(self) -> None:
        self._campaign = None
        self._campaign_meta = None
        self.reset_survey()

    def reset_station(self) -> None:
        self._station = None
        self._site = None
        self.reset_campaign()

    def reset_network(self) -> None:
        self._network = None
        self.reset_station()

    # ------------------------------------------------------------------
    # Metadata loaders (explicit; never auto-loaded)
    # ------------------------------------------------------------------

    def load_site_metadata(self, site: "Site") -> None:
        self._site = site

    def load_campaign_metadata(self, campaign: "Campaign") -> None:
        self._campaign_meta = campaign

    def load_survey_metadata(self, survey: "Survey") -> None:
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
    # Façade properties
    # ------------------------------------------------------------------

    @property
    def layout(self) -> LayoutFacade:
        return LayoutFacade(
            _tree=self._tree,
            _builder=self._builder,
            _inspector=self._inspector,
            _scope=self.scope,
        )

    @property
    def assets(self) -> AssetQueryFacade:
        return AssetQueryFacade(_catalog=self._catalog, _scope=self.scope)

    @property
    def ingest(self) -> IngestFacade:
        return IngestFacade(_ingestor=self._ingestor, _scope=self.scope)

    @property
    def archive(self) -> ArchiveSource:
        """Return the injected :class:`ArchiveSource` adapter.

        Exposed for callers that need archive operations beyond what
        :class:`IngestFacade` covers (e.g. metadata loading).
        """
        return self._archive

    @property
    def metadata(self) -> MetadataFacade:
        return MetadataFacade(
            site=self._site,
            campaign=self._campaign_meta,
            survey=self._survey_meta,
        )

    # ------------------------------------------------------------------
    # Lower-level access (intentionally underscored — internal use)
    # ------------------------------------------------------------------

    @property
    def _tree_view(self) -> DirectoryTree:
        """Pure tree, exposed for tests and adapters that need raw paths."""
        return self._tree

    # ------------------------------------------------------------------
    # Resource lifecycle
    # ------------------------------------------------------------------

    def close(self) -> None:
        self._catalog.close()
        self._files.close()
        self._archive.close()

    def __exit__(self, exc_type, exc, tb) -> None:  # noqa: D401
        self.close()


__all__ = ["Workspace"]
