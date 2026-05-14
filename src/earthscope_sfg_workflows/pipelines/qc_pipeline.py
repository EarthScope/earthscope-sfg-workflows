# External Imports
import concurrent.futures
import datetime
import json
import os
import sys
import threading
from collections import deque
from dataclasses import replace
from functools import partial, wraps
from pathlib import Path
from typing import Callable

from pride_ppp import PrideProcessor, ProcessingMode, kin_to_kin_position_df, rinex_get_time_range
from tqdm.auto import tqdm

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
from earthscope_sfg_workflows.logging import ProcessLogger

from ..data_mgmt.model import AssetEntry, AssetKind, CampaignLayout, SFGScope, TileDBLayout
from ..data_mgmt.ports import AssetCatalogPort
from ..data_mgmt.utils import get_merge_signature_shotdata
from .config import PrideConfig, QCPipelineConfig, RinexConfig
from .exceptions import (
    NoKinFound,
    NoQCPinFound,
    NoRinexBuilt,
    NoRinexFound,
)
from .shotdata_gnss_refinement import merge_shotdata_qc


def _pipeline_method(fn):
    """Decorator that ensures only one pipeline method runs at a time per instance."""
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
    try:
        df = qcjson_to_shotdata(entry.local_path, ProcessLogger.logger)
        rangea_strings: list[str] = extract_rangea_strings_from_qcpin(entry.local_path)
        if df is None or df.empty:
            ProcessLogger.warning(f"No valid shotdata parsed from {entry.local_path}, skipping write")
            return False
        entry = replace(entry, is_processed=True)
        shotdata_df_queue.append(df)
        rangea_string_queue.extend(rangea_strings)
        processed_asset_queue.append(entry)
        return True
    except Exception as e:
        ProcessLogger.error(f"Error processing {entry.local_path}: {e}")
        return False


