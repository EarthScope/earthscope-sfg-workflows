"""QC pipeline: processes Sonardyne QC PIN files through PRIDE-PPP to refined shotdata."""

# External Imports
import concurrent.futures
import datetime
import json
import os
import shutil
import tempfile
import threading
from collections import deque
from collections.abc import Callable
from dataclasses import replace
from functools import partial, wraps
from pathlib import Path

import numpy as np

# Local Imports
from earthscope_sfg_tools.novatel_tools.rangea_parser import (
    extract_rangea_strings_from_qcpin,
)
from earthscope_sfg_tools.novatel_tools.utils import get_metadata, get_metadatav2
from earthscope_sfg_tools.sonardyne_tools.sv3_qc_operations import qcjson_to_shotdata
from earthscope_sfg_tools.tiledb_integration import (
    TDBGNSSObsArray,
    TDBKinPositionArray,
    TDBShotDataArray,
    tdb2rnx,
)
from pride_ppp import PrideProcessor, ProcessingMode, kin_to_kin_position_df, rinex_get_time_range
from rich.progress import track

from earthscope_sfg_workflows.logging import ProcessLogger

from ..data_mgmt.model import (
    RINEX_KINDS,
    AssetEntry,
    AssetKind,
    CampaignLayout,
    SFGScope,
    TileDBLayout,
    rinex_kind_for_version,
)
from ..data_mgmt.ports import AssetCatalogPort
from ..data_mgmt.utils import get_merge_signature_shotdata
from .config import PrideConfig, QCPipelineConfig, RinexConfig
from .exceptions import (
    NoKinFound,
    NoQCPinFound,
    NoRinexBuilt,
    NoRinexFound,
    NoSVPFound,
)
from .shotdata_gnss_refinement import merge_shotdata_qc
from .svp_processing import process_svp_for_scope

# tdb2rnx names observation files differently depending on RinexConfig's RINEX
# version: short form "STAT####.YYo" for v2, long form "..._MO.rnx" for v3/v4.
_RINEX_OBS_GLOBS = ("*.??o", "*.rnx")


def _find_rinex_files(rinex_dest: Path) -> list[Path]:
    """Find RINEX observation files written by tdb2rnx, in either v2 or v3/v4 naming."""
    return sorted(p for pattern in _RINEX_OBS_GLOBS for p in rinex_dest.glob(pattern))


def _rinex_content_equal(path_a: Path, path_b: Path) -> bool:
    """Compare two RINEX files, ignoring the "PGM / RUN BY / DATE" header line.

    tdb2rnx stamps that line with the current wall-clock generation time on
    every run, so it always differs between two otherwise byte-identical
    regenerations of the same observation data — a plain file comparison
    would treat every day as "changed" on every rebuild.
    """
    with open(path_a, "rb") as fa, open(path_b, "rb") as fb:
        lines_a = [line for line in fa if b"PGM / RUN BY / DATE" not in line]
        lines_b = [line for line in fb if b"PGM / RUN BY / DATE" not in line]
    return lines_a == lines_b


def _pipeline_method(fn):
    """Wrap a pipeline method so only one runs at a time per instance."""

    @wraps(fn)
    def wrapper(self, *args, **kwargs):
        if not self._lock.acquire(blocking=False):
            raise RuntimeError(
                f"Pipeline is busy: cannot call '{fn.__name__}' while another method is running."
            )
        try:
            return fn(self, *args, **kwargs)
        finally:
            self._lock.release()

    return wrapper


def process_single_qcpin(
    entry: AssetEntry,
    shotdata_df_queue: deque,
    rangea_string_queue: deque,
    processed_asset_queue: deque,
) -> bool:
    """Parse a single QC PIN file and append results to the shared queues.

    Parameters
    ----------
    entry : AssetEntry
        Catalog entry for the QC PIN file to process.
    shotdata_df_queue : deque
        Queue to which the parsed shotdata DataFrame is appended.
    rangea_string_queue : deque
        Queue to which extracted RANGEA strings are appended.
    processed_asset_queue : deque
        Queue to which the updated (processed) asset entry is appended.

    Returns
    -------
    bool
        ``True`` on success, ``False`` if parsing failed.
    """
    try:
        df = qcjson_to_shotdata(entry.local_path, ProcessLogger.logger)
        rangea_strings: list[str] = extract_rangea_strings_from_qcpin(entry.local_path)
        if df is None or df.empty:
            ProcessLogger.warning(
                f"No valid shotdata parsed from {entry.local_path.name}, skipping write"
            )
            return False
        entry = replace(entry, is_processed=True)
        shotdata_df_queue.append(df)
        rangea_string_queue.extend(rangea_strings)
        processed_asset_queue.append(entry)
        return True
    except Exception as e:  # noqa: BLE001
        ProcessLogger.error(f"Error processing {entry.local_path}: {e}")
        return False


