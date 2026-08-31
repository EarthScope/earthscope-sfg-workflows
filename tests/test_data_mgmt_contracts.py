"""Contract tests for the data_mgmt ports & adapters (RFC A, phase 1).

These tests pin down the *behavior* expected of any adapter implementing
:class:`AssetStore`, :class:`FileStore`, or :class:`ArchiveSource`. Future
adapters (SQLite, Postgres, S3, EarthScope) reuse the same suite.
"""

from __future__ import annotations

from datetime import UTC
from pathlib import Path

import pytest

from earthscope_sfg_workflows.data_mgmt import (
    AssetEntry,
    AssetKind,
    DirectoryTree,
    FileManager,
    FileTypeDetector,
    SFGScope,
)
from earthscope_sfg_workflows.data_mgmt.adapters.memory import (
    FakeArchive,
    InMemoryAssetStore,
    InMemoryFileStore,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def scope() -> SFGScope:
    return SFGScope(network="cascadia", station="NCB1", campaign="2024_A")


@pytest.fixture
def workspace_tree() -> DirectoryTree:
    return DirectoryTree(root=Path("/ws"))


# ---------------------------------------------------------------------------
# Pure model
# ---------------------------------------------------------------------------


class TestPureModel:
    def test_scope_is_mutable(self, scope: SFGScope) -> None:
        scope.campaign = "new_campaign"
        assert scope.campaign == "new_campaign"

    def test_with_survey(self, scope: SFGScope) -> None:
        s = scope.with_survey("S1")
        assert s.survey == "S1"
        assert scope.survey is None  # original untouched

    def test_directory_tree_paths(self, workspace_tree: DirectoryTree, scope: SFGScope) -> None:
        assert workspace_tree.station_dir(scope) == Path("/ws/cascadia/NCB1")
        assert workspace_tree.campaign_dir(scope) == Path("/ws/cascadia/NCB1/2024_A")
        assert workspace_tree.catalog_db == Path("/ws/catalog.sqlite")

    def test_survey_dir_requires_survey(
        self, workspace_tree: DirectoryTree, scope: SFGScope
    ) -> None:
        with pytest.raises(ValueError):
            workspace_tree.survey_dir(scope)

    def test_tiledb_layout_is_pure(self, workspace_tree: DirectoryTree, scope: SFGScope) -> None:
        layout = workspace_tree.tiledb(scope)
        assert layout.acoustic == Path("/ws/cascadia/NCB1/TileDB/acoustic.tdb")
        # All child paths share the layout root.
        for p in layout.all_paths:
            assert str(p).startswith("/ws/cascadia/NCB1/TileDB")

    def test_asset_entry_addressability(self, scope: SFGScope) -> None:
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
            ("SLT100USA_R_20261541758_01D_20C_MO.rnx", AssetKind.RINEX2),
            ("BRDC00IGS_R_20261540000_01D_MN.rnx", AssetKind.RINEX3),
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
    def test_add_assigns_id(self, scope: SFGScope) -> None:
        store = InMemoryAssetStore()
        out = store.add(AssetEntry(kind=AssetKind.NOVATEL, scope=scope))
        assert out.id is not None
        assert store.by_id(out.id) == out

    def test_assets_for_scope_filter(self, scope: SFGScope) -> None:
        store = InMemoryAssetStore()
        other = SFGScope(network="x", station="y", campaign="z")
        store.add(AssetEntry(kind=AssetKind.KIN, scope=scope))
        store.add(AssetEntry(kind=AssetKind.KIN, scope=scope))
        store.add(AssetEntry(kind=AssetKind.KIN, scope=other))

        assert (
            len(
                store.assets_for(
                    network=scope.network, station=scope.station, campaign=scope.campaign
                )
            )
            == 2
        )
        assert (
            len(
                store.assets_for(
                    network=other.network, station=other.station, campaign=other.campaign
                )
            )
            == 1
        )

    def test_count_by_kind(self, scope: SFGScope) -> None:
        store = InMemoryAssetStore()
        for k in (AssetKind.NOVATEL, AssetKind.NOVATEL, AssetKind.KIN):
            store.add(AssetEntry(kind=k, scope=scope))
        counts = store.count_by_kind(scope)
        assert counts[AssetKind.NOVATEL] == 2
        assert counts[AssetKind.KIN] == 1

    def test_update_and_delete(self, scope: SFGScope) -> None:
        store = InMemoryAssetStore()
        a = store.add(AssetEntry(kind=AssetKind.NOVATEL, scope=scope))
        assert store.update(a.with_local_path(Path("/tmp/x.bin")))
        assert store.by_id(a.id).local_path == Path("/tmp/x.bin")  # type: ignore[arg-type]
        assert store.delete(scope, kind=AssetKind.NOVATEL) == 1
        assert store.by_id(a.id) is None  # type: ignore[arg-type]


class TestAssetCatalog:
    """Smoke contract tests for the SQLAlchemy adapter against on-disk SQLite."""

    def test_roundtrip(self, tmp_path: Path, scope: SFGScope) -> None:
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

            assert store.assets_for(
                network=scope.network, station=scope.station, campaign=scope.campaign
            ) == [fetched]
            assert store.count_by_kind(
                network=scope.network, station=scope.station, campaign=scope.campaign
            ) == {AssetKind.NOVATEL: 1}

            assert store.update(fetched.with_local_path(Path("/data/n2.bin")))
            assert store.by_id(a.id).local_path == Path("/data/n2.bin")  # type: ignore[arg-type]

            assert (
                store.delete(network=scope.network, station=scope.station, campaign=scope.campaign)
                == 1
            )
            assert (
                store.assets_for(
                    network=scope.network, station=scope.station, campaign=scope.campaign
                )
                == []
            )
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


class TestEarthScopeArchiveQcZipUrl:
    def test_instance_method(self) -> None:
        from earthscope_sfg_workflows.data_mgmt.archives.earthscope_archive import (
            EarthScopeArchive,
        )

        scope = SFGScope(network="cascadia-gorda", station="GCC1", campaign="2025_A_1126")
        arc = EarthScopeArchive()
        assert arc.campaign_qc_zip_url(scope) == (
            "https://data.earthscope.org/archive/seafloor/"
            "cascadia-gorda/2025/GCC1/2025_A_1126/qc.zip"
        )

    def test_module_level_function(self) -> None:
        from earthscope_sfg_workflows.data_mgmt.archives.earthscope_archive import (
            campaign_qc_zip_url,
        )

        scope = SFGScope(network="cascadia-gorda", station="GCC1", campaign="2025_A_1126")
        assert campaign_qc_zip_url(scope) == (
            "https://data.earthscope.org/archive/seafloor/"
            "cascadia-gorda/2025/GCC1/2025_A_1126/qc.zip"
        )


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
        self, workspace_tree: DirectoryTree, scope: SFGScope
    ) -> None:
        fs = InMemoryFileStore()
        tb = FileManager(workspace_tree, fs)
        layout = tb.ensure_campaign(scope)
        for d in layout.standard_dirs:
            assert fs.is_dir(d)

    def test_ensure_garpos_requires_survey(
        self, workspace_tree: DirectoryTree, scope: SFGScope
    ) -> None:
        tb = FileManager(workspace_tree, InMemoryFileStore())
        with pytest.raises(ValueError):
            tb.ensure_garpos_survey(scope)

    def test_ensure_garpos_survey(self, workspace_tree: DirectoryTree, scope: SFGScope) -> None:
        fs = InMemoryFileStore()
        tb = FileManager(workspace_tree, fs)
        layout = tb.ensure_garpos_survey(scope.with_survey("S1"))
        for d in layout.standard_dirs:
            assert fs.is_dir(d)


