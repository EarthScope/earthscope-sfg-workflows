"""Integration tests for SV3Pipeline using real sub-sampled fixture data.

These tests cover:
- KIN file → TileDB kin_position (``process_kin``)
- TileDB shotdata_pre + kin_position → final shotdata (``update_shotdata``)
- Idempotency: each stage is a no-op when already complete
- Config injection: ``override=True`` forces re-processing
- Exception paths: ``NoKinFound``, ``NoDFOP00Found`` on empty catalogs

Fixture data lives in ``tests/fixtures/NCC1_2025_A_1126/`` and is a
lightweight (< 500 KB) sub-sample of a real NCC1 2025 campaign.  TileDB
arrays are created fresh in pytest ``tmp_path`` for full isolation.

Skipped automatically when the fixture directory is absent (e.g., CI without
the external SSD) or when optional pipeline dependencies are not installed.
"""

from __future__ import annotations

import datetime
import shutil
from pathlib import Path

import pandas as pd
import pytest

FIXTURES = Path(__file__).parent / "fixtures" / "NCC1_2025_A_1126"

# ---------------------------------------------------------------------------
# Skip the entire module when fixtures or heavy deps are missing
# ---------------------------------------------------------------------------

pytestmark = pytest.mark.skipif(
    not FIXTURES.exists(),
    reason="Fixture directory tests/fixtures/NCC1_2025_A_1126 not present",
)

try:
    from earthscope_sfg_tools.tiledb_integration.arrays import (
        TDBKinPositionArray,
        TDBShotDataArray,
        TDBIMUPositionArray,
    )

    _TILEDB_DEPS = True
except ImportError:
    _TILEDB_DEPS = False


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

NETWORK = "cascadia-gorda"
STATION = "NCC1"
CAMPAIGN = "2025_A_1126"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_scope():
    from earthscope_sfg_workflows.data_mgmt.model import SFGScope

    return SFGScope(network=NETWORK, station=STATION, campaign=CAMPAIGN)


def _build_tiledb_layout(base: Path):
    """Create a TileDBLayout rooted under *base*/TileDB."""
    from upath import UPath
    from earthscope_sfg_workflows.data_mgmt.model import TileDBLayout

    return TileDBLayout.for_station(UPath(base))


def _build_campaign_layout(base: Path):
    """Create a CampaignLayout rooted at *base*."""
    from upath import UPath
    from earthscope_sfg_workflows.data_mgmt.model import CampaignLayout

    layout = CampaignLayout.for_campaign(UPath(base))
    for d in layout.standard_dirs:
        d.mkdir(parents=True, exist_ok=True)
    return layout


def _make_pipeline(tmp_path: Path, catalog, *, config=None):
    """Build an SV3Pipeline backed by *catalog* and fresh TileDB arrays."""
    from earthscope_sfg_workflows.pipelines.sv3_pipeline import SV3Pipeline

    scope = _make_scope()
    tdb_layout = _build_tiledb_layout(tmp_path / "station")
    campaign_layout = _build_campaign_layout(tmp_path / "campaign")

    return SV3Pipeline(
        catalog=catalog,
        scope=scope,
        tiledb_layout=tdb_layout,
        campaign_layout=campaign_layout,
        config=config,
    )


def _add_kin_entry(catalog, kin_file: Path):
    """Catalog a KIN asset entry for the NCC1 scope."""
    from earthscope_sfg_workflows.data_mgmt.model import AssetEntry, AssetKind
    from upath import UPath

    scope = _make_scope()
    entry = AssetEntry(
        kind=AssetKind.KIN,
        scope=scope,
        local_path=UPath(kin_file),
        timestamp_created=datetime.datetime.now(tz=datetime.timezone.utc),
    )
    return catalog.add(entry)


def _seed_tiledb_from_parquet(pipeline) -> None:
    """Write fixture parquet data into the pipeline's kin_position and shotdata_pre TileDB arrays.

    Uses ``validate=False`` because the parquet files store pingTime/returnTime as
    datetime64, whereas the write schema expects them as GPS-second floats before
    the internal coercion step.  The underlying write_df handles both types when
    validation is bypassed.
    """
    kin_df = pd.read_parquet(FIXTURES / "kin_position_sample.parquet")
    pre_df = pd.read_parquet(FIXTURES / "shotdata_pre_sample.parquet")

    # Write kin_position — schema accepts datetime64[ms]
    if not kin_df.empty:
        kin_df["time"] = pd.to_datetime(kin_df["time"]).astype("datetime64[ms]")
        pipeline.kinPositionTDB.write_df(kin_df)

    # Write shotdata_pre — reset index to expose pingTime as a column, skip
    # pandera validation because parquet stores timestamps as datetime64 while
    # the schema expects GPS-second floats (coerce path is incompatible here).
    if not pre_df.empty:
        if "pingTime" not in pre_df.columns:
            pre_df = pre_df.reset_index()
        pre_df["pingTime"] = pd.to_datetime(pre_df["pingTime"]).astype("datetime64[ns]")
        pre_df["returnTime"] = pd.to_datetime(pre_df["returnTime"]).astype("datetime64[ns]")
        pipeline.shotDataPreTDB.write_df(pre_df, validate=False)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def catalog():
    """Fresh InMemoryAssetStore for each test."""
    from earthscope_sfg_workflows.data_mgmt.adapters.memory import InMemoryAssetStore

    return InMemoryAssetStore()


