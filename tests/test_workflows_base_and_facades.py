"""Tests for the post-RFC-A workflow infrastructure: ``Workspace``, the four
façades, ``WorkflowBase``, plus the new ``Ingestor.discover_campaign`` and
``LayoutInspector`` domain primitives.

These tests run entirely against in-memory adapters — no disk, no network,
no database.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from earthscope_sfg_workflows.data_mgmt import (
    AssetEntry,
    AssetKind,
    CampaignScope,
    DirectoryTree,
    LayoutInspector,
)
from earthscope_sfg_workflows.data_mgmt.archives.earthscope._archive_urls import (
    ARCHIVE_PREFIX,
    canonical_campaign_urls,
    list_campaign_archive_urls,
)
from earthscope_sfg_workflows.data_mgmt.adapters.test_adapters import (
    FakeArchive,
    InMemoryAssetStore,
    InMemoryFileStore,
)
from earthscope_sfg_workflows.data_mgmt.core import (
    FileManager,
    FileTypeDetector,
    Ingestor,
)
from earthscope_sfg_workflows.workflows.base import (
    WorkflowBase,
    validate_network_station_campaign,
)
from earthscope_sfg_workflows.workflows.workspace import Workspace


# ---------------------------------------------------------------------------
# Workspace — scope semantics
# ---------------------------------------------------------------------------


class TestWorkspaceScope:
    def test_scope_raises_when_incomplete(self):
        ws = Workspace.for_test()
        with pytest.raises(ValueError, match="network"):
            _ = ws.scope

    def test_scope_returns_campaign_scope(self):
        ws = Workspace.for_test(network="N", station="S", campaign="2026_A")
        assert ws.scope == CampaignScope("N", "S", "2026_A")

    def test_set_station_requires_network(self):
        ws = Workspace.for_test()
        with pytest.raises(ValueError, match="network"):
            ws.set_station("S")

    def test_set_campaign_requires_station(self):
        ws = Workspace.for_test(network="N")
        with pytest.raises(ValueError, match="station"):
            ws.set_campaign("C")

    def test_set_survey_requires_campaign(self):
        ws = Workspace.for_test(network="N", station="S")
        with pytest.raises(ValueError, match="campaign"):
            ws.set_survey("V")

    def test_set_network_clears_descendants_and_metadata(self):
        ws = Workspace.for_test(network="N", station="S", campaign="2026_A", survey="V")
        ws.load_site_metadata(object())
        ws.load_campaign_metadata(object())
        ws.load_survey_metadata(object())

        ws.set_network("N2")

        assert ws.network_name == "N2"
        assert ws.station_name is None
        assert ws.campaign_name is None
        assert ws.survey_name is None
        assert ws.metadata.site is None
        assert ws.metadata.campaign is None
        assert ws.metadata.survey is None

    def test_set_station_always_clears_site_metadata(self):
        """PRD decision: site metadata is always cleared on station change."""
        ws = Workspace.for_test(network="N", station="S")
        ws.load_site_metadata(object())
        assert ws.metadata.site is not None

        ws.set_station("S")  # same id, still clears
        assert ws.metadata.site is None

    def test_set_campaign_clears_survey_only(self):
        ws = Workspace.for_test(network="N", station="S", campaign="C", survey="V")
        site_obj = object()
        ws.load_site_metadata(site_obj)

        ws.set_campaign("C2")

        assert ws.campaign_name == "C2"
        assert ws.survey_name is None
        # Site metadata persists across campaign change.
        assert ws.metadata.site is site_obj


# ---------------------------------------------------------------------------
# LayoutFacade
# ---------------------------------------------------------------------------


class TestLayoutFacade:
    def test_paths_match_directory_tree(self):
        ws = Workspace.for_test(root="/data", network="N", station="S", campaign="2026_A")
        tree = DirectoryTree(root=Path("/data"))
        scope = CampaignScope("N", "S", "2026_A")

        assert ws.layout.network == tree.network_dir("N")
        assert ws.layout.station == tree.station_dir(scope)
        assert ws.layout.campaign().root == tree.campaign(scope).root

    def test_garpos_survey_requires_survey(self):
        ws = Workspace.for_test(root="/data", network="N", station="S", campaign="C")
        with pytest.raises(ValueError, match="survey"):
            ws.layout.garpos_survey()

    def test_ensure_campaign_materializes_dirs(self):
        ws = Workspace.for_test(root="/data", network="N", station="S", campaign="2026_A")
        layout = ws.layout.ensure_campaign()
        for path in layout.standard_dirs:
            assert ws._files.is_dir(path), f"{path} not materialized"


# ---------------------------------------------------------------------------
# AssetQueryFacade — frozen update semantics
# ---------------------------------------------------------------------------


class TestAssetQueryFacade:
    def _seed(self) -> Workspace:
        ws = Workspace.for_test(network="N", station="S", campaign="C")
        ws.assets.add(
            AssetEntry(
                kind=AssetKind.NOVATEL,
                scope=ws.scope,
                local_path=Path("/tmp/a.bin"),
            )
        )
        ws.assets.add(
            AssetEntry(
                kind=AssetKind.RINEX2,
                scope=ws.scope,
                local_path=Path("/tmp/b.23o"),
            )
        )
        return ws

    def test_all_filters_by_kind(self):
        ws = self._seed()
        novatels = ws.assets.all(AssetKind.NOVATEL)
        assert len(novatels) == 1
        assert novatels[0].kind == AssetKind.NOVATEL

    def test_count_by_kind(self):
        ws = self._seed()
        counts = ws.assets.count_by_kind()
        assert counts[AssetKind.NOVATEL] == 1
        assert counts[AssetKind.RINEX2] == 1

    def test_update_returns_new_frozen_entry(self):
        ws = self._seed()
        entry = ws.assets.all(AssetKind.NOVATEL)[0]
        assert entry.is_processed is False

        updated = ws.assets.update(entry, is_processed=True)

        assert updated is not entry  # frozen — different object
        assert updated.is_processed is True
        # Catalog reflects the change.
        assert ws.assets.all(AssetKind.NOVATEL)[0].is_processed is True

    def test_update_unknown_id_raises(self):
        ws = Workspace.for_test(network="N", station="S", campaign="C")
        ghost = AssetEntry(
            kind=AssetKind.NOVATEL,
            scope=ws.scope,
            id=999,
            local_path=Path("/x"),
        )
        with pytest.raises(LookupError):
            ws.assets.update(ghost, is_processed=True)

    def test_update_requires_persisted_entry(self):
        ws = Workspace.for_test(network="N", station="S", campaign="C")
        unsaved = AssetEntry(kind=AssetKind.NOVATEL, scope=ws.scope, local_path=Path("/y"))
        with pytest.raises(ValueError, match="entry.id"):
            ws.assets.update(unsaved, is_processed=True)


# ---------------------------------------------------------------------------
# Ingestor.discover_campaign
# ---------------------------------------------------------------------------


class TestDiscoverCampaign:
    def test_canonical_urls_compose_year_from_campaign(self):
        scope = CampaignScope("CAS", "NCB1", "2026_A_FOO")
        urls = canonical_campaign_urls(scope)
        assert all(u.startswith(ARCHIVE_PREFIX) for u in urls)
        assert urls[0].endswith("/CAS/2026/NCB1/2026_A_FOO/raw")
        assert urls[1].endswith("/CAS/2026/NCB1/2026_A_FOO/metadata")
        assert urls[2].endswith("/CAS/2026/NCB1/2026_A_FOO/rinex_1Hz")
        assert urls[3].endswith("/CAS/2026/NCB1/2026_A_FOO/rinex_10Hz")

    def test_discover_campaign_aggregates_four_urls(self):
        catalog = InMemoryAssetStore()
        files = InMemoryFileStore()
        archive = FakeArchive()
        scope = CampaignScope("CAS", "NCB1", "2026_A")

        raw, meta, r1, r10 = canonical_campaign_urls(scope)
        archive.seed(f"{raw}/NOV770_data.bin", b"x")
        archive.seed(f"{meta}/master.xml", b"<x/>")
        archive.seed(f"{r1}/foo.23o", b"r")
        # rinex 10Hz intentionally empty — partial success path

        ingestor = Ingestor(
            catalog=catalog,
            file_manager=FileManager(DirectoryTree(root=Path("/")), files),
            archive=archive,
            detector=FileTypeDetector(),
        )

        report = ingestor.discover_campaign(scope)

        # NOV770, MASTER, RINEX2 each match a default detector pattern.
        assert report.cataloged == 3
        kinds = {a.kind for a in catalog.assets_for(scope)}
        assert AssetKind.NOVATEL770 in kinds
        assert AssetKind.MASTER in kinds
        assert AssetKind.RINEX2 in kinds

    def test_list_archive_urls_is_side_effect_free(self):
        """Tracer-bullet replacement for legacy ``list_campaign_files``.

        Lists raw / metadata / metadata/ctd / RINEX 1Hz / RINEX 10Hz without
        writing to the catalog.
        """
        catalog = InMemoryAssetStore()
        archive = FakeArchive()
        scope = CampaignScope("CAS", "NCB1", "2026_A")

        raw, meta, r1, _r10 = canonical_campaign_urls(scope)
        archive.seed(f"{raw}/NOV770_data.bin", b"x")
        archive.seed(f"{meta}/master.xml", b"<x/>")
        archive.seed(f"{meta}/ctd/CTD_001.txt", b"c")
        archive.seed(f"{r1}/foo.23o", b"r")

        urls = list_campaign_archive_urls(archive, scope)

        assert sorted(urls) == sorted(
            [
                f"{raw}/NOV770_data.bin",
                f"{meta}/master.xml",
                f"{meta}/ctd/CTD_001.txt",
                f"{r1}/foo.23o",
            ]
        )
        # Side-effect-free — no catalog writes.
        assert catalog.assets_for(scope) == []

    def test_facade_list_archive_urls_matches_helper(self):
        ws = Workspace.for_test(network="CAS", station="NCB1", campaign="2026_A")
        raw, meta, _r1, _r10 = canonical_campaign_urls(ws.scope)
        ws._archive.seed(f"{raw}/NOV770_data.bin", b"x")  # type: ignore[attr-defined]
        ws._archive.seed(f"{meta}/ctd/CTD.txt", b"c")  # type: ignore[attr-defined]

        urls = ws.ingest.list_archive_urls()

        assert sorted(urls) == sorted([f"{raw}/NOV770_data.bin", f"{meta}/ctd/CTD.txt"])


# ---------------------------------------------------------------------------
# LayoutInspector
# ---------------------------------------------------------------------------


class TestLayoutInspector:
    def test_is_garpos_directory_requires_both_default_files(self):
        ws = Workspace.for_test(root="/d", network="N", station="S", campaign="C", survey="V")
        ws.layout.ensure_garpos_survey()
        layout = ws.layout.garpos_survey()
        inspector = LayoutInspector(ws._files)

        assert inspector.is_garpos_directory(layout) is False

        ws._files.write_bytes(layout.obs_file, b"x")
        assert inspector.is_garpos_directory(layout) is False  # only one

        ws._files.write_bytes(layout.settings_file, b"x")
        assert inspector.is_garpos_directory(layout) is True

    def test_find_rectified_shotdata_returns_none_if_missing(self):
        ws = Workspace.for_test(root="/d", network="N", station="S", campaign="C", survey="V")
        ws.layout.ensure_garpos_survey()
        layout = ws.layout.garpos_survey()
        inspector = LayoutInspector(ws._files)

        assert inspector.find_rectified_shotdata(layout) is None

        target = layout.root / "abc_rectified.csv"
        ws._files.write_bytes(target, b"")
        assert inspector.find_rectified_shotdata(layout) == target


# ---------------------------------------------------------------------------
# WorkflowBase + decorators
# ---------------------------------------------------------------------------


class _DummyWorkflow(WorkflowBase):
    @validate_network_station_campaign
    def do_thing(self) -> str:
        return "ok"


class TestWorkflowBase:
    def test_directory_property_matches_workspace_root(self):
        ws = Workspace.for_test(root="/data")
        wf = _DummyWorkflow(ws)
        assert wf.directory == Path("/data")

    def test_decorator_blocks_incomplete_scope(self):
        ws = Workspace.for_test(network="N")
        wf = _DummyWorkflow(ws)
        with pytest.raises(ValueError, match="Station"):
            wf.do_thing()

    def test_decorator_passes_when_scope_is_complete(self):
        ws = Workspace.for_test(network="N", station="S", campaign="C")
        wf = _DummyWorkflow(ws)
        assert wf.do_thing() == "ok"

    def test_no_port_attributes_leaked(self):
        wf = _DummyWorkflow(Workspace.for_test())
        # Façade-only access. The base must not expose ports as attributes.
        assert not hasattr(wf, "asset_catalog")
        assert not hasattr(wf, "directory_handler")
        assert not hasattr(wf, "catalog")
        assert not hasattr(wf, "files")
        assert not hasattr(wf, "archive")