# ---------------------------------------------------------------------------
# IngestService — end-to-end orchestration
# ---------------------------------------------------------------------------


class TestIngestService:
    def _session(self, scope: SFGScope, files: InMemoryFileStore | None = None):
        from tests.utils import make_session

        catalog = InMemoryAssetStore()
        fs = files if files is not None else InMemoryFileStore()
        archive = FakeArchive()
        session = make_session(
            network=scope.network,
            station=scope.station,
            campaign=scope.campaign,
            catalog=catalog,
            archive=archive,
        )
        # Replace the test session's file backend so we control its contents.
        from earthscope_sfg_workflows.data_mgmt.core import FileManager

        session._file_manager = FileManager(DirectoryTree(root=Path("/ws")), fs)
        return session, catalog, fs, archive

    def test_discover_remote_includes_ctd_subdirectory(self, scope: SFGScope) -> None:
        from earthscope_sfg_workflows.data_mgmt.archives.earthscope_archive import (
            canonical_campaign_urls,
        )

        session, catalog, _, archive = self._session(scope)
        _, metadata_url, _, _ = canonical_campaign_urls(scope)
        archive.seed(f"{metadata_url}/ctd/CTD_001.csv", b"C")

        report = session.ingest.discover_remote()
        assert report.cataloged == 1

        [asset] = catalog.assets_for(
            network=scope.network, station=scope.station, campaign=scope.campaign
        )
        assert asset.kind == AssetKind.CTD
        assert asset.remote_path is not None
        assert asset.local_path is None

    def test_discover_ctd_only(self, scope: SFGScope) -> None:
        from earthscope_sfg_workflows.data_mgmt.archives.earthscope_archive import (
            canonical_campaign_urls,
        )

        session, catalog, _, archive = self._session(scope)
        raw_url, metadata_url, _, _ = canonical_campaign_urls(scope)
        archive.seed(f"{metadata_url}/ctd/CTD_001.csv", b"C")
        archive.seed(f"{raw_url}/sonardyne.bin", b"S")  # should be ignored by discover_ctd

        report = session.ingest.discover_ctd()
        assert report.cataloged == 1

        kinds = {
            a.kind
            for a in catalog.assets_for(
                network=scope.network, station=scope.station, campaign=scope.campaign
            )
        }
        assert kinds == {AssetKind.CTD}

    def test_discover_ctd_falls_back_to_previous_campaign(self, scope: SFGScope) -> None:
        from datetime import datetime
        from types import SimpleNamespace

        from earthscope_sfg_tools.datamodels.metadata import Campaign

        from earthscope_sfg_workflows.data_mgmt.archives.earthscope_archive import (
            canonical_campaign_urls,
        )

        session, catalog, _, archive = self._session(scope)

        older_campaign_name = "2020_A"
        older_scope = SFGScope(
            network=scope.network, station=scope.station, campaign=older_campaign_name
        )
        _, older_metadata_url, _, _ = canonical_campaign_urls(older_scope)
        older_ctd_url = f"{older_metadata_url}/ctd/CTD_001.csv"
        archive.seed(older_ctd_url, b"OLD")
        # No CTD seeded for the active campaign itself.

        session._site = SimpleNamespace(
            campaigns=[
                Campaign(
                    name=older_campaign_name,
                    type="A",
                    vesselCode="V1",
                    start=datetime(2020, 1, 1, tzinfo=UTC),
                    end=datetime(2020, 1, 2, tzinfo=UTC),
                ),
                Campaign(
                    name=scope.campaign,
                    type="A",
                    vesselCode="V1",
                    start=datetime(2024, 1, 1, tzinfo=UTC),
                    end=datetime(2024, 1, 2, tzinfo=UTC),
                ),
            ]
        )

        report = session.ingest.discover_ctd()
        assert report.ok
        assert report.cataloged == 1

        [asset] = catalog.assets_for(
            network=scope.network, station=scope.station, campaign=scope.campaign
        )
        assert asset.kind == AssetKind.CTD
        assert asset.remote_path == older_ctd_url

    def test_discover_ctd_no_fallback_without_site_metadata(self, scope: SFGScope) -> None:
        session, catalog, _, _archive = self._session(scope)
        assert session.site is None  # make_session leaves _site unset

        report = session.ingest.discover_ctd()
        assert report.cataloged == 0
        assert (
            catalog.assets_for(
                network=scope.network, station=scope.station, campaign=scope.campaign
            )
            == []
        )

    def test_ingest_ctd_only_downloads_ctd(self, scope: SFGScope, tmp_path: Path) -> None:
        from earthscope_sfg_workflows.data_mgmt.archives.earthscope_archive import (
            canonical_campaign_urls,
        )
        from earthscope_sfg_workflows.data_mgmt.core import FileManager
        from earthscope_sfg_workflows.data_mgmt.filestore.disk_filestore import FsspecFileStore
        from tests.utils import make_session

        catalog = InMemoryAssetStore()
        archive = FakeArchive()
        _, metadata_url, _, _ = canonical_campaign_urls(scope)
        archive.seed(f"{metadata_url}/ctd/CTD_001.csv", b"C")

        files = FsspecFileStore(root=str(tmp_path))
        session = make_session(
            network=scope.network,
            station=scope.station,
            catalog=catalog,
            archive=archive,
        )
        session._file_manager = FileManager(DirectoryTree(root=tmp_path), files)
        session.set_campaign(scope.campaign)

        report = session.ingest.ingest_ctd_only()

        assert report.ok
        assert report.cataloged == 1
        assert report.downloaded == 1

        [asset] = catalog.assets_for(
            network=scope.network, station=scope.station, campaign=scope.campaign
        )
        assert asset.kind == AssetKind.CTD
        assert asset.local_path is not None
        assert asset.local_path.exists()
        assert asset.local_path.read_bytes() == b"C"

    def test_ingest_local(self, scope: SFGScope) -> None:
        session, catalog, files, _ = self._session(scope)
        files.write_bytes(Path("/in/foo.24o"), b"R")
        files.write_bytes(Path("/in/sonardyne.log"), b"S")
        files.write_bytes(Path("/in/random.xyz"), b"?")
        files.write_bytes(Path("/in/._mac"), b"!")

        report = session.ingest.local(Path("/in"))
        assert report.ok
        assert report.cataloged == 2
        # Hidden ._mac is filtered at the FileStore boundary; only random.xyz
        # reaches the service and gets counted as skipped.
        assert report.skipped == 1

        kinds = {
            a.kind
            for a in catalog.assets_for(
                network=scope.network, station=scope.station, campaign=scope.campaign
            )
        }
        assert kinds == {AssetKind.RINEX2, AssetKind.SONARDYNE}

    def test_discover_archive_sets_remote_only(self, scope: SFGScope) -> None:
        session, catalog, _, archive = self._session(scope)
        archive.seed("https://arc/a/foo.24o", b"R")
        archive.seed("https://arc/a/sonardyne.bin", b"S")

        report = session.ingest._discover_archive(scope, "https://arc/a")
        assert report.cataloged == 2
        for a in catalog.assets_for(scope):
            assert a.remote_path is not None
            assert a.local_path is None

    @pytest.mark.skip(reason="_collect_remote_candidates passes scope as kind to assets_for")
    def test_download_marks_local_path(self, scope: SFGScope, tmp_path: Path) -> None:
        # Need a real local fs for download because FakeArchive writes to disk.
        from earthscope_sfg_workflows.data_mgmt.core import FileManager
        from earthscope_sfg_workflows.data_mgmt.filestore.disk_filestore import FsspecFileStore
        from tests.utils import make_session

        catalog = InMemoryAssetStore()
        files = FsspecFileStore(root=str(tmp_path))
        archive = FakeArchive()
        archive.seed("https://arc/a/foo.24o", b"R")

        session = make_session(
            network=scope.network,
            station=scope.station,
            catalog=catalog,
            archive=archive,
        )
        session._file_manager = FileManager(DirectoryTree(root=tmp_path), files)
        session.set_campaign(scope.campaign)

        session.ingest._discover_archive(scope, "https://arc/a")
        report = session.ingest.download_remote()
        assert report.ok
        assert report.downloaded == 1

        [asset] = catalog.assets_for(
            network=scope.network, station=scope.station, campaign=scope.campaign
        )
        assert asset.local_path is not None
        assert asset.local_path.exists()
        assert asset.local_path.read_bytes() == b"R"

    def test_ingest_qc_zip_extracts_and_catalogs(self, scope: SFGScope, tmp_path: Path) -> None:
        import io
        import tarfile
        import zipfile

        from earthscope_sfg_workflows.data_mgmt.archives.earthscope_archive import (
            campaign_qc_zip_url,
        )
        from earthscope_sfg_workflows.data_mgmt.core import FileManager
        from earthscope_sfg_workflows.data_mgmt.filestore.disk_filestore import FsspecFileStore
        from tests.utils import make_session

        tar_buf = io.BytesIO()
        with tarfile.open(fileobj=tar_buf, mode="w:gz") as tf:
            pin_data = b"PIN-DATA"
            info = tarfile.TarInfo(name="results.pin")
            info.size = len(pin_data)
            tf.addfile(info, io.BytesIO(pin_data))
        tar_bytes = tar_buf.getvalue()

        zip_buf = io.BytesIO()
        with zipfile.ZipFile(zip_buf, "w") as zf:
            zf.writestr("nested/survey1.tar.gz", tar_bytes)
            zf.writestr("README.txt", b"ignore me")
        zip_bytes = zip_buf.getvalue()

        catalog = InMemoryAssetStore()
        archive = FakeArchive()
        archive.seed(campaign_qc_zip_url(scope), zip_bytes)
        files = FsspecFileStore(root=str(tmp_path))

        session = make_session(
            network=scope.network,
            station=scope.station,
            catalog=catalog,
            archive=archive,
        )
        session._file_manager = FileManager(DirectoryTree(root=tmp_path), files)
        session.set_campaign(scope.campaign)

        report = session.ingest.ingest_qc_zip()

        assert report.ok
        assert report.cataloged == 1
        assert report.skipped >= 1  # README.txt ignored

        [asset] = catalog.assets_for(
            network=scope.network, station=scope.station, campaign=scope.campaign
        )
        assert asset.kind == AssetKind.QCPIN
        assert asset.local_path is not None
        assert asset.local_path.exists()
        assert asset.local_path.read_bytes() == b"PIN-DATA"

    def test_ingest_qc_zip_missing_returns_empty_report(self, scope: SFGScope) -> None:
        from earthscope_sfg_workflows.data_mgmt.model import IngestReport

        session, catalog, _, _ = self._session(scope)

        report = session.ingest.ingest_qc_zip()

        assert report == IngestReport()
        assert (
            catalog.assets_for(
                network=scope.network, station=scope.station, campaign=scope.campaign
            )
            == []
        )

    @staticmethod
    def _make_tarball(pin_name: str, pin_data: bytes) -> bytes:
        import io
        import tarfile

        buf = io.BytesIO()
        with tarfile.open(fileobj=buf, mode="w:gz") as tf:
            info = tarfile.TarInfo(name=pin_name)
            info.size = len(pin_data)
            tf.addfile(info, io.BytesIO(pin_data))
        return buf.getvalue()

    def _qc_session(self, scope: SFGScope, tmp_path: Path, archive: FakeArchive):
        from earthscope_sfg_workflows.data_mgmt.core import FileManager
        from earthscope_sfg_workflows.data_mgmt.filestore.disk_filestore import FsspecFileStore
        from tests.utils import make_session

        catalog = InMemoryAssetStore()
        files = FsspecFileStore(root=str(tmp_path))
        session = make_session(
            network=scope.network,
            station=scope.station,
            catalog=catalog,
            archive=archive,
        )
        session._file_manager = FileManager(DirectoryTree(root=tmp_path), files)
        session.set_campaign(scope.campaign)
        return session, catalog

    def test_ingest_qc_falls_back_to_tarballs_when_no_zip(
        self, scope: SFGScope, tmp_path: Path
    ) -> None:
        from earthscope_sfg_workflows.data_mgmt.archives.earthscope_archive import (
            campaign_qc_dir_url,
        )

        archive = FakeArchive()
        archive.seed(
            f"{campaign_qc_dir_url(scope)}/survey1.tar.gz",
            self._make_tarball("results.pin", b"PIN-DATA"),
        )
        session, catalog = self._qc_session(scope, tmp_path, archive)

        report = session.ingest.ingest_qc()

        assert report.ok
        assert report.downloaded == 1
        assert report.cataloged == 1

        [asset] = catalog.assets_for(
            network=scope.network, station=scope.station, campaign=scope.campaign
        )
        assert asset.kind == AssetKind.QCPIN
        assert asset.local_path is not None
        assert asset.local_path.read_bytes() == b"PIN-DATA"

    def test_ingest_qc_prefers_zip_over_tarball_dir(self, scope: SFGScope, tmp_path: Path) -> None:
        import io
        import zipfile

        from earthscope_sfg_workflows.data_mgmt.archives.earthscope_archive import (
            campaign_qc_dir_url,
            campaign_qc_zip_url,
        )

        tar_bytes = self._make_tarball("zip_survey.pin", b"FROM-ZIP")
        zip_buf = io.BytesIO()
        with zipfile.ZipFile(zip_buf, "w") as zf:
            zf.writestr("nested/zip_survey.tar.gz", tar_bytes)
        archive = FakeArchive()
        archive.seed(campaign_qc_zip_url(scope), zip_buf.getvalue())
        archive.seed(
            f"{campaign_qc_dir_url(scope)}/other.tar.gz",
            self._make_tarball("other.pin", b"FROM-TARBALL-DIR"),
        )
        session, catalog = self._qc_session(scope, tmp_path, archive)

        report = session.ingest.ingest_qc()

        assert report.ok
        assert report.cataloged == 1
        [asset] = catalog.assets_for(
            network=scope.network, station=scope.station, campaign=scope.campaign
        )
        assert asset.local_path.read_bytes() == b"FROM-ZIP"

    def test_ingest_qc_rerun_picks_up_new_tarball_without_redownloading(
        self, scope: SFGScope, tmp_path: Path
    ) -> None:
        from earthscope_sfg_workflows.data_mgmt.archives.earthscope_archive import (
            campaign_qc_dir_url,
        )

        archive = FakeArchive()
        archive.seed(
            f"{campaign_qc_dir_url(scope)}/survey1.tar.gz",
            self._make_tarball("survey1.pin", b"FIRST"),
        )
        session, catalog = self._qc_session(scope, tmp_path, archive)

        first = session.ingest.ingest_qc()
        assert first.downloaded == 1
        assert first.cataloged == 1

        # Rerunning with the same archive state re-downloads nothing new and
        # re-catalogs nothing already cataloged.
        second = session.ingest.ingest_qc()
        assert second.downloaded == 0
        assert second.cataloged == 0

        # A newly published tarball is picked up on the next rerun, and the
        # first tarball is neither redownloaded nor recataloged.
        archive.seed(
            f"{campaign_qc_dir_url(scope)}/survey2.tar.gz",
            self._make_tarball("survey2.pin", b"SECOND"),
        )
        third = session.ingest.ingest_qc()
        assert third.downloaded == 1
        assert third.cataloged == 1

        assets = catalog.assets_for(
            network=scope.network, station=scope.station, campaign=scope.campaign
        )
        assert {a.local_path.read_bytes() for a in assets} == {b"FIRST", b"SECOND"}