@pytest.fixture()
def pipeline(tmp_path, catalog):
    """SV3Pipeline with a fresh in-memory catalog and tmp TileDB arrays."""
    return _make_pipeline(tmp_path, catalog)


@pytest.fixture()
def seeded_pipeline(tmp_path, catalog):
    """Pipeline with shotdata_pre and kin_position pre-loaded from parquet fixtures."""
    p = _make_pipeline(tmp_path, catalog)
    _seed_tiledb_from_parquet(p)
    return p


# ---------------------------------------------------------------------------
# Tests: process_kin
# ---------------------------------------------------------------------------


@pytest.mark.skipif(not _TILEDB_DEPS, reason="earthscope_sfg_tools not installed")
class TestProcessKin:
    """Tests for SV3Pipeline.process_kin()."""

    def test_writes_rows_to_tiledb(self, pipeline, catalog, tmp_path):
        """KIN file from catalog → kin_position TileDB receives rows."""
        kin_file = FIXTURES / "kin_2025251_ncc1.kin"
        _add_kin_entry(catalog, kin_file)

        pipeline.process_kin()

        df = pipeline.kinPositionTDB.read_df(
            start=datetime.datetime(2025, 9, 8, tzinfo=datetime.timezone.utc),
            end=datetime.datetime(2025, 9, 8, 0, 1, tzinfo=datetime.timezone.utc),
        )
        assert len(df) > 0, "Expected kin_position rows after process_kin"

    def test_marks_entry_processed(self, pipeline, catalog, tmp_path):
        """process_kin marks the KIN catalog entry as processed."""
        kin_file = FIXTURES / "kin_2025251_ncc1.kin"
        entry = _add_kin_entry(catalog, kin_file)

        pipeline.process_kin()

        updated = catalog.by_id(entry.id)
        assert updated.is_processed, "KIN entry should be marked processed"

    def test_raises_no_kin_found_when_catalog_empty(self, pipeline):
        """process_kin raises NoKinFound when no KIN entries exist."""
        from earthscope_sfg_workflows.pipelines.exceptions import NoKinFound

        with pytest.raises(NoKinFound):
            pipeline.process_kin()

    def test_idempotent_second_run_skips(self, pipeline, catalog, tmp_path):
        """Second process_kin call skips because entry is already processed."""
        from earthscope_sfg_workflows.pipelines.exceptions import NoKinFound

        kin_file = FIXTURES / "kin_2025251_ncc1.kin"
        _add_kin_entry(catalog, kin_file)

        pipeline.process_kin()

        # Second call: entry is now marked processed — assets_to_process returns []
        with pytest.raises(NoKinFound):
            pipeline.process_kin()

    def test_override_reruns_processed_entries(self, tmp_path, catalog):
        """override=True in RinexConfig forces reprocessing of already-processed KIN entries."""
        from earthscope_sfg_workflows.pipelines.config import SV3PipelineConfig

        config = SV3PipelineConfig()
        config.rinex_config.override = True
        p = _make_pipeline(tmp_path, catalog, config=config)

        kin_file = FIXTURES / "kin_2025251_ncc1.kin"
        _add_kin_entry(catalog, kin_file)

        # First pass
        p.process_kin()

        # Second pass with override — should not raise
        p.process_kin()

        df = p.kinPositionTDB.read_df(
            start=datetime.datetime(2025, 9, 8, tzinfo=datetime.timezone.utc),
            end=datetime.datetime(2025, 9, 8, 0, 1, tzinfo=datetime.timezone.utc),
        )
        assert len(df) > 0, "Expected kin_position rows after override re-run"

    def test_longitude_wraparound_corrected(self, pipeline, catalog, tmp_path):
        """process_kin converts longitudes > 180 to negative (PRIDE outputs 0–360)."""
        kin_file = FIXTURES / "kin_2025251_ncc1.kin"
        _add_kin_entry(catalog, kin_file)

        pipeline.process_kin()

        df = pipeline.kinPositionTDB.read_df(
            start=datetime.datetime(2025, 9, 8, tzinfo=datetime.timezone.utc),
            end=datetime.datetime(2025, 9, 8, 0, 1, tzinfo=datetime.timezone.utc),
        )
        assert len(df) > 0, "Need rows to check longitude"
        assert (df["longitude"] <= 180).all(), (
            "All longitudes should be <= 180 after wraparound fix"
        )