def rangea_string_epoch(
    gnss_obs_tdb: TDBGNSSObsArray,
    rangea_string_queue: deque,
    stop_event: threading.Event,
) -> None:
    """Flush RANGEA string batches from the queue to the GNSS observation TileDB array.

    Intended to be run in a background thread.  Sleeps for 10 seconds between
    flush cycles.  When *stop_event* is set the loop exits and any remaining
    strings are written before the function returns.

    Parameters
    ----------
    gnss_obs_tdb : TDBGNSSObsArray
        Open TileDB array for GNSS observations.
    rangea_string_queue : deque
        Shared queue populated by :func:`process_single_qcpin`.
    stop_event : threading.Event
        Signal used by the main thread to request shutdown.

    Returns
    -------
    None
    """
    import time as _time

    SLEEP_TIME_SECONDS = 10
    sleep_time = SLEEP_TIME_SECONDS
    while not stop_event.is_set():
        _time.sleep(sleep_time)
        start_time = _time.time()
        rangea_string_list = list(rangea_string_queue)
        rangea_string_queue.clear()
        if rangea_string_list:
            gnss_obs_tdb.write_rangea_strings(rangea_string_list, verbose=False)
        elapsed_time = _time.time() - start_time
        sleep_time = max(0, SLEEP_TIME_SECONDS - elapsed_time)
    # Drain any remaining strings after stop signal
    final_batch = list(rangea_string_queue)
    if final_batch:
        gnss_obs_tdb.write_rangea_strings(final_batch, verbose=False)


