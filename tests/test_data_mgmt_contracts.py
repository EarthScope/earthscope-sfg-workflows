"""Contract tests for the data_mgmt ports & adapters (RFC A, phase 1).

These tests pin down the *behavior* expected of any adapter implementing
:class:`AssetStore`, :class:`FileStore`, or :class:`ArchiveSource`. Future
adapters (SQLite, Postgres, S3, EarthScope) reuse the same suite.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from earthscope_sfg_workflows.data_mgmt import (
    AssetEntry,
    AssetKind,
    CampaignScope,
    DirectoryTree,
    FileManager,
    FileTypeDetector,
    Ingestor,
)
from earthscope_sfg_workflows.data_mgmt.adapters.test_adapters import (
    FakeArchive,
    InMemoryAssetStore,
    InMemoryFileStore,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def scope() -> CampaignScope:
    return CampaignScope(network="cascadia", station="NCB1", campaign="2024_A")


@pytest.fixture
def workspace_tree() -> DirectoryTree:
    return DirectoryTree(root=Path("/ws"))


# ---------------------------------------------------------------------------
# Pure model
# ---------------------------------------------------------------------------


class TestPureModel:
    def test_scope_is_frozen(self, scope: CampaignScope) -> None:
        with pytest.raises(Exception):
            scope.network = "other"  # type: ignore[misc]

    def test_with_survey(self, scope: CampaignScope) -> None:
        s = scope.with_survey("S1")
        assert s.survey == "S1"
        assert scope.survey is None  # original untouched

    def test_directory_tree_paths(
        self, workspace_tree: DirectoryTree, scope: CampaignScope
    ) -> None:
        assert workspace_tree.station_dir(scope) == Path("/ws/cascadia/NCB1")
        assert workspace_tree.campaign_dir(scope) == Path("/ws/cascadia/NCB1/2024_A")
        assert workspace_tree.catalog_db == Path("/ws/catalog.sqlite")

    def test_survey_dir_requires_survey(
        self, workspace_tree: DirectoryTree, scope: CampaignScope
    ) -> None:
        with pytest.raises(ValueError):
            workspace_tree.survey_dir(scope)

    def test_tiledb_layout_is_pure(
        self, workspace_tree: DirectoryTree, scope: CampaignScope
    ) -> None:
        layout = workspace_tree.tiledb(scope)
        assert layout.acoustic == Path("/ws/cascadia/NCB1/TileDB/acoustic.tdb")
        # All child paths share the layout root.
        for p in layout.all_paths:
            assert str(p).startswith("/ws/cascadia/NCB1/TileDB")

    def test_asset_entry_addressability(self, scope: CampaignScope) -> None:
        a = AssetEntry(kind=AssetKind.NOVATEL, scope=scope)
        assert not a.is_addressable()
        b = a.with_local_path(Path("/tmp/n.bin"))
        assert b.is_addressable()
        assert a.local_path is None  # immutability


# ---------------------------------------------------------------------------
# FileTypeDetector
# ---------------------------------------------------------------------------


class TestFileTypeDetector:
    @pytest.mark.parametrize(
        "name,expected",
        [
            ("foo.24o", AssetKind.RINEX2),
            ("sonardyne_log.txt", AssetKind.SONARDYNE),
            ("NOV770_001.raw", AssetKind.NOVATEL770),
            ("DFOP00.raw", AssetKind.DFOP00),
            ("novatelpin_007.bin", AssetKind.NOVATELPIN),
            ("novatel_log.bin", AssetKind.NOVATEL),
            ("results.pin", AssetKind.QCPIN),
            ("kin_2024.dat", AssetKind.KIN),
            ("CTD_001.csv", AssetKind.CTD),
            ("seabird.cnv", AssetKind.SEABIRD),
        ],
    )
    def test_detect(self, name: str, expected: AssetKind) -> None:
        assert FileTypeDetector().detect(name) == expected

    def test_unknown_returns_none(self) -> None:
        assert FileTypeDetector().detect("random_data.xyz") is None


# ---------------------------------------------------------------------------
# AssetStore contract
# ---------------------------------------------------------------------------


class TestInMemoryAssetStore:
    def test_add_assigns_id(self, scope: CampaignScope) -> None:
        store = InMemoryAssetStore()
        out = store.add(AssetEntry(kind=AssetKind.NOVATEL, scope=scope))
        assert out.id is not None
        assert store.by_id(out.id) == out

    def test_assets_for_scope_filter(self, scope: CampaignScope) -> None:
        store = InMemoryAssetStore()
        other = CampaignScope(network="x", station="y", campaign="z")
        store.add(AssetEntry(kind=AssetKind.KIN, scope=scope))
        store.add(AssetEntry(kind=AssetKind.KIN, scope=scope))
        store.add(AssetEntry(kind=AssetKind.KIN, scope=other))

        assert len(store.assets_for(scope)) == 2
        assert len(store.assets_for(other)) == 1

    def test_count_by_kind(self, scope: CampaignScope) -> None:
        store = InMemoryAssetStore()
        for k in (AssetKind.NOVATEL, AssetKind.NOVATEL, AssetKind.KIN):
            store.add(AssetEntry(kind=k, scope=scope))
        counts = store.count_by_kind(scope)
        assert counts[AssetKind.NOVATEL] == 2
        assert counts[AssetKind.KIN] == 1

    def test_update_and_delete(self, scope: CampaignScope) -> None:
        store = InMemoryAssetStore()
        a = store.add(AssetEntry(kind=AssetKind.NOVATEL, scope=scope))
        assert store.update(a.with_local_path(Path("/tmp/x.bin")))
        assert store.by_id(a.id).local_path == Path("/tmp/x.bin")  # type: ignore[arg-type]
        assert store.delete(scope, kind=AssetKind.NOVATEL) == 1
        assert store.by_id(a.id) is None  # type: ignore[arg-type]


class TestAssetCatalog:
    """Smoke contract tests for the SQLAlchemy adapter against on-disk SQLite."""

    def test_roundtrip(self, tmp_path: Path, scope: CampaignScope) -> None:
        from earthscope_sfg_workflows.data_mgmt.adapters import AssetCatalog

        store = AssetCatalog.sqlite(tmp_path / "catalog.sqlite")
        try:
            a = store.add(
                AssetEntry(
                    kind=AssetKind.NOVATEL,
                    scope=scope,
                    local_path=Path("/data/n.bin"),
                )
            )
            assert a.id is not None
            fetched = store.by_id(a.id)
            assert fetched is not None
            assert fetched.kind == AssetKind.NOVATEL
            assert fetched.local_path == Path("/data/n.bin")

            assert store.assets_for(scope) == [fetched]
            assert store.count_by_kind(scope) == {AssetKind.NOVATEL: 1}

            assert store.update(fetched.with_local_path(Path("/data/n2.bin")))
            assert store.by_id(a.id).local_path == Path("/data/n2.bin")  # type: ignore[arg-type]

            assert store.delete(scope) == 1
            assert store.assets_for(scope) == []
        finally:
            store.close()


# ---------------------------------------------------------------------------
# FileStore contract
# ---------------------------------------------------------------------------


class TestInMemoryFileStore:
    def test_mkdir_and_exists(self) -> None:
        fs = InMemoryFileStore()
        fs.mkdir(Path("/a/b/c"))
        assert fs.is_dir(Path("/a"))
        assert fs.is_dir(Path("/a/b/c"))
        assert not fs.is_file(Path("/a/b/c"))

    def test_write_then_read(self) -> None:
        fs = InMemoryFileStore()
        fs.write_bytes(Path("/a/b.txt"), b"hello")
        assert fs.is_file(Path("/a/b.txt"))
        assert fs.read_bytes(Path("/a/b.txt")) == b"hello"
        assert fs.get_size(Path("/a/b.txt")) == 5

    def test_list_files_filters_hidden(self) -> None:
        fs = InMemoryFileStore()
        fs.write_bytes(Path("/d/keep.bin"), b"x")
        fs.write_bytes(Path("/d/._junk"), b"y")
        listed = fs.list_files(Path("/d"))
        assert [fi.path.name for fi in listed] == ["keep.bin"]


# ---------------------------------------------------------------------------
# ArchiveSource contract
# ---------------------------------------------------------------------------


class TestFakeArchive:
    def test_list_and_download(self, tmp_path: Path) -> None:
        arc = FakeArchive(
            {
                "https://x/y/a.24o": b"AAA",
                "https://x/y/b.24o": b"BBB",
                "https://x/y/sub/c.24o": b"CCC",
            }
        )
        listing = arc.list_files("https://x/y")
        assert {af.filename for af in listing} == {"a.24o", "b.24o"}

        dest = tmp_path / "a.24o"
        arc.download_file("https://x/y/a.24o", dest)
        assert dest.read_bytes() == b"AAA"

    def test_download_missing_raises(self, tmp_path: Path) -> None:
        from earthscope_sfg_workflows.data_mgmt import ArchiveNotFoundError

        arc = FakeArchive()
        with pytest.raises(ArchiveNotFoundError):
            arc.download_file("https://x/missing", tmp_path / "x")


# ---------------------------------------------------------------------------
# FileManager
# ---------------------------------------------------------------------------


class TestFileManager:
    def test_ensure_campaign_creates_standard_dirs(
        self, workspace_tree: DirectoryTree, scope: CampaignScope
    ) -> None:
        fs = InMemoryFileStore()
        tb = FileManager(workspace_tree, fs)
        layout = tb.ensure_campaign(scope)
        for d in layout.standard_dirs:
            assert fs.is_dir(d)

    def test_ensure_garpos_requires_survey(
        self, workspace_tree: DirectoryTree, scope: CampaignScope
    ) -> None:
        tb = FileManager(workspace_tree, InMemoryFileStore())
        with pytest.raises(ValueError):
            tb.ensure_garpos_survey(scope)

    def test_ensure_garpos_survey(
        self, workspace_tree: DirectoryTree, scope: CampaignScope
    ) -> None:
        fs = InMemoryFileStore()
        tb = FileManager(workspace_tree, fs)
        layout = tb.ensure_garpos_survey(scope.with_survey("S1"))
        for d in layout.standard_dirs:
            assert fs.is_dir(d)


# ---------------------------------------------------------------------------
# Ingestor — end-to-end orchestration
# ---------------------------------------------------------------------------


class TestIngestor:
    def _ingestor(
        self, scope: CampaignScope, tree: DirectoryTree
    ) -> tuple[Ingestor, InMemoryAssetStore, InMemoryFileStore, FakeArchive]:
        catalog = InMemoryAssetStore()
        files = InMemoryFileStore()
        archive = FakeArchive()
        ing = Ingestor(
            catalog=catalog,
            file_manager=FileManager(tree, files),
            archive=archive,
            detector=FileTypeDetector(),
        )
        return ing, catalog, files, archive

    def test_ingest_local(self, scope: CampaignScope, workspace_tree: DirectoryTree) -> None:
        ing, catalog, files, _ = self._ingestor(scope, workspace_tree)
        files.write_bytes(Path("/in/foo.24o"), b"R")
        files.write_bytes(Path("/in/sonardyne.log"), b"S")
        files.write_bytes(Path("/in/random.xyz"), b"?")
        files.write_bytes(Path("/in/._mac"), b"!")

        report = ing.ingest_local(scope, Path("/in"))
        assert report.ok
        assert report.cataloged == 2
        # Hidden ._mac is filtered at the FileStore boundary; only random.xyz
        # reaches the Ingestor and gets counted as skipped.
        assert report.skipped == 1

        kinds = {a.kind for a in catalog.assets_for(scope)}
        assert kinds == {AssetKind.RINEX2, AssetKind.SONARDYNE}

    def test_discover_archive_sets_remote_only(
        self, scope: CampaignScope, workspace_tree: DirectoryTree
    ) -> None:
        ing, catalog, _, archive = self._ingestor(scope, workspace_tree)
        archive.seed("https://arc/a/foo.24o", b"R")
        archive.seed("https://arc/a/sonardyne.bin", b"S")

        report = ing.discover_archive(scope, "https://arc/a")
        assert report.cataloged == 2
        for a in catalog.assets_for(scope):
            assert a.remote_path is not None
            assert a.local_path is None

    def test_download_marks_local_path(
        self, scope: CampaignScope, workspace_tree: DirectoryTree, tmp_path: Path
    ) -> None:
        # Need a real local fs for download because FakeArchive writes to disk.
        from earthscope_sfg_workflows.data_mgmt.adapters.disk_filestore import LocalFileStore

        catalog = InMemoryAssetStore()
        files = LocalFileStore(root=tmp_path)
        archive = FakeArchive()
        archive.seed("https://arc/a/foo.24o", b"R")
        tree = DirectoryTree(root=tmp_path)

        ing = Ingestor(
            catalog=catalog,
            file_manager=FileManager(tree, files),
            archive=archive,
            detector=FileTypeDetector(),
        )
        ing.discover_archive(scope, "https://arc/a")
        report = ing.download(scope)
        assert report.ok
        assert report.downloaded == 1

        [asset] = catalog.assets_for(scope)
        assert asset.local_path is not None
        assert asset.local_path.exists()
        assert asset.local_path.read_bytes() == b"R"