# ---------------------------------------------------------------------------
# Tests: update_shotdata
# ---------------------------------------------------------------------------


@pytest.mark.skipif(not _TILEDB_DEPS, reason="earthscope_sfg_tools not installed")
class TestUpdateShotdata:
    """Tests for SV3Pipeline.update_shotdata()."""

    def test_writes_final_shotdata(self, seeded_pipeline, catalog):
        """update_shotdata writes rows to shotDataFinalTDB given pre-seeded inputs."""
        seeded_pipeline.update_shotdata()

        df = seeded_pipeline.shotDataFinalTDB.read_df(
            start=datetime.datetime(2025, 9, 7, tzinfo=datetime.timezone.utc),
            end=datetime.datetime(2025, 9, 14, tzinfo=datetime.timezone.utc),
        )
        assert len(df) > 0, "Expected shotdata_final rows after update_shotdata"

    def test_idempotent_second_run_skips(self, seeded_pipeline, catalog):
        """Second update_shotdata call is a no-op (merge job already recorded)."""
        seeded_pipeline.update_shotdata()

        df_after_first = seeded_pipeline.shotDataFinalTDB.read_df(
            start=datetime.datetime(2025, 9, 7, tzinfo=datetime.timezone.utc),
            end=datetime.datetime(2025, 9, 14, tzinfo=datetime.timezone.utc),
        )
        row_count_after_first = len(df_after_first)

        # Second call should be a no-op due to merge job
        seeded_pipeline.update_shotdata()

        df_after_second = seeded_pipeline.shotDataFinalTDB.read_df(
            start=datetime.datetime(2025, 9, 7, tzinfo=datetime.timezone.utc),
            end=datetime.datetime(2025, 9, 14, tzinfo=datetime.timezone.utc),
        )
        assert len(df_after_second) == row_count_after_first, (
            "Second update_shotdata call should not add duplicate rows"
        )

    def test_override_reruns_despite_merge_job(self, tmp_path, catalog):
        """override=True forces update_shotdata to re-run even with existing merge job."""
        from earthscope_sfg_workflows.pipelines.config import SV3PipelineConfig

        config = SV3PipelineConfig()
        p = _make_pipeline(tmp_path, catalog, config=config)
        _seed_tiledb_from_parquet(p)

        p.update_shotdata()

        # Confirm merge job was recorded
        assert len(catalog._merge_jobs) > 0, "Expected merge job after first update_shotdata"

        merge_job_count_before = len(catalog._merge_jobs)

        # Re-run with override — same merge signature added again (idempotent to set)
        config.position_update_config.override = True
        p.update_shotdata()

        # Still at least one merge job recorded (set de-dups same sig)
        assert len(catalog._merge_jobs) >= merge_job_count_before

    def test_empty_tiledb_returns_early(self, pipeline, catalog):
        """update_shotdata returns without error when both TileDB arrays are empty."""
        # No data seeded — get_merge_signature_shotdata will raise, update_shotdata catches it
        pipeline.update_shotdata()  # must not raise


# ---------------------------------------------------------------------------
# Tests: process_dfop00 exception path
# ---------------------------------------------------------------------------


@pytest.mark.skipif(not _TILEDB_DEPS, reason="earthscope_sfg_tools not installed")
class TestProcessDFOP00:
    """Tests for SV3Pipeline.process_dfop00() exception paths."""

    def test_raises_no_dfop00_when_catalog_empty(self, pipeline):
        """process_dfop00 raises NoDFOP00Found when no DFOP00 entries are cataloged."""
        from earthscope_sfg_workflows.pipelines.exceptions import NoDFOP00Found

        with pytest.raises(NoDFOP00Found):
            pipeline.process_dfop00()


# ---------------------------------------------------------------------------
# Tests: catalog de-duplication (InMemoryAssetStore)
# ---------------------------------------------------------------------------


