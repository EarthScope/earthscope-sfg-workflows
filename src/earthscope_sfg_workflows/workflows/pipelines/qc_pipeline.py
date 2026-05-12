# External Imports
import concurrent.futures
import datetime
import json
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
    tile2rinex,
)
from earthscope_sfg_workflows.logging import ProcessLogger

from ...data_mgmt.model import AssetEntry, AssetKind, SFGScope, TileDBLayout
from ...data_mgmt.ports import AssetCatalogPort
from ...data_mgmt.utils import get_merge_signature_shotdata
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
    shotdata_tdb: TDBShotDataArray,
    rangea_string_queue: deque,
    processed_asset_queue: deque,
) -> bool:
    try:
        df = qcjson_to_shotdata(entry.local_path, ProcessLogger.logger)
        rangea_strings: list[str] = extract_rangea_strings_from_qcpin(entry.local_path)
        entry = replace(entry, is_processed=True)
        shotdata_tdb.write_df(df)
        rangea_string_queue.extend(rangea_strings)
        processed_asset_queue.append(entry)
        return True
    except Exception as e:
        ProcessLogger.error(f"Error processing {entry.local_path}: {e}")
        return False


def rangea_string_epoch(
    gnss_obs_tdb: TDBGNSSObsArray,
    rangea_string_queue: deque,
    processed_asset_queue: deque,
    catalog: AssetCatalogPort,
    entries_to_process: int,
    stop_event: threading.Event,
) -> None:
    import time as _time

    SLEEP_TIME_SECONDS = 10
    total_processed = 0
    sleep_time = SLEEP_TIME_SECONDS
    while not stop_event.is_set():
        _time.sleep(sleep_time)
        with threading.Lock():
            rangea_string_list = list(rangea_string_queue)
            rangea_string_queue.clear()
        if rangea_string_list:
            print(
                f"Processing {len(rangea_string_list)} RANGEA strings. "
                f"Total processed: {total_processed}/{entries_to_process}"
            )
            gnss_obs_tdb.write_rangea_strings(rangea_string_list, verbose=False)
            print(
                f"Finished processing batch of RANGEA strings. "
                f"Total processed: {total_processed}/{entries_to_process}"
            )
        start_time = _time.time()
        while processed_asset_queue:
            entry = processed_asset_queue.popleft()
            catalog.update(entry)
            total_processed += 1
            if total_processed >= entries_to_process:
                return
        elapsed_time = _time.time() - start_time
        sleep_time = max(0, SLEEP_TIME_SECONDS - elapsed_time)


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

        tiledb: TileDBLayout = self.scope.station.layout.tiledb
        self.qcShotDataPreTDB = TDBShotDataArray(tiledb.qc_shotdata_pre)
        self.qcKinPositionTDB = TDBKinPositionArray(tiledb.qc_kin_position)
        self.qcShotDataFinalTDB = TDBShotDataArray(tiledb.qc_shotdata)
        self.qcGnssObsTDB = TDBGNSSObsArray(tiledb.qc_gnss_obs)

    # ------------------------------------------------------------------
    # Scope accessors
    # ------------------------------------------------------------------

    @property
    def current_network_name(self) -> str:
        return self.scope.network.name

    @property
    def current_station_name(self) -> str:
        return self.scope.station.name

    @property
    def current_campaign_name(self) -> str | None:
        return self.scope.campaign.name

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _build_rinex_meta(self) -> None:
        """Write RINEX metadata JSON files for the current campaign if absent."""
        meta_dir = self.scope.campaign.layout.metadata_dir
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
            parent_kind=AssetKind.QCPIN,
            override=self.config.qcpin_config.override,
        )
        if not qcpin_entries:
            msg = (
                f"No QCPIN Files Found for {self.current_network_name} "
                f"{self.current_station_name} {self.current_campaign_name}"
            )
            ProcessLogger.error(msg)
            raise NoQCPinFound(msg)

        ProcessLogger.info(f"Found {len(qcpin_entries)} QCPIN Files")
        count = 0
        rangea_string_queue: deque = deque()
        processed_asset_queue: deque = deque()
        process_func_partial = partial(
            process_single_qcpin,
            shotdata_tdb=self.qcShotDataPreTDB,
            rangea_string_queue=rangea_string_queue,
            processed_asset_queue=processed_asset_queue,
        )
        stop_event = threading.Event()
        second_step = threading.Thread(
            target=rangea_string_epoch,
            args=(
                self.qcGnssObsTDB,
                rangea_string_queue,
                processed_asset_queue,
                self.catalog,
                len(qcpin_entries),
                stop_event,
            ),
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

        if not rangea_string_queue and not processed_asset_queue:
            stop_event.set()
        second_step.join(timeout=10)

        ProcessLogger.info(f"Processed {count} out of {len(qcpin_entries)} QCPIN Files")

    @_pipeline_method
    def get_rinex_files(self) -> None:
        """Generate and catalog daily RINEX files from the QC GNSS observation TileDB array.

        Raises:
            NoRinexBuilt: If ``tile2rinex`` produces no files.
        """
        rinex_cfg: RinexConfig = self.config.rinex_config
        rinex_dest = self.scope.campaign.layout.rinex

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
                rinex_paths: list[Path] = tile2rinex(
                    gnss_obs_tdb=gnss_uri,
                    settings=rinex_cfg.settings_path,
                    writedir=rinex_dest,
                    time_interval=rinex_cfg.time_interval,
                    processing_year=year,
                    modulo_millis=rinex_cfg.modulo_millis,
                )

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
                        network=self.current_network_name,
                        station=self.current_station_name,
                        campaign=self.current_campaign_name,
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
                sys.exit(1)

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

        intermediate_dir = self.scope.campaign.layout.intermediate
        pride_dir = intermediate_dir / "pride"

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
                    network=self.current_network_name,
                    station=self.current_station_name,
                    campaign=self.current_campaign_name,
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
                    network=self.current_network_name,
                    station=self.current_station_name,
                    campaign=self.current_campaign_name,
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
                    processed_count += 1
                    self.catalog.update(replace(entry, is_processed=True))
                    self.qcKinPositionTDB.write_df(df)
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