def rangea_string_epoch(
    gnss_obs_tdb: TDBGNSSObsArray,
    rangea_string_queue: deque,
    stop_event: threading.Event,
) -> None:
    """Background thread: flush RANGEA string batches to the GNSS obs TileDB array."""
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
    """Orchestrates the QC data processing pipeline for seafloor geodesy.

    This class manages a workflow for processing QC (Quality Control) data
    from Sonardyne equipment, including:

    1. **QC PIN File Processing**:
       - Processes QC PIN JSON files to generate preliminary shotdata
       - Extracts RANGEA logs from PIN files for GNSS processing

    2. **GNSS Data Processing**:
       - Processes NOVATEL PIN files into TileDB GNSS observation arrays
       - Generates daily RINEX files from GNSS observations

    3. **Precise Point Positioning**:
       - Downloads GNSS product files (SP3, OBX, ATT)
       - Runs PRIDE-PPPAR for high-precision positioning
       - Generates kinematic (KIN) and residual files

    4. **Kinematic Position Processing**:
       - Converts KIN files to structured dataframes
       - Stores kinematic positions in QC-specific TileDB array

    5. **Shotdata Refinement**:
       - Interpolates high-precision GNSS positions to acoustic ping times
       - Refines shotdata with improved position estimates

    Attributes:
        scope: Active network/station/campaign scope.
        catalog: Asset catalog for tracking data provenance.
        config: Configuration settings for all pipeline stages.
        qcShotDataPreTDB: QC preliminary shotdata (before position refinement).
        qcKinPositionTDB: QC high-precision kinematic positions.
        qcShotDataFinalTDB: QC final shotdata (after position refinement).
        qcGnssObsTDB: QC GNSS observation array.
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

        Args:
            catalog: Asset catalog for provenance tracking.
            scope: Pre-built scope (preferred). Must have hydrated station layout.
            config: Pipeline configuration; defaults to :class:`QCPipelineConfig`.
            network: Network name (used when *scope* is not provided).
            station: Station name (used when *scope* is not provided).
            campaign: Campaign name (used when *scope* is not provided).
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
    # Scope accessors
    # ------------------------------------------------------------------

    @property
    def current_network_name(self) -> str:
        return self.scope.network

    @property
    def current_station_name(self) -> str:
        return self.scope.station

    @property
    def current_campaign_name(self) -> str | None:
        return self.scope.campaign

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _build_rinex_meta(self) -> None:
        """Write RINEX metadata JSON files for the current campaign if absent."""
        meta_dir = self._campaign_layout.metadata_dir
        meta_dir.mkdir(parents=True, exist_ok=True)
        rinex_metav2 = meta_dir / "rinex_metav2.json"
        rinex_metav1 = meta_dir / "rinex_metav1.json"

        if not rinex_metav2.exists():
            with open(rinex_metav2, "w") as f:
                json.dump(get_metadatav2(site=self.current_station_name), f)

        if not rinex_metav1.exists():
            with open(rinex_metav1, "w") as f:
                json.dump(get_metadata(site=self.current_station_name), f)

        self.config.rinex_config.settings_path = rinex_metav2

    # ------------------------------------------------------------------
    # Pipeline steps
    # ------------------------------------------------------------------

    @_pipeline_method
    def process_qcpin(self) -> None:
        """Process QC PIN files to generate preliminary shotdata and GNSS observations.

        Raises:
            NoQCPinFound: If no QC PIN files are found for the current context.
        """
        qcpin_entries: list[AssetEntry] = self.catalog.assets_to_process(
            network=self.current_network_name,
            station=self.current_station_name,
            campaign=self.current_campaign_name,
            kind=AssetKind.QCPIN,
            override=self.config.qcpin_config.override,
        )
        if not qcpin_entries:
            msg = (
                f"No QCPIN Files Found for {self.current_network_name} "
                f"{self.current_station_name} {self.current_campaign_name}"
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
            for future in tqdm(
                concurrent.futures.as_completed(futures),
                total=len(qcpin_entries),
                desc="Processing QCPIN files",
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

        Raises:
            NoRinexBuilt: If ``tdb2rnx`` produces no files.
        """
        rinex_cfg: RinexConfig = self.config.rinex_config
        rinex_dest = self._campaign_layout.rinex

        year = (
            rinex_cfg.processing_year
            if rinex_cfg.processing_year != -1
            else int(self.current_campaign_name.split("_")[0])
        )
        gnss_uri = self.qcGnssObsTDB.uri

        ProcessLogger.info(
            f"Generating QC RINEX files for {self.current_network_name} "
            f"{self.current_station_name} {year}. This may take a few minutes..."
        )

        parent_ids = (
            f"N-{self.current_network_name}"
            f"|ST-{self.current_station_name}"
            f"|SV-{self.current_campaign_name}"
            f"|TDB-{gnss_uri}"
            f"|YEAR-{year}"
            f"|QC"
        )
        merge_signature = {
            "parent_type": AssetKind.GNSSOBSTDB.value,
            "child_type": AssetKind.RINEX2.value,
            "parent_ids": [parent_ids],
        }

        if rinex_cfg.override or not self.catalog.is_merge_complete(**merge_signature):
            try:
                self._build_rinex_meta()

                # tdb2rnx writes RINEX files to CWD; run from rinex_dest.
                # Remove any pre-existing .??o files so the post-run glob is clean.
                rinex_dest.mkdir(parents=True, exist_ok=True)
                for _stale in rinex_dest.glob("*.??o"):
                    _stale.unlink()
                old_cwd = Path.cwd()
                try:
                    os.chdir(rinex_dest)
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
                    raise NoRinexBuilt(
                        f"tdb2rnx exited with code {result.returncode}"
                    )

                rinex_paths = sorted(rinex_dest.glob("*.??o"))

                if not rinex_paths:
                    ProcessLogger.warning(
                        f"No QC RINEX files generated for "
                        f"{self.current_network_name} {self.current_station_name} {year}."
                    )
                    raise NoRinexBuilt("No QC RINEX files were built.")

                rinex_entries: list[AssetEntry] = []
                upload_count = 0

                for rinex_path in rinex_paths:
                    start, end = rinex_get_time_range(rinex_path)
                    entry = AssetEntry(
                        kind=AssetKind.RINEX2,
                        scope=self.scope,
                        local_path=rinex_path,
                        timestamp_data_start=start,
                        timestamp_data_end=end,
                        timestamp_created=datetime.datetime.now(tz=datetime.UTC),
                    )
                    persisted = self.catalog.add(entry)
                    rinex_entries.append(persisted if persisted is not None else entry)
                    if persisted is not None:
                        upload_count += 1

                self.catalog.add_merge_job(**merge_signature)

                ProcessLogger.info(
                    f"Generated {len(rinex_entries)} QC RINEX files spanning "
                    f"{rinex_entries[0].timestamp_data_start} to "
                    f"{rinex_entries[-1].timestamp_data_end}"
                )
                ProcessLogger.debug(
                    f"Added {upload_count} out of {len(rinex_entries)} QC RINEX files to the catalog"
                )

            except NoRinexBuilt:
                raise

            except Exception as e:
                if (message := ProcessLogger.error(f"Error generating QC RINEX files: {e}")) is not None:
                    print(message)
                raise NoRinexBuilt(f"QC RINEX generation failed: {e}") from e

        else:
            rinex_entries = self.catalog.assets_for(
                network=self.current_network_name,
                station=self.current_station_name,
                campaign=self.current_campaign_name,
                kind=AssetKind.RINEX2,
            )
            ProcessLogger.debug(
                f"QC RINEX already generated for {self.current_network_name}, "
                f"{self.current_station_name}, {year}. "
                f"Found {len(rinex_entries)} entries."
            )

    @_pipeline_method
    def process_rinex(self) -> None:
        """Run PRIDE-PPP on QC RINEX files to generate KIN and residual files.

        Raises:
            NoRinexFound: If no processable QC RINEX files are found.
        """
        pride_cfg: PrideConfig = self.config.pride_config

        ProcessLogger.info(
            f"Running PRIDE-PPPAR on QC RINEX for {self.current_network_name} "
            f"{self.current_station_name} {self.current_campaign_name}. "
            "This may take a few minutes..."
        )

        intermediate_dir = self._campaign_layout.intermediate
        pride_dir = intermediate_dir / "pride"
        pride_dir.mkdir(parents=True, exist_ok=True)

        rinex_entries: list[AssetEntry] = self.catalog.assets_to_process(
            network=self.current_network_name,
            station=self.current_station_name,
            campaign=self.current_campaign_name,
            kind=AssetKind.RINEX2,
            override=pride_cfg.override,
        )
        rinex_entries = [e for e in rinex_entries if e.local_path is not None]

        if not rinex_entries:
            msg = (
                f"No QC RINEX files found to process for "
                f"{self.current_network_name} {self.current_station_name} "
                f"{self.current_campaign_name}"
            )
            ProcessLogger.error(msg)
            raise NoRinexFound(msg)

        ProcessLogger.info(f"Found {len(rinex_entries)} QC RINEX files to process")

        processor = PrideProcessor(
            pride_dir=pride_dir,
            output_dir=intermediate_dir,
            mode=ProcessingMode.DEFAULT,
        )
        rinex_path_map = {e.local_path: e for e in rinex_entries}
        kin_count = res_count = upload_count = 0

        for result in tqdm(
            processor.process_batch(
                [e.local_path for e in rinex_entries],
                max_workers=pride_cfg.n_processes,
                override=pride_cfg.override,
            ),
            desc=(
                f"Processing QC RINEX with PRIDE-PPPAR for "
                f"{self.current_network_name} {self.current_station_name} "
                f"{self.current_campaign_name} using {pride_cfg.n_processes} workers"
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
        """Process KIN files to generate QC kinematic-position dataframes.

        Raises:
            NoKinFound: If no KIN files are found for the current context.
        """
        ProcessLogger.info(
            f"Looking for KIN files to process for {self.current_network_name} "
            f"{self.current_station_name} {self.current_campaign_name}"
        )

        kin_entries: list[AssetEntry] = self.catalog.assets_to_process(
            network=self.current_network_name,
            station=self.current_station_name,
            campaign=self.current_campaign_name,
            kind=AssetKind.KIN,
            override=self.config.rinex_config.override,
        )
        if not kin_entries:
            msg = (
                f"No KIN files found to process for "
                f"{self.current_network_name} {self.current_station_name} "
                f"{self.current_campaign_name}"
            )
            ProcessLogger.info(msg)
            raise NoKinFound(msg)

        ProcessLogger.info(f"Found {len(kin_entries)} KIN files to process")

        processed_count = 0
        for entry in tqdm(kin_entries, desc="Processing QC KIN files"):
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
            except Exception as e:
                ProcessLogger.error(f"Error processing {entry.local_path}: {e}")

        ProcessLogger.info(
            f"Generated {processed_count} QC KinPosition dataframes from {len(kin_entries)} KIN files"
        )

    @_pipeline_method
    def update_shotdata(self) -> None:
        """Refine QC shotdata with interpolated high-precision kinematic positions."""
        ProcessLogger.info("Updating QC shotdata with interpolated QCKinPosition data")

        try:
            merge_signature, dates = get_merge_signature_shotdata(
                self.qcShotDataPreTDB, self.qcKinPositionTDB
            )
        except Exception as e:
            ProcessLogger.error(e)
            return

        merge_job = {
            "parent_type": AssetKind.KINPOSITION.value,
            "child_type": AssetKind.SHOTDATA.value,
            "parent_ids": merge_signature,
        }

        if not self.catalog.is_merge_complete(**merge_job) or self.config.position_update_config.override:
            dates.append(dates[-1] + datetime.timedelta(days=1))
            merge_shotdata_qc(
                shotdata_pre=self.qcShotDataPreTDB,
                shotdata=self.qcShotDataFinalTDB,
                kin_position=self.qcKinPositionTDB,
                dates=dates,
            )
            self.catalog.add_merge_job(**merge_job)

    @_pipeline_method
    def run_pipeline(self) -> None:
        """Execute the complete QC data processing pipeline in sequence.

        Pipeline steps (in order):
        1. process_qcpin(): Process QC PIN files to generate shotdata + GNSS obs
        2. get_rinex_files(): Generate RINEX files from QC GNSS observations
        3. process_rinex(): Run PRIDE-PPP on RINEX
        4. process_kin(): Convert KIN files to dataframes
        5. update_shotdata(): Refine shotdata with high-precision positions
        """
        ProcessLogger.info(
            f"Starting QC Processing Pipeline for {self.current_network_name} "
            f"{self.current_station_name} {self.current_campaign_name}"
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

        ProcessLogger.info(
            f"Completed QC Processing Pipeline for {self.current_network_name} "
            f"{self.current_station_name} {self.current_campaign_name}"
        )


QC_JOBS: dict[str, Callable[["QCPipeline"], None]] = {
    "all": lambda p: p.run_pipeline(),
    "process_qcpin": lambda p: p.process_qcpin(),
    "build_rinex": lambda p: p.get_rinex_files(),
    "run_pride": lambda p: p.process_rinex(),
    "process_kinematic": lambda p: p.process_kin(),
    "refine_shotdata": lambda p: p.update_shotdata(),
}