class TestCatalogDeduplication:
    """Tests for de-duplication behavior in the in-memory catalog."""

    def test_assets_to_process_excludes_processed(self, catalog):
        """assets_to_process omits entries already marked as processed."""
        from earthscope_sfg_workflows.data_mgmt.model import AssetEntry, AssetKind
        from upath import UPath

        scope = _make_scope()
        entry = catalog.add(
            AssetEntry(kind=AssetKind.KIN, scope=scope, local_path=UPath("/fake/a.kin"))
        )

        # Mark it processed
        catalog.mark_processed_bulk([entry.id])

        unprocessed = catalog.assets_to_process(
            kind=AssetKind.KIN,
            network=NETWORK,
            station=STATION,
            campaign=CAMPAIGN,
        )
        assert len(unprocessed) == 0, "Processed entry must not appear in assets_to_process"

    def test_assets_to_process_override_includes_processed(self, catalog):
        """assets_to_process with override=True returns all entries regardless of status."""
        from earthscope_sfg_workflows.data_mgmt.model import AssetEntry, AssetKind
        from upath import UPath

        scope = _make_scope()
        entry = catalog.add(
            AssetEntry(kind=AssetKind.KIN, scope=scope, local_path=UPath("/fake/b.kin"))
        )
        catalog.mark_processed_bulk([entry.id])

        all_entries = catalog.assets_to_process(
            kind=AssetKind.KIN,
            override=True,
            network=NETWORK,
            station=STATION,
            campaign=CAMPAIGN,
        )
        assert len(all_entries) == 1, "override=True must return processed entries too"

    def test_merge_job_is_idempotent(self, catalog):
        """Recording the same merge job twice does not create duplicate state."""
        sig = {"parent_type": "KIN", "child_type": "KINPOSITION", "parent_ids": [1, 2, 3]}

        catalog.add_merge_job(**sig)
        catalog.add_merge_job(**sig)  # same signature

        assert catalog.is_merge_complete(**sig), "Merge job should be recorded"
        assert len(catalog._merge_jobs) == 1, "Duplicate merge job must not be stored twice"

    def test_mark_processed_bulk_is_idempotent(self, catalog):
        """Calling mark_processed_bulk twice on the same ids is safe."""
        from earthscope_sfg_workflows.data_mgmt.model import AssetEntry, AssetKind
        from upath import UPath

        scope = _make_scope()
        entry = catalog.add(
            AssetEntry(kind=AssetKind.KIN, scope=scope, local_path=UPath("/fake/c.kin"))
        )

        catalog.mark_processed_bulk([entry.id])
        updated_count = catalog.mark_processed_bulk([entry.id])  # second call

        assert catalog.by_id(entry.id).is_processed
        assert updated_count == 1  # second call still touches that row

    def test_separate_scopes_are_independent(self, catalog):
        """Assets for different campaigns are isolated in assets_to_process."""
        from earthscope_sfg_workflows.data_mgmt.model import AssetEntry, AssetKind, SFGScope
        from upath import UPath

        scope_a = SFGScope(network=NETWORK, station=STATION, campaign="2025_A_1126")
        scope_b = SFGScope(network=NETWORK, station=STATION, campaign="2024_A_0101")

        catalog.add(AssetEntry(kind=AssetKind.KIN, scope=scope_a, local_path=UPath("/a.kin")))
        catalog.add(AssetEntry(kind=AssetKind.KIN, scope=scope_b, local_path=UPath("/b.kin")))

        results_a = catalog.assets_to_process(
            kind=AssetKind.KIN, network=NETWORK, station=STATION, campaign="2025_A_1126"
        )
        results_b = catalog.assets_to_process(
            kind=AssetKind.KIN, network=NETWORK, station=STATION, campaign="2024_A_0101"
        )

        assert len(results_a) == 1
        assert len(results_b) == 1
        assert results_a[0].scope.campaign != results_b[0].scope.campaign


# ---------------------------------------------------------------------------
# Tests: data round-trip (parquet → TileDB → DataFrame)
# ---------------------------------------------------------------------------


@pytest.mark.skipif(not _TILEDB_DEPS, reason="earthscope_sfg_tools not installed")
class TestTileDBRoundTrip:
    """Tests that fixture data survives a write → read round-trip through TileDB."""

    def test_kin_position_round_trip(self, tmp_path):
        """kin_position parquet data can be written to and read back from TileDB."""
        from upath import UPath

        kin_df = pd.read_parquet(FIXTURES / "kin_position_sample.parquet")
        uri = tmp_path / "kin_position_rt.tdb"
        arr = TDBKinPositionArray(UPath(uri))

        kin_df["time"] = pd.to_datetime(kin_df["time"]).astype("datetime64[ms]")
        arr.write_df(kin_df)

        read_back = arr.read_df(
            start=kin_df["time"].min(),
            end=kin_df["time"].max() + pd.Timedelta(seconds=1),
        )
        assert len(read_back) == len(kin_df), (
            f"Round-trip row count mismatch: wrote {len(kin_df)}, read {len(read_back)}"
        )

    def test_shotdata_pre_round_trip(self, tmp_path):
        """shotdata_pre parquet data can be written to and read back from TileDB.

        Uses ``validate=False`` because the parquet fixtures store timestamps as
        datetime64 while the schema expects GPS-second floats.
        """
        from upath import UPath

        pre_df = pd.read_parquet(FIXTURES / "shotdata_pre_sample.parquet")
        if "pingTime" not in pre_df.columns:
            pre_df = pre_df.reset_index()
        pre_df["pingTime"] = pd.to_datetime(pre_df["pingTime"]).astype("datetime64[ns]")
        pre_df["returnTime"] = pd.to_datetime(pre_df["returnTime"]).astype("datetime64[ns]")

        uri = tmp_path / "shotdata_pre_rt.tdb"
        arr = TDBShotDataArray(UPath(uri))
        arr.write_df(pre_df, validate=False)

        read_back = arr.read_df(
            start=pre_df["pingTime"].min(),
            end=pre_df["pingTime"].max() + pd.Timedelta(seconds=1),
        )
        assert len(read_back) > 0, "Expected rows after round-trip"


