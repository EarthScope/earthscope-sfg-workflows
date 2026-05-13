"""Tests for the post-RFC-A workflow infrastructure: ``Workspace``, the four
façades, plus the new ``Ingestor.discover_campaign`` and
``LayoutInspector`` domain primitives.

These tests run entirely against in-memory adapters — no disk, no network,
no database.
"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from earthscope_sfg_workflows.data_mgmt import (
    AssetEntry,
    AssetKind,
    SFGScope,
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
from earthscope_sfg_workflows.workflows.session import StationSession as Workspace


# ---------------------------------------------------------------------------
# Workspace — scope semantics
# ---------------------------------------------------------------------------


class TestWorkspaceScope:
    def test_network_station_always_set_after_construction(self):
        ws = Workspace.for_test(network="N", station="S")
        assert ws.scope.network == "N"
        assert ws.scope.station == "S"

    def test_scope_campaign_none_before_set(self):
        ws = Workspace.for_test(network="N", station="S")
        assert ws.scope.campaign is None

    def test_scope_returns_campaign_scope_when_campaign_set(self):
        ws = Workspace.for_test(network="N", station="S", campaign="2026_A")
        assert ws.scope == SFGScope("N", "S", "2026_A")

    def test_set_survey_requires_campaign(self):
        ws = Workspace.for_test(network="N", station="S")
        with pytest.raises(ValueError, match="campaign"):
            ws.set_survey("V")

    def test_set_campaign_clears_survey_only(self):
        ws = Workspace.for_test(network="N", station="S", campaign="C", survey="V")
        site_obj = object()
        ws.load_site_metadata(site_obj)

        ws.set_campaign("C2")

        assert ws.scope.campaign == "C2"
        assert ws.scope.survey is None
        # Site metadata persists across campaign changes.
        assert ws.site is site_obj

    def test_set_campaign_returns_campaign_layout(self):
        ws = Workspace.for_test(network="N", station="S")
        layout = ws.set_campaign("2026_A")
        assert layout is not None
        assert ws.scope.campaign == "2026_A"

    def test_campaign_name_none_before_set(self):
        ws = Workspace.for_test(network="N", station="S")
        assert ws.scope.campaign is None

    def test_survey_name_cleared_by_set_campaign(self):
        ws = Workspace.for_test(network="N", station="S", campaign="C", survey="V")
        assert ws.scope.survey == "V"
        ws.set_campaign("C2")
        assert ws.scope.survey is None


# ---------------------------------------------------------------------------
# Decorator enforcement on CampaignSession methods
# ---------------------------------------------------------------------------


class TestSessionDecorators:
    def test_campaign_layout_requires_campaign(self):
        ws = Workspace.for_test(network="N", station="S")
        with pytest.raises(ValueError, match="campaign"):
            ws.campaign_layout()

    def test_ensure_campaign_requires_campaign(self):
        ws = Workspace.for_test(network="N", station="S")
        with pytest.raises(ValueError, match="campaign"):
            ws.ensure_campaign()

    def test_garpos_survey_requires_survey(self):
        ws = Workspace.for_test(network="N", station="S", campaign="C")
        with pytest.raises(ValueError, match="survey"):
            ws.garpos_survey()

    def test_survey_dir_requires_survey(self):
        ws = Workspace.for_test(network="N", station="S", campaign="C")
        with pytest.raises(ValueError, match="survey"):
            _ = ws.survey_dir

    def test_scope_requires_campaign_for_catalog_calls(self):
        ws = Workspace.for_test(network="N", station="S")
        # scope is always valid; campaign is None until set_campaign() is called
        assert ws.scope.campaign is None
        # catalog.assets_for with no campaign returns an empty result (no raise)
        results = ws.catalog.assets_for(ws.scope)
        assert results == []

    def test_tiledb_layout_available_without_campaign(self):
        """TileDB is station-scoped — no campaign needed."""
        ws = Workspace.for_test(network="N", station="S")
        layout = ws.tiledb_layout()
        assert layout is not None

    def test_station_dir_available_without_campaign(self):
        ws = Workspace.for_test(network="N", station="S")
        assert ws.station_dir is not None

    def test_list_campaigns_available_without_campaign(self):
        ws = Workspace.for_test(network="N", station="S")
        campaigns = ws.list_campaigns()
        assert isinstance(campaigns, list)


# ---------------------------------------------------------------------------
# LayoutFacade
# ---------------------------------------------------------------------------


class TestLayoutFacade:
    def test_paths_match_directory_tree(self):
        ws = Workspace.for_test(root="/data", network="N", station="S", campaign="2026_A")
        tree = DirectoryTree(root=Path("/data"))
        scope = SFGScope("N", "S", "2026_A")

        assert ws.network_dir == tree.network_dir("N")
        assert ws.station_dir == tree.station_dir(scope)
        assert ws.campaign_layout().root == tree.campaign(scope).root

    def test_garpos_survey_requires_survey(self):
        ws = Workspace.for_test(root="/data", network="N", station="S", campaign="C")
        with pytest.raises(ValueError, match="survey"):
            ws.garpos_survey()

    def test_ensure_campaign_materializes_dirs(self):
        ws = Workspace.for_test(root="/data", network="N", station="S", campaign="2026_A")
        layout = ws.ensure_campaign()
        for path in layout.standard_dirs:
            assert ws._files.is_dir(path), f"{path} not materialized"


# ---------------------------------------------------------------------------
# AssetQueryFacade — frozen update semantics
# ---------------------------------------------------------------------------


class TestAssetQueryFacade:
    def _seed(self) -> Workspace:
        ws = Workspace.for_test(network="N", station="S", campaign="C")
        ws.catalog.add(AssetEntry(kind=AssetKind.NOVATEL, scope=ws.scope, local_path=Path("/tmp/a.bin")))
        ws.catalog.add(AssetEntry(kind=AssetKind.RINEX2, scope=ws.scope, local_path=Path("/tmp/b.23o")))
        return ws

    def test_all_filters_by_kind(self):
        ws = self._seed()
        novatels = ws.catalog.assets_for(ws.scope, AssetKind.NOVATEL)
        assert len(novatels) == 1
        assert novatels[0].kind == AssetKind.NOVATEL

    def test_count_by_kind(self):
        ws = self._seed()
        counts = ws.catalog.count_by_kind(ws.scope)
        assert counts[AssetKind.NOVATEL] == 1
        assert counts[AssetKind.RINEX2] == 1

    def test_update_returns_new_frozen_entry(self):
        ws = self._seed()
        entry = ws.catalog.assets_for(ws.scope, AssetKind.NOVATEL)[0]
        assert entry.is_processed is False

        updated = replace(entry, is_processed=True)
        ws.catalog.update(updated)

        assert updated is not entry  # frozen — different object
        assert updated.is_processed is True
        assert ws.catalog.assets_for(ws.scope, AssetKind.NOVATEL)[0].is_processed is True

    def test_update_unknown_id_returns_false(self):
        ws = Workspace.for_test(network="N", station="S", campaign="C")
        ghost = AssetEntry(kind=AssetKind.NOVATEL, scope=ws.scope, id=999, local_path=Path("/x"))
        assert ws.catalog.update(ghost) is False

    def test_catalog_requires_campaign_via_scope(self):
        ws = Workspace.for_test(network="N", station="S")
        # scope is always valid; campaign slot is None before set_campaign()
        assert ws.scope.campaign is None


# ---------------------------------------------------------------------------
# Ingestor.discover_campaign
# ---------------------------------------------------------------------------


class TestDiscoverCampaign:
    def test_canonical_urls_compose_year_from_campaign(self):
        scope = SFGScope("CAS", "NCB1", "2026_A_FOO")
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
        scope = SFGScope("CAS", "NCB1", "2026_A")

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
        scope = SFGScope("CAS", "NCB1", "2026_A")

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

        urls = ws.ingestor.list_archive_urls(ws.scope)

        assert sorted(urls) == sorted([f"{raw}/NOV770_data.bin", f"{meta}/ctd/CTD.txt"])


# ---------------------------------------------------------------------------
# LayoutInspector
# ---------------------------------------------------------------------------


class TestLayoutInspector:
    def test_is_garpos_directory_requires_both_default_files(self):
        ws = Workspace.for_test(root="/d", network="N", station="S", campaign="C", survey="V")
        ws.ensure_garpos_survey()
        layout = ws.garpos_survey()
        inspector = LayoutInspector(ws._files)

        assert inspector.is_garpos_directory(layout) is False

        ws._files.write_bytes(layout.obs_file, b"x")
        assert inspector.is_garpos_directory(layout) is False  # only one

        ws._files.write_bytes(layout.settings_file, b"x")
        assert inspector.is_garpos_directory(layout) is True

    def test_find_rectified_shotdata_returns_none_if_missing(self):
        ws = Workspace.for_test(root="/d", network="N", station="S", campaign="C", survey="V")
        ws.ensure_garpos_survey()
        layout = ws.garpos_survey()
        inspector = LayoutInspector(ws._files)

        assert inspector.find_rectified_shotdata(layout) is None

        target = layout.root / "abc_rectified.csv"
        ws._files.write_bytes(target, b"")
        assert inspector.find_rectified_shotdata(layout) == target


# ---------------------------------------------------------------------------
# Workspace class
# ---------------------------------------------------------------------------

from earthscope_sfg_workflows.workflows.workspace import Workspace as RealWorkspace  # noqa: E402


class TestRealWorkspace:
    def _ws(self, **kw):
        return RealWorkspace.for_test(**kw)

    def test_for_test_has_root(self):
        ws = self._ws(root="/data")
        assert ws.root == Path("/data")

    def test_session_raises_without_active(self):
        ws = self._ws()
        with pytest.raises(RuntimeError, match="No active session"):
            _ = ws.session

    def test_set_active_returns_session(self):
        ws = self._ws()
        sess = ws.set_active("NET", "STA")
        assert sess.scope.network == "NET"
        assert sess.scope.station == "STA"
        assert ws.session is sess

    def test_set_active_sets_campaign(self):
        ws = self._ws()
        sess = ws.set_active("NET", "STA", campaign="2026_A")
        assert sess.scope.campaign == "2026_A"

    def test_get_session_does_not_change_active(self):
        ws = self._ws()
        ws.set_active("NET", "STA")
        ws.get_session("NET2", "STA2")
        assert ws.session.scope.network == "NET"

    def test_sessions_are_cached(self):
        ws = self._ws()
        s1 = ws.set_active("NET", "STA")
        s2 = ws.get_session("NET", "STA")
        assert s1 is s2

    def test_list_networks_empty(self):
        ws = self._ws()
        assert ws.list_networks() == []

    def test_list_campaigns_via_distinct_values(self):
        from earthscope_sfg_workflows.data_mgmt.adapters.memory import InMemoryAssetStore
        from earthscope_sfg_workflows.data_mgmt.model import AssetEntry, AssetKind, SFGScope

        cat = InMemoryAssetStore()
        cat.add(AssetEntry(scope=SFGScope("NET", "STA", "2025_A"), kind=AssetKind.NOVATEL, local_path=Path("/f1")))
        cat.add(AssetEntry(scope=SFGScope("NET", "STA", "2026_B"), kind=AssetKind.NOVATEL, local_path=Path("/f2")))
        ws = self._ws(catalog=cat)
        campaigns = ws.list_campaigns("NET", "STA")
        assert campaigns == ["2025_A", "2026_B"]

    def test_list_stations_filters_by_network(self):
        from earthscope_sfg_workflows.data_mgmt.adapters.memory import InMemoryAssetStore
        from earthscope_sfg_workflows.data_mgmt.model import AssetEntry, AssetKind, SFGScope

        cat = InMemoryAssetStore()
        cat.add(AssetEntry(scope=SFGScope("NET1", "STA1", "C"), kind=AssetKind.NOVATEL, local_path=Path("/a")))
        cat.add(AssetEntry(scope=SFGScope("NET2", "STA2", "C"), kind=AssetKind.NOVATEL, local_path=Path("/b")))
        ws = self._ws(catalog=cat)
        assert ws.list_stations("NET1") == ["STA1"]
        assert ws.list_stations("NET2") == ["STA2"]

    def test_injected_ports_are_used(self):
        from earthscope_sfg_workflows.data_mgmt.adapters.memory import InMemoryAssetStore

        cat = InMemoryAssetStore()
        ws = self._ws(catalog=cat)
        assert ws.catalog is cat