class QCPipeline:
    """Orchestrate the QC data processing pipeline for seafloor geodesy.

    This class manages a workflow for processing QC (Quality Control) data
    from Sonardyne equipment, including:

    1. **QC PIN File Processing** — converts QC PIN JSON files to preliminary
       shotdata and extracts RANGEA logs for GNSS processing.
    2. **GNSS Data Processing** — writes NOVATEL observations into a TileDB
       array and generates daily RINEX files from it.
    3. **Precise Point Positioning** — runs PRIDE-PPPAR to produce kinematic
       (KIN) and residual files.
    4. **Kinematic Position Processing** — converts KIN files to structured
       DataFrames stored in a QC-specific TileDB array.
    5. **Shotdata Refinement** — interpolates high-precision GNSS positions to
       acoustic ping times and writes the refined shotdata.

    Attributes
    ----------
    scope : SFGScope
        Active network/station/campaign scope.
    catalog : AssetCatalogPort
        Asset catalog for tracking data provenance.
    config : QCPipelineConfig
        Configuration for all pipeline stages.
    qcShotDataPreTDB : TDBShotDataArray
        QC preliminary shotdata TileDB array (before position refinement).
    qcKinPositionTDB : TDBKinPositionArray
        QC high-precision kinematic position TileDB array.
    qcShotDataFinalTDB : TDBShotDataArray
        QC final shotdata TileDB array (after position refinement).
    qcGnssObsTDB : TDBGNSSObsArray
        QC GNSS observation TileDB array.

    Methods
    -------
    process_qcpin()
        Process QC PIN files to generate preliminary shotdata and GNSS observations.
    get_rinex_files()
        Generate and catalog daily RINEX files from the QC GNSS observation array.
    process_rinex()
        Run PRIDE-PPP on QC RINEX files to generate KIN and residual files.
    process_kin()
        Process KIN files to generate QC kinematic-position DataFrames.
    update_shotdata()
        Refine QC shotdata with interpolated high-precision kinematic positions.
    process_svp(override=False)
        Process CTD and Seabird files to generate a sound velocity profile.
    run_pipeline()
        Execute the complete QC data processing pipeline in sequence.
    """

    def __init__(
        self,
        catalog: AssetCatalogPort,
        scope: SFGScope | None = None,
        config: QCPipelineConfig | None = None,
        campaign_layout: CampaignLayout | None = None,
        tiledb_layout: TileDBLayout | None = None,
        *,
        network: str | None = None,
        station: str | None = None,
        campaign: str | None = None,
    ) -> None:
        """Initialise the QCPipeline.

        Parameters
        ----------
        catalog : AssetCatalogPort
            Asset catalog for provenance tracking.
        scope : SFGScope, optional
            Pre-built scope (preferred). Must have a hydrated station layout.
        config : QCPipelineConfig, optional
            Pipeline configuration; defaults to :class:`QCPipelineConfig`.
        campaign_layout : CampaignLayout, optional
            Directory layout for the active campaign.
        tiledb_layout : TileDBLayout, optional
            URIs for all TileDB arrays used by this pipeline.
        network : str, optional
            Network name (used when *scope* is not provided).
        station : str, optional
            Station name (used when *scope* is not provided).
        campaign : str, optional
            Campaign name (used when *scope* is not provided).

        Raises
        ------
        ValueError
            If neither *scope* nor both *network* and *station* are supplied.
        """
        self._lock = threading.RLock()
        self.config = config if config is not None else QCPipelineConfig()

        if scope is None:
            if network is None or station is None:
                raise ValueError("Must provide either scope or network/station.")
            scope = SFGScope.from_ids(
                network_name=network, station_name=station, campaign_name=campaign
            )

        self.scope: SFGScope = scope
        self.catalog: AssetCatalogPort = catalog
        self._campaign_layout = campaign_layout
        self._tiledb_layout = tiledb_layout

        tiledb = tiledb_layout
        self.qcShotDataPreTDB = TDBShotDataArray(tiledb.qc_shotdata_pre)
        self.qcShotDataPreTDB.consolidate()
        self.qcKinPositionTDB = TDBKinPositionArray(tiledb.qc_kin_position)
        self.qcKinPositionTDB.consolidate()
        self.qcShotDataFinalTDB = TDBShotDataArray(tiledb.qc_shotdata)
        self.qcShotDataFinalTDB.consolidate()
        self.qcGnssObsTDB = TDBGNSSObsArray(tiledb.qc_gnss_obs)
        self.qcGnssObsTDB.consolidate()

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _build_rinex_meta(self) -> str:
        """Write RINEX metadata JSON files for the current campaign if absent.

        Returns
        -------
        str
            The configured ``rinex_version`` (e.g. ``"4.02"``) — read back from
            ``rinex_metav2.json``, whether pre-existing or freshly generated,
            so callers can derive the correct :class:`AssetKind` even if a
            user has hand-edited the file to a different version.
        """
        meta_dir = self._campaign_layout.metadata_dir
        meta_dir.mkdir(parents=True, exist_ok=True)
        rinex_metav2 = meta_dir / "rinex_metav2.json"
        rinex_metav1 = meta_dir / "rinex_metav1.json"

        if rinex_metav2.exists():
            with open(rinex_metav2) as f:
                metadata = json.load(f)
        else:
            metadata = get_metadatav2(site=self.scope.station)
            with open(rinex_metav2, "w") as f:
                json.dump(metadata, f)

        if not rinex_metav1.exists():
            with open(rinex_metav1, "w") as f:
                json.dump(get_metadata(site=self.scope.station), f)

        self.config.rinex_config.settings_path = rinex_metav2
        return metadata["rinex_version"]

    # ------------------------------------------------------------------
    # Pipeline steps
    # ------------------------------------------------------------------

    @_pipeline_method
    def process_qcpin(self) -> None:
        """Process QC PIN files to generate preliminary shotdata and GNSS observations.

        Raises
        ------
        NoQCPinFound
            If no QC PIN files are cataloged for the active campaign.
        """
        qcpin_entries: list[AssetEntry] = self.catalog.assets_to_process(
            network=self.scope.network,
            station=self.scope.station,
            campaign=self.scope.campaign,
            kind=AssetKind.QCPIN,
            override=self.config.qcpin_config.override,
        )
        if not qcpin_entries:
            msg = (
                f"No QCPIN Files Found for {self.scope.network} "
                f"{self.scope.station} {self.scope.campaign}"
            )
            ProcessLogger.error(msg)
            raise NoQCPinFound(msg)

        import pandas as pd

        ProcessLogger.info(f"Found {len(qcpin_entries)} QCPIN Files")
        count = 0
        shotdata_df_queue: deque = deque()
        rangea_string_queue: deque = deque()
        processed_asset_queue: deque = deque()
        process_func_partial = partial(
            process_single_qcpin,
            shotdata_df_queue=shotdata_df_queue,
            rangea_string_queue=rangea_string_queue,
            processed_asset_queue=processed_asset_queue,
        )
        stop_event = threading.Event()
        second_step = threading.Thread(
            target=rangea_string_epoch,
            args=(self.qcGnssObsTDB, rangea_string_queue, stop_event),
        )
        second_step.start()
        with concurrent.futures.ThreadPoolExecutor(max_workers=50) as executor:
            futures = [executor.submit(process_func_partial, entry) for entry in qcpin_entries]
            for future in track(
                concurrent.futures.as_completed(futures),
                total=len(qcpin_entries),
                description="Processing QCPIN files",
            ):
                if future.result():
                    count += 1

        stop_event.set()
        second_step.join()

        # Batch-write all collected shotdata DataFrames in chunks to minimise
        # TileDB fragment count (one write per chunk instead of one per file).
        _BATCH_SIZE = 500
        batch: list = []
        writes = 0
        for df in shotdata_df_queue:
            batch.append(df)
            if len(batch) >= _BATCH_SIZE:
                self.qcShotDataPreTDB.write_df(pd.concat(batch, ignore_index=True))
                batch = []
                writes += 1
        if batch:
            self.qcShotDataPreTDB.write_df(pd.concat(batch, ignore_index=True))
            writes += 1

        # Bulk-mark all successfully processed entries in the main thread.
        processed_ids = [e.id for e in processed_asset_queue if e.id is not None]
        marked = self.catalog.mark_processed_bulk(processed_ids)
        ProcessLogger.info(
            f"Processed {count} out of {len(qcpin_entries)} QCPIN Files "
            f"({marked} catalog entries marked, {writes} TileDB writes)"
        )

        # Consolidate fragments accumulated during batch writes.
        ProcessLogger.info("Consolidating qc_shotdata_pre TileDB array...")
        self.qcShotDataPreTDB.consolidate()

    @_pipeline_method
    def get_rinex_files(self) -> None:
        """Generate and catalog daily RINEX files from the QC GNSS observation TileDB array.

        ``tdb2rnx`` only supports whole-year regeneration — there's no way to
        ask it to rebuild a single day — so every call regenerates every
        day's file into a staging directory first. Each freshly-generated
        file is then content-compared (byte-for-byte) against the existing
        file for that same day, matched by the day the RINEX header actually
        covers rather than by filename, since a day's filename can shift
        (different start time / sampling) as more data arrives for it within
        the same calendar day. Days whose content is unchanged are discarded
        from staging, leaving the existing file, its mtime, and its catalog
        entry untouched; only genuinely new or changed days get replaced.

        This makes it safe to call unconditionally — no ``override`` is
        needed just to notice that a previously-partial day now has more
        data — and it keeps RINEX mtimes meaningful as a "did this actually
        change" signal for :meth:`process_rinex`'s own staleness check.
        ``rinex_cfg.override`` skips the content comparison and treats every
        staged day as changed, forcing a full rebuild.

        Raises
        ------
        NoRinexBuilt
            If ``tdb2rnx`` produces no RINEX files or exits with a non-zero
            return code.
        """
        rinex_cfg: RinexConfig = self.config.rinex_config
        rinex_dest = self._campaign_layout.qc / "rinex"
        rinex_dest.mkdir(parents=True, exist_ok=True)

        year = (
            rinex_cfg.processing_year
            if rinex_cfg.processing_year != -1
            else int(self.scope.campaign.split("_")[0])
        )
        gnss_uri = self.qcGnssObsTDB.uri

        ProcessLogger.info(
            f"Generating QC RINEX files for {self.scope.network} "
            f"{self.scope.station} {year}. This may take a few minutes..."
        )

        rinex_version = self._build_rinex_meta()
        rinex_kind = rinex_kind_for_version(rinex_version)

        existing_by_day: dict[datetime.date, AssetEntry] = {
            e.timestamp_data_start.date(): e
            for e in self.catalog.assets_for(
                network=self.scope.network,
                station=self.scope.station,
                campaign=self.scope.campaign,
                kind=rinex_kind,
            )
            if e.timestamp_data_start is not None
        }

        try:
            with tempfile.TemporaryDirectory(dir=rinex_dest) as staging:
                staging_dir = Path(staging)
                old_cwd = Path.cwd()
                try:
                    os.chdir(staging_dir)
                    result = tdb2rnx(
                        tdb_path=str(gnss_uri),
                        settings_file=str(rinex_cfg.settings_path),
                        time_interval=rinex_cfg.time_interval,
                        processing_year=year,
                        modulo_millis=rinex_cfg.modulo_millis,
                        logger=ProcessLogger.logger,
                    )
                finally:
                    os.chdir(old_cwd)

                if result.returncode != 0:
                    raise NoRinexBuilt(f"tdb2rnx exited with code {result.returncode}")

                staged_paths = _find_rinex_files(staging_dir)

                if not staged_paths:
                    ProcessLogger.warning(
                        f"No QC RINEX files generated for "
                        f"{self.scope.network} {self.scope.station} {year}."
                    )
                    raise NoRinexBuilt("No QC RINEX files were built.")

                rinex_entries: list[AssetEntry] = []
                changed_count = 0
                unchanged_count = 0

                for staged_path in staged_paths:
                    start, end = rinex_get_time_range(staged_path)
                    existing_entry = existing_by_day.get(start.date())
                    existing_path = (
                        Path(existing_entry.local_path)
                        if existing_entry is not None and existing_entry.local_path is not None
                        else None
                    )

                    if (
                        not rinex_cfg.override
                        and existing_path is not None
                        and existing_path.exists()
                        and _rinex_content_equal(staged_path, existing_path)
                    ):
                        unchanged_count += 1
                        rinex_entries.append(existing_entry)
                        continue

                    changed_count += 1
                    if existing_entry is not None:
                        if existing_path is not None:
                            existing_path.unlink(missing_ok=True)
                        if existing_entry.id is not None:
                            self.catalog.delete_by_id(existing_entry.id)

                    final_path = rinex_dest / staged_path.name
                    shutil.move(str(staged_path), str(final_path))
                    entry = AssetEntry(
                        kind=rinex_kind,
                        scope=self.scope,
                        local_path=final_path,
                        timestamp_data_start=start,
                        timestamp_data_end=end,
                        timestamp_created=datetime.datetime.now(tz=datetime.UTC),
                    )
                    persisted = self.catalog.add(entry)
                    rinex_entries.append(persisted if persisted is not None else entry)

            ProcessLogger.info(
                f"QC RINEX for {self.scope.network} {self.scope.station} {year}: "
                f"{changed_count} day(s) changed/new, {unchanged_count} day(s) unchanged "
                f"({len(rinex_entries)} total)."
            )

        except NoRinexBuilt:
            raise

        except Exception as e:
            if (
                message := ProcessLogger.error(f"Error generating QC RINEX files: {e}")
            ) is not None:
                print(message)
            raise NoRinexBuilt(f"QC RINEX generation failed: {e}") from e

    def _select_rinex_needing_pride(
        self, rinex_entries: list[AssetEntry], intermediate_dir: Path, force: bool
    ) -> list[AssetEntry]:
        """Filter to RINEX entries that actually need a PRIDE-PPP run, clearing stale outputs.

        ``PrideProcessor`` only checks whether a ``.kin`` file exists and
        parses (see ``PrideProcessor._validate_kinfile``) — it has no notion
        of the source RINEX having changed since that file was written.
        :meth:`get_rinex_files` only replaces a day's RINEX (and thus its
        mtime) when that day's content actually changed, so comparing
        mtimes here identifies exactly the days that need reprocessing.
        Their stale ``.kin``/``.res`` are deleted so ``_validate_kinfile``
        doesn't skip them anyway just because a (now-outdated) file still
        happens to parse; days that don't need work are dropped from the
        returned list entirely, so ``process_batch`` never pays its per-day
        dependency-resolution cost for them.

        Parameters
        ----------
        rinex_entries : list of AssetEntry
            All cataloged RINEX entries for the active campaign.
        intermediate_dir : Path
            Directory containing ``.kin``/``.res`` output files (matches
            ``PrideProcessor``'s ``output_dir``).
        force : bool
            If ``True``, return every entry unconditionally (matches
            ``PrideConfig.override``) instead of comparing mtimes.

        Returns
        -------
        list of AssetEntry
            The subset that's new, changed, or force-selected.
        """
        if force:
            return rinex_entries

        site = self.scope.station.lower()
        needs_processing: list[AssetEntry] = []
        for entry in rinex_entries:
            if entry.local_path is None or entry.timestamp_data_start is None:
                continue
            rinex_path = Path(entry.local_path)
            if not rinex_path.exists():
                continue
            doy = entry.timestamp_data_start.timetuple().tm_yday
            year = entry.timestamp_data_start.year
            kin_path = intermediate_dir / f"kin_{year}{doy:03d}_{site}.kin"
            res_path = intermediate_dir / f"res_{year}{doy:03d}_{site}.res"

            if kin_path.exists() and rinex_path.stat().st_mtime <= kin_path.stat().st_mtime:
                continue  # up to date

            if kin_path.exists():
                kin_path.unlink()
                if res_path.exists():
                    res_path.unlink()
                ProcessLogger.info(
                    f"RINEX {rinex_path.name} changed since last PRIDE run; "
                    f"cleared stale {kin_path.name} for reprocessing."
                )
            needs_processing.append(entry)
        return needs_processing

    @_pipeline_method
    def process_rinex(self) -> None:
        """Run PRIDE-PPP on QC RINEX files to generate KIN and residual files.

        Selection is based on file mtimes (see
        :meth:`_select_rinex_needing_pride`), not the catalog's
        ``is_processed`` flag — a RINEX file can be re-marked "processed"
        from a prior run yet still need a fresh PRIDE-PPP pass if
        :meth:`get_rinex_files` has since replaced its content (e.g. a
        partial day completed by newly-arrived data). If none need work,
        logs the up-to-date count and returns without running PRIDE-PPP,
        rather than raising.
        """
        pride_cfg: PrideConfig = self.config.pride_config

        ProcessLogger.info(
            f"Running PRIDE-PPPAR on QC RINEX for {self.scope.network} "
            f"{self.scope.station} {self.scope.campaign}. "
            "This may take a few minutes..."
        )

        intermediate_dir = self._campaign_layout.intermediate
        pride_dir = intermediate_dir / "pride"
        pride_dir.mkdir(parents=True, exist_ok=True)

        all_rinex: list[AssetEntry] = [
            e
            for e in self.catalog.assets_for(
                network=self.scope.network,
                station=self.scope.station,
                campaign=self.scope.campaign,
            )
            if e.local_path is not None and e.kind in RINEX_KINDS
        ]

        if not all_rinex:
            ProcessLogger.info(
                f"No QC RINEX files cataloged for {self.scope.network} "
                f"{self.scope.station} {self.scope.campaign}."
            )
            return

        rinex_entries = self._select_rinex_needing_pride(
            all_rinex, intermediate_dir, force=pride_cfg.override
        )

        if not rinex_entries:
            ProcessLogger.info(
                f"All {len(all_rinex)} QC RINEX file(s) for {self.scope.network} "
                f"{self.scope.station} {self.scope.campaign} are already up to date."
            )
            return

        ProcessLogger.info(f"Found {len(rinex_entries)} QC RINEX files to process")

        processor = PrideProcessor(
            pride_dir=pride_dir,
            output_dir=intermediate_dir,
            cli_config=pride_cfg.cli,
            mode=ProcessingMode.DEFAULT,
            override_products_download=pride_cfg.override_products_download,
        )
        rinex_path_map = {e.local_path: e for e in rinex_entries}
        kin_count = res_count = upload_count = 0

        for result in track(
            processor.process_batch(
                [e.local_path for e in rinex_entries],
                max_workers=pride_cfg.n_processes,
                override=pride_cfg.override,
            ),
            description=(
                f"Processing QC RINEX with PRIDE-PPPAR for "
                f"{self.scope.network} {self.scope.station} "
                f"{self.scope.campaign} using {pride_cfg.n_processes} workers"
            ),
            total=len(rinex_entries),
        ):
            rinex_entry = rinex_path_map.get(result.rinex_path)
            if result.kin_path is not None:
                kin_count += 1
                rinex_entry = replace(rinex_entry, is_processed=True)
                self.catalog.update(rinex_entry)
                kin_entry = AssetEntry(
                    kind=AssetKind.KIN,
                    scope=self.scope,
                    local_path=result.kin_path,
                    parent_id=rinex_entry.id,
                    timestamp_data_start=rinex_entry.timestamp_data_start,
                    timestamp_data_end=rinex_entry.timestamp_data_end,
                    timestamp_created=datetime.datetime.now(tz=datetime.UTC),
                )
                if self.catalog.add(kin_entry):
                    upload_count += 1

            res_path = getattr(result, "res_path", None) or getattr(result, "residual_path", None)
            if res_path is not None:
                res_count += 1
                res_entry = AssetEntry(
                    kind=AssetKind.KINRESIDUALS,
                    scope=self.scope,
                    local_path=res_path,
                    parent_id=rinex_entry.id,
                    timestamp_data_start=rinex_entry.timestamp_data_start,
                    timestamp_data_end=rinex_entry.timestamp_data_end,
                    timestamp_created=datetime.datetime.now(tz=datetime.UTC),
                )
                if self.catalog.add(res_entry):
                    upload_count += 1

        ProcessLogger.info(
            f"Generated {kin_count} KIN files and {res_count} residual files from "
            f"{len(rinex_entries)} QC RINEX files, added {upload_count} to catalog"
        )

    @_pipeline_method
    def process_kin(self) -> None:
        """Process KIN files to generate QC kinematic-position DataFrames.

        Raises
        ------
        NoKinFound
            If no KIN files are found for the active campaign in the catalog.
        """
        ProcessLogger.info(
            f"Looking for KIN files to process for {self.scope.network} "
            f"{self.scope.station} {self.scope.campaign}"
        )

        kin_entries: list[AssetEntry] = self.catalog.assets_to_process(
            network=self.scope.network,
            station=self.scope.station,
            campaign=self.scope.campaign,
            kind=AssetKind.KIN,
            override=self.config.kin_config.override,
        )
        if not kin_entries:
            msg = (
                f"No KIN files found to process for "
                f"{self.scope.network} {self.scope.station} "
                f"{self.scope.campaign}"
            )
            ProcessLogger.info(msg)
            raise NoKinFound(msg)

        ProcessLogger.info(f"Found {len(kin_entries)} KIN files to process")

        processed_count = 0
        for entry in track(kin_entries, description="Processing QC KIN files"):
            try:
                df = kin_to_kin_position_df(entry.local_path)
                if df is not None:
                    # PRIDE outputs 0-360 longitudes; schema requires -180 to 180.
                    if "longitude" in df.columns:
                        df["longitude"] = df["longitude"].where(
                            df["longitude"] <= 180, df["longitude"] - 360
                        )
                    self.qcKinPositionTDB.write_df(df)
                    processed_count += 1
                    self.catalog.update(replace(entry, is_processed=True))
            except Exception as e:  # noqa: BLE001
                ProcessLogger.error(f"Error processing {entry.local_path}: {e}")

        ProcessLogger.info(
            f"Generated {processed_count} QC KinPosition dataframes from {len(kin_entries)} KIN files"
        )

    def _campaign_rinex_date_range(self) -> tuple[np.datetime64, np.datetime64] | None:
        """Return the (min, max) day-granularity date range of this campaign's RINEX files.

        The shotdata/kin-position TileDB arrays are shared across every campaign ever
        run at this station (``TileDBLayout.for_station``), so a raw date intersection
        between them spans the station's whole history, not just the active campaign.
        RINEX assets, unlike the TileDB arrays, are cataloged per campaign, so their
        date bounds are used to scope the merge back down to the active campaign.

        Returns
        -------
        tuple[np.datetime64, np.datetime64] or None
            ``(min, max)`` dates, or ``None`` if no dated RINEX assets are cataloged
            for the active campaign yet.
        """
        rinex_entries = [
            e
            for e in self.catalog.assets_for(
                network=self.scope.network,
                station=self.scope.station,
                campaign=self.scope.campaign,
            )
            if e.kind in RINEX_KINDS
            and e.timestamp_data_start is not None
            and e.timestamp_data_end is not None
        ]
        if not rinex_entries:
            return None
        starts = [e.timestamp_data_start.replace(tzinfo=None) for e in rinex_entries]
        ends = [e.timestamp_data_end.replace(tzinfo=None) for e in rinex_entries]
        return np.datetime64(min(starts), "D"), np.datetime64(max(ends), "D")

    @_pipeline_method
    def update_shotdata(self) -> None:
        """Refine QC shotdata with interpolated high-precision kinematic positions.

        Scoped to the active campaign's RINEX date range (see
        :meth:`_campaign_rinex_date_range`) so it doesn't re-merge other
        campaigns' dates that happen to share this station's TileDB arrays.

        Returns
        -------
        None
            Returns early without raising if the merge-signature lookup fails,
            or if the active campaign has no dates to merge.
        """
        ProcessLogger.info("Updating QC shotdata with interpolated QCKinPosition data")

        try:
            merge_signature, dates = get_merge_signature_shotdata(
                self.qcShotDataPreTDB, self.qcKinPositionTDB
            )
        except ValueError as e:
            ProcessLogger.error(e)
            return

        campaign_range = self._campaign_rinex_date_range()
        if campaign_range is not None:
            start, end = campaign_range
            dates = [d for d in dates if start <= d <= end]
            if not dates:
                ProcessLogger.info(
                    f"No shotdata/kin_position dates within the {self.scope.campaign} "
                    "RINEX date range; nothing to refine."
                )
                return
            merge_signature = [str(d) for d in dates]

        merge_job = {
            "parent_type": AssetKind.KINPOSITION.value,
            "child_type": AssetKind.SHOTDATA.value,
            "parent_ids": merge_signature,
        }

        if (
            not self.catalog.is_merge_complete(**merge_job)
            or self.config.position_update_config.override
        ):
            dates.append(dates[-1] + datetime.timedelta(days=1))
            merge_shotdata_qc(
                shotdata_pre=self.qcShotDataPreTDB,
                shotdata=self.qcShotDataFinalTDB,
                kin_position=self.qcKinPositionTDB,
                dates=dates,
            )
            self.catalog.add_merge_job(**merge_job)

    @_pipeline_method
    def process_svp(self, override: bool = False) -> None:
        """Process CTD and Seabird files to generate a sound velocity profile (SVP).

        Processing order:

        1. Tries each CTD file with ``CTD_to_svp_v2``, then ``CTD_to_svp_v1``.
        2. If no CTD file yields a valid SVP, tries each Seabird file with
           ``seabird_to_soundvelocity``.

        The first successful SVP is written to
        :attr:`CampaignLayout.svp_file` (``<campaign_root>/processed/svp.csv``)
        and processing stops. Shared with
        :meth:`~earthscope_sfg_workflows.pipelines.sv3_pipeline.SV3Pipeline.process_svp`,
        since both pipelines' GARPOS runs read from the same ``svp_file``.

        Parameters
        ----------
        override : bool, optional
            If ``True``, forces reprocessing even if the SVP CSV already
            exists.  Default is ``False``.

        Raises
        ------
        NoSVPFound
            If no CTD or Seabird files are cataloged for the active campaign.
        """
        process_svp_for_scope(
            catalog=self.catalog,
            scope=self.scope,
            destination=self._campaign_layout.svp_file,
            override=override,
        )

    @_pipeline_method
    def run_pipeline(self) -> None:
        """Execute the complete QC data processing pipeline in sequence.

        Steps run in order:

        1. :meth:`process_qcpin` — QC PIN files → preliminary shotdata + GNSS obs.
        2. :meth:`get_rinex_files` — GNSS obs TileDB → daily RINEX files.
        3. :meth:`process_rinex` — RINEX → KIN + residual files via PRIDE-PPP.
        4. :meth:`process_kin` — KIN files → kinematic-position DataFrames.
        5. :meth:`update_shotdata` — merge kinematic positions into final shotdata.
        6. :meth:`process_svp` — CTD/Seabird files → SVP CSV.

        Each step's expected exception is caught and logged so that the
        remaining steps still execute.
        """
        ProcessLogger.info(
            f"Starting QC Processing Pipeline for {self.scope.network} "
            f"{self.scope.station} {self.scope.campaign}"
        )

        try:
            self.process_qcpin()
        except NoQCPinFound:
            pass

        try:
            self.get_rinex_files()
        except NoRinexBuilt:
            pass

        try:
            self.process_rinex()
        except NoRinexFound:
            pass

        try:
            self.process_kin()
        except NoKinFound:
            pass

        self.update_shotdata()

        try:
            self.process_svp()
        except NoSVPFound:
            pass

        ProcessLogger.info(
            f"Completed QC Processing Pipeline for {self.scope.network} "
            f"{self.scope.station} {self.scope.campaign}"
        )


QC_JOBS: dict[str, Callable[["QCPipeline"], None]] = {
    "all": lambda p: p.run_pipeline(),
    "process_qcpin": lambda p: p.process_qcpin(),
    "build_rinex": lambda p: p.get_rinex_files(),
    "run_pride": lambda p: p.process_rinex(),
    "process_kinematic": lambda p: p.process_kin(),
    "refine_shotdata": lambda p: p.update_shotdata(),
    "process_svp": lambda p: p.process_svp(override=p.config.svp_config.override),
}