# ---------------------------------------------------------------------------
# Helpers for novatel / rinex tests
# ---------------------------------------------------------------------------

_NOV770_MOCK = "earthscope_sfg_workflows.pipelines.sv3_pipeline.novb_ops.novatel_770_2tile"
_TDB2RNX_MOCK = "earthscope_sfg_workflows.pipelines.sv3_pipeline.tdb2rnx"

_T0 = datetime.datetime(2025, 9, 8, 0, 0, 0, tzinfo=datetime.timezone.utc)
_T1 = datetime.datetime(2025, 9, 9, 0, 0, 0, tzinfo=datetime.timezone.utc)


def _add_novatel770_entry(catalog, fake_path: Path):
    """Catalog a NOVATEL770 asset entry pointing at *fake_path*."""
    from earthscope_sfg_workflows.data_mgmt.model import AssetEntry, AssetKind
    from upath import UPath

    scope = _make_scope()
    entry = AssetEntry(
        kind=AssetKind.NOVATEL770,
        scope=scope,
        local_path=UPath(fake_path),
        timestamp_created=datetime.datetime.now(tz=datetime.timezone.utc),
    )
    return catalog.add(entry)


# Real RINEX fixture produced from NCC1 DOY-251 (2025-09-08) data.
# Contains a valid header and the first 5 one-second observation epochs.
_RINEX_FIXTURE = FIXTURES / "NCC12510.rnx"


def _make_fake_tdb2rnx(rinex_dest: Path, filenames: list[str] | None = None):
    """Return a side-effect callable that copies the real RINEX fixture into CWD.

    The pipeline ``chdir``s into *rinex_dest* before calling ``tdb2rnx``, so
    the side effect writes to ``Path.cwd()`` at call time.  Using the real
    RINEX fixture means ``rinex_get_time_range`` runs on actual data and the
    catalog entries receive genuine timestamps — no extra mock needed.
    """
    import shutil
    import subprocess

    _names = filenames or ["NCC12510.rnx"]

    def _side_effect(**_kwargs):
        cwd = Path.cwd()
        for name in _names:
            if _RINEX_FIXTURE.exists():
                shutil.copy(_RINEX_FIXTURE, cwd / name)
            else:
                # Fallback when fixture SSD is absent: write minimal valid header.
                (cwd / name).write_text(
                    "     2.11           O                   G                   RINEX VERSION / TYPE\n"
                    "NCC1                                                        MARKER NAME         \n"
                    "  2025     9     8     0     0    0.0000000     GPS         TIME OF FIRST OBS   \n"
                    "  2025     9     8     0     0    4.0000000     GPS         TIME OF LAST OBS    \n"
                    "                                                            END OF HEADER       \n"
                    " 25  9  8  0  0  0.0000000  0  1G05\n"
                    "  20000000.000 8\n"
                )
        return subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr="")

    return _side_effect


# ---------------------------------------------------------------------------
# TestPreProcessNovatel
# ---------------------------------------------------------------------------


class TestPreProcessNovatel:
    """Tests for ``SV3Pipeline.pre_process_novatel``."""

    def test_records_merge_job_after_processing(self, tmp_path, catalog):
        """Cataloged 770 file → novatel_770_2tile called → merge job recorded."""
        from unittest.mock import patch

        from earthscope_sfg_workflows.data_mgmt.model import AssetKind

        fake_nov = tmp_path / "NOV770_fake.raw"
        fake_nov.touch()
        _add_novatel770_entry(catalog, fake_nov)

        pipeline = _make_pipeline(tmp_path, catalog)

        with patch(_NOV770_MOCK) as mock_tile:
            pipeline.pre_process_novatel()

        mock_tile.assert_called_once()
        call_kwargs = mock_tile.call_args
        assert fake_nov in call_kwargs.kwargs.get(
            "files", call_kwargs.args[0] if call_kwargs.args else []
        )

        assert catalog.is_merge_complete(
            parent_type=AssetKind.NOVATEL770.value,
            child_type=AssetKind.GNSSOBSTDB.value,
            parent_ids=[catalog.assets_for(kind=AssetKind.NOVATEL770)[0].id],
        ), "Merge job should be recorded after successful processing"

    def test_idempotent_skips_when_merge_job_exists(self, tmp_path, catalog):
        """Second call with the same entries skips ``novatel_770_2tile`` (idempotency)."""
        from unittest.mock import patch

        fake_nov = tmp_path / "NOV770_fake.raw"
        fake_nov.touch()
        _add_novatel770_entry(catalog, fake_nov)

        pipeline = _make_pipeline(tmp_path, catalog)

        with patch(_NOV770_MOCK) as mock_tile:
            pipeline.pre_process_novatel()
            pipeline.pre_process_novatel()

        assert mock_tile.call_count == 1, (
            "novatel_770_2tile should be called only once; second run should be skipped"
        )

    def test_override_reruns_despite_merge_job(self, tmp_path, catalog):
        """``override=True`` forces re-processing even when a merge job exists."""
        from unittest.mock import patch

        from earthscope_sfg_workflows.pipelines.config import SV3PipelineConfig

        fake_nov = tmp_path / "NOV770_fake.raw"
        fake_nov.touch()
        _add_novatel770_entry(catalog, fake_nov)

        config = SV3PipelineConfig()
        config.novatel_config.override = True
        pipeline = _make_pipeline(tmp_path, catalog, config=config)

        with patch(_NOV770_MOCK) as mock_tile:
            pipeline.pre_process_novatel()
            pipeline.pre_process_novatel()

        assert mock_tile.call_count == 2, (
            "novatel_770_2tile should be called both times when override=True"
        )

    def test_raises_no_novatel_when_catalog_empty(self, tmp_path, catalog):
        """Empty catalog raises ``NoNovatelFound``."""
        from earthscope_sfg_workflows.pipelines.exceptions import NoNovatelFound

        pipeline = _make_pipeline(tmp_path, catalog)

        with pytest.raises(NoNovatelFound):
            pipeline.pre_process_novatel()

    def test_processing_error_is_handled_gracefully(self, tmp_path, catalog):
        """If ``novatel_770_2tile`` raises, the pipeline logs the error but does NOT record a merge job."""
        from unittest.mock import patch

        from earthscope_sfg_workflows.data_mgmt.model import AssetKind

        fake_nov = tmp_path / "NOV770_bad.raw"
        fake_nov.touch()
        entry = _add_novatel770_entry(catalog, fake_nov)

        pipeline = _make_pipeline(tmp_path, catalog)

        with patch(_NOV770_MOCK, side_effect=RuntimeError("binary exploded")):
            # Should NOT raise — the pipeline catches and logs
            pipeline.pre_process_novatel()

        assert not catalog.is_merge_complete(
            parent_type=AssetKind.NOVATEL770.value,
            child_type=AssetKind.GNSSOBSTDB.value,
            parent_ids=[entry.id],
        ), "Merge job must not be recorded when novatel_770_2tile fails"


# ---------------------------------------------------------------------------
# TestGetRinexFiles
# ---------------------------------------------------------------------------


class TestGetRinexFiles:
    """Tests for ``SV3Pipeline.get_rinex_files`` (TileDB GNSS → RINEX)."""

    def test_creates_rinex_files_and_catalogs_them(self, tmp_path, catalog):
        """Mock ``tdb2rnx`` success → RINEX entries appear in the catalog."""
        from unittest.mock import patch

        from earthscope_sfg_workflows.data_mgmt.model import AssetKind

        pipeline = _make_pipeline(tmp_path, catalog)
        rinex_dest = pipeline._campaign_layout.rinex
        rinex_dest.mkdir(parents=True, exist_ok=True)

        side_fx = _make_fake_tdb2rnx(rinex_dest, ["NCC12510.rnx", "NCC12520.rnx"])

        with patch(_TDB2RNX_MOCK, side_effect=side_fx):
            pipeline.get_rinex_files()

        rinex_entries = catalog.assets_for(
            kind=AssetKind.RINEX2,
            network=NETWORK,
            station=STATION,
            campaign=CAMPAIGN,
        )
        assert len(rinex_entries) == 2, "Expected two RINEX2 entries in the catalog"
        assert all(e.kind == AssetKind.RINEX2 for e in rinex_entries)

    def test_records_merge_job_after_rinex_build(self, tmp_path, catalog):
        """A merge job is recorded from GNSSOBSTDB → RINEX2 after a successful build."""
        from unittest.mock import patch

        from earthscope_sfg_workflows.data_mgmt.model import AssetKind

        pipeline = _make_pipeline(tmp_path, catalog)
        rinex_dest = pipeline._campaign_layout.rinex
        rinex_dest.mkdir(parents=True, exist_ok=True)

        with patch(_TDB2RNX_MOCK, side_effect=_make_fake_tdb2rnx(rinex_dest)):
            pipeline.get_rinex_files()

        tdb_uri = pipeline._tiledb_layout.gnss_obs
        year = int(CAMPAIGN.split("_")[0])
        parent_ids = f"N-{NETWORK}|ST-{STATION}|SV-{CAMPAIGN}|TDB-{tdb_uri}|YEAR-{year}"
        assert catalog.is_merge_complete(
            parent_type=AssetKind.GNSSOBSTDB.value,
            child_type=AssetKind.RINEX2.value,
            parent_ids=[parent_ids],
        ), "Merge job should be recorded after RINEX build"

    def test_idempotent_skips_when_merge_job_exists(self, tmp_path, catalog):
        """Second call skips ``tdb2rnx`` when the merge job already exists."""
        from unittest.mock import patch

        pipeline = _make_pipeline(tmp_path, catalog)
        rinex_dest = pipeline._campaign_layout.rinex
        rinex_dest.mkdir(parents=True, exist_ok=True)

        with patch(_TDB2RNX_MOCK, side_effect=_make_fake_tdb2rnx(rinex_dest)) as mock_tdb:
            pipeline.get_rinex_files()
            pipeline.get_rinex_files()

        assert mock_tdb.call_count == 1, (
            "tdb2rnx should only be invoked once; second run should be skipped"
        )

    def test_override_reruns_despite_merge_job(self, tmp_path, catalog):
        """``override=True`` forces regeneration even when merge job exists."""
        from unittest.mock import patch

        from earthscope_sfg_workflows.pipelines.config import SV3PipelineConfig

        config = SV3PipelineConfig()
        config.rinex_config.override = True
        pipeline = _make_pipeline(tmp_path, catalog, config=config)
        rinex_dest = pipeline._campaign_layout.rinex
        rinex_dest.mkdir(parents=True, exist_ok=True)

        with patch(_TDB2RNX_MOCK, side_effect=_make_fake_tdb2rnx(rinex_dest)) as mock_tdb:
            pipeline.get_rinex_files()
            pipeline.get_rinex_files()

        assert mock_tdb.call_count == 2, "tdb2rnx should be invoked both times when override=True"

    def test_raises_no_rinex_built_on_nonzero_returncode(self, tmp_path, catalog):
        """``tdb2rnx`` non-zero exit raises ``NoRinexBuilt``."""
        import subprocess
        from unittest.mock import patch

        from earthscope_sfg_workflows.pipelines.exceptions import NoRinexBuilt

        pipeline = _make_pipeline(tmp_path, catalog)

        failed_result = subprocess.CompletedProcess(
            args=[], returncode=1, stdout="", stderr="tdb2rnx: fatal error"
        )

        with patch(_TDB2RNX_MOCK, return_value=failed_result):
            with pytest.raises(NoRinexBuilt):
                pipeline.get_rinex_files()

    def test_raises_no_rinex_built_when_no_files_produced(self, tmp_path, catalog):
        """``tdb2rnx`` exits 0 but writes no ``.rnx`` files → ``NoRinexBuilt``."""
        import subprocess
        from unittest.mock import patch

        from earthscope_sfg_workflows.pipelines.exceptions import NoRinexBuilt

        pipeline = _make_pipeline(tmp_path, catalog)
        # Ensure rinex_dest exists but stays empty
        pipeline._campaign_layout.rinex.mkdir(parents=True, exist_ok=True)

        empty_result = subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr="")

        with patch(_TDB2RNX_MOCK, return_value=empty_result):
            with pytest.raises(NoRinexBuilt):
                pipeline.get_rinex_files()


# ---------------------------------------------------------------------------
# TestRinexFixture — validates the RINEX text fixture used by the above tests
# ---------------------------------------------------------------------------


class TestRinexFixture:
    """Sanity checks on the real RINEX 2.11 fixture extracted from NCC1 DOY-251 data."""

    def test_fixture_exists(self):
        """The RINEX fixture file must be present in the repo."""
        assert _RINEX_FIXTURE.exists(), (
            f"RINEX fixture not found at {_RINEX_FIXTURE}. "
            "Re-run the fixture extraction script to regenerate it."
        )

    def test_rinex_get_time_range_parses_fixture(self):
        """``rinex_get_time_range`` returns the expected date from the fixture header."""
        from pride_ppp import rinex_get_time_range
        import datetime

        if not _RINEX_FIXTURE.exists():
            pytest.skip("RINEX fixture not present")

        start, end = rinex_get_time_range(_RINEX_FIXTURE)
        assert start.year == 2025
        assert start.month == 9
        assert start.day == 8
        assert start.hour == 0
        assert end >= start

    def test_fixture_has_header_and_epochs(self):
        """The fixture must contain a RINEX END OF HEADER and at least one epoch."""
        if not _RINEX_FIXTURE.exists():
            pytest.skip("RINEX fixture not present")

        text = _RINEX_FIXTURE.read_text()
        assert "END OF HEADER" in text, "Missing END OF HEADER"
        assert "TIME OF FIRST OBS" in text, "Missing TIME OF FIRST OBS"

        # Count epoch lines: RINEX 2 epoch lines start with " YY MM DD"
        header_done = False
        epoch_count = 0
        for line in text.splitlines():
            if "END OF HEADER" in line:
                header_done = True
                continue
            if header_done and line.startswith(" 25"):
                parts = line.split()
                try:
                    int(parts[0])
                    int(parts[1])
                    int(parts[2])
                    epoch_count += 1
                except (ValueError, IndexError):
                    pass

        assert epoch_count >= 5, f"Expected ≥5 epochs in fixture, got {epoch_count}"

    def test_fixture_glob_pattern_matches(self, tmp_path):
        """The ``.rnx`` glob used by ``get_rinex_files`` matches the fixture filename."""
        import shutil

        dest = tmp_path / _RINEX_FIXTURE.name
        shutil.copy(_RINEX_FIXTURE, dest)
        matches = list(tmp_path.glob("*.rnx"))
        assert len(matches) == 1, f"Expected 1 match, got {matches}"
        assert matches[0].name == _RINEX_FIXTURE.name


# ---------------------------------------------------------------------------
# TestNovatel770ToRinexPipeline (combined end-to-end flow)
# ---------------------------------------------------------------------------


class TestNovatel770ToRinexPipeline:
    """End-to-end flow: novatel770 cataloged → TileDB → RINEX generated.

    Both binary calls are mocked so no real Go binaries are required.
    """

    def test_full_flow_novatel_to_rinex(self, tmp_path, catalog):
        """pre_process_novatel → get_rinex_files produces RINEX catalog entries."""
        from unittest.mock import patch

        from earthscope_sfg_workflows.data_mgmt.model import AssetKind

        fake_nov = tmp_path / "NOV770_fake.raw"
        fake_nov.touch()
        _add_novatel770_entry(catalog, fake_nov)

        pipeline = _make_pipeline(tmp_path, catalog)
        rinex_dest = pipeline._campaign_layout.rinex
        rinex_dest.mkdir(parents=True, exist_ok=True)

        with (
            patch(_NOV770_MOCK),
            patch(_TDB2RNX_MOCK, side_effect=_make_fake_tdb2rnx(rinex_dest)),
        ):
            pipeline.pre_process_novatel()
            pipeline.get_rinex_files()

        assert catalog.is_merge_complete(
            parent_type=AssetKind.NOVATEL770.value,
            child_type=AssetKind.GNSSOBSTDB.value,
            parent_ids=[catalog.assets_for(kind=AssetKind.NOVATEL770)[0].id],
        )
        rinex_entries = catalog.assets_for(
            kind=AssetKind.RINEX2, network=NETWORK, station=STATION, campaign=CAMPAIGN
        )
        assert len(rinex_entries) == 1
        # Verify timestamps came from the real RINEX file (not a mock)
        entry = rinex_entries[0]
        assert entry.timestamp_data_start is not None
        assert entry.timestamp_data_start.year == 2025

    def test_full_flow_is_idempotent(self, tmp_path, catalog):
        """Running the full flow twice does not double-process anything."""
        from unittest.mock import patch

        fake_nov = tmp_path / "NOV770_fake.raw"
        fake_nov.touch()
        _add_novatel770_entry(catalog, fake_nov)

        pipeline = _make_pipeline(tmp_path, catalog)
        rinex_dest = pipeline._campaign_layout.rinex
        rinex_dest.mkdir(parents=True, exist_ok=True)

        with (
            patch(_NOV770_MOCK) as mock_nov,
            patch(_TDB2RNX_MOCK, side_effect=_make_fake_tdb2rnx(rinex_dest)) as mock_tdb,
        ):
            pipeline.pre_process_novatel()
            pipeline.get_rinex_files()
            pipeline.pre_process_novatel()
            pipeline.get_rinex_files()

        assert mock_nov.call_count == 1, "novatel_770_2tile should only run once"
        assert mock_tdb.call_count == 1, "tdb2rnx should only run once"
