# External Imports
import concurrent.futures
import datetime
import sys
import threading
import time
from collections import deque
from dataclasses import replace
from functools import partial
from typing import TYPE_CHECKING
import os as _os

from tqdm.auto import tqdm

# Local Imports
from earthscope_sfg_workflows.logging import ProcessLogger
from pathlib import Path
from earthscope_sfg_tools.novatel_tools.rangea_parser import (
    extract_rangea_strings_from_qcpin,
)
from earthscope_sfg_tools.sonardyne_tools.sv3_qc_operations import qcjson_to_shotdata
from earthscope_sfg_tools.tiledb_integration import (
    TDBGNSSObsArray,
    TDBKinPositionArray,
    TDBShotDataArray,
)

from ...data_mgmt.model import AssetEntry, AssetKind
from ...data_mgmt.utils import get_merge_signature_shotdata
from ..base import validate_network_station_campaign
from ..session import StationSession as Workspace, _build_default_workspace
from .config import PrideConfig, QCPipelineConfig, RinexConfig

if TYPE_CHECKING:
    from ..session import StationSession as Workspace
from .exceptions import (
    NoKinFound,
    NoLocalData,
    NoQCPinFound,
    NoRinexBuilt,
    NoRinexFound,
)
from .gnss_rinex_base import GnssRinexPipelineBase
from .shotdata_gnss_refinement import merge_shotdata_qc


def process_single_qcpin(
    entry: AssetEntry,
    shotdata_tdb: TDBShotDataArray,
    rangea_string_queue: deque,
    processed_asset_queue: deque,
) -> bool:
    try:
        df = qcjson_to_shotdata(entry.local_path, ProcessLogger.logger)
        rangea_strings: list[str] = extract_rangea_strings_from_qcpin(entry.local_path)
        # AssetEntry is frozen; produce a replacement marked as processed.
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
    asset_catalog: "Workspace",
    entries_to_process: int,
    stop_event: threading.Event,
) -> None:

    SLEEP_TIME_SECONDS = 10
    total_processed = 0
    sleep_time = SLEEP_TIME_SECONDS
    while True and not stop_event.is_set():
        time.sleep(sleep_time)
        with threading.Lock():
            rangea_string_list = list(rangea_string_queue)
            rangea_string_queue.clear()
        if rangea_string_list:
            print(
                f"Processing {len(rangea_string_list)} RANGEA strings. Total processed: {total_processed}/{entries_to_process}"
            )
            gnss_obs_tdb.write_rangea_strings(rangea_string_list, verbose=False)
            print(
                f"Finished processing batch of RANGEA strings. Total processed: {total_processed}/{entries_to_process}"
            )
        start_time = time.time()
        while processed_asset_queue:
            entry = processed_asset_queue.popleft()
            asset_catalog.add_or_update(entry)
            total_processed += 1
            if total_processed >= entries_to_process:
                return
        elapsed_time = time.time() - start_time
        remainder = SLEEP_TIME_SECONDS - elapsed_time
        if remainder <= 0:
            sleep_time = 0
        else:
            sleep_time = remainder


class QCPipeline(GnssRinexPipelineBase):
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

    The QC pipeline uses separate TileDB arrays from the normal pipeline
    to avoid data overlap.

    Attributes:
        workspace: Manages the project directory structure and data layer access (catalog reads/writes flow through ``workspace.assets``).
        config: Configuration settings for all pipeline steps.
        qcShotDataPreTDB: QC preliminary shotdata (before position refinement).
        qcKinPositionTDB: QC high-precision kinematic positions.
        qcShotDataFinalTDB: QC final shotdata (after position refinement).
        qcGnssObsTDBURI: QC GNSS observation array URI.
    """

    mid_process_workflow = False

    def __init__(
        self,
        directory: Path | str | None = None,
        s3_sync_bucket: str | None = None,
        config: QCPipelineConfig | None = None,
        *,
        workspace: Workspace | None = None,
    ):
        """Initialize the QCPipeline with a workspace and configuration.
        Args:
            directory: Root path of the data tree. Used to build a default :class:`Workspace` when ``workspace`` is not provided.
            s3_sync_bucket: S3 bucket name/URI for sync operations.
            config: Configuration settings for the pipeline. If None, uses default configuration.
            workspace: Pre-constructed workspace. Preferred over ``directory``.
        """
        if workspace is None:
   
            workspace = _build_default_workspace(
                directory if directory is not None else _os.environ.get("MAIN_DIRECTORY", ".")
            )
        super().__init__(workspace)

        self.s3_sync_bucket: str | None = s3_sync_bucket
        self.config = config if config is not None else QCPipelineConfig()

        # Initialize QC-specific TileDB array objects to None
        # These will be created when set_network_station_campaign() is called
        self.qcShotDataPreTDB: TDBShotDataArray = None
        self.qcKinPositionTDB: TDBKinPositionArray = None
        self.qcShotDataFinalTDB: TDBShotDataArray = None
        self.qcGnssObsTDB: TDBGNSSObsArray = None

    # ── GnssRinexPipelineBase abstract interface ──────────────────────────────

    @property
    def _gnss_obs_uri(self):
        return self.qcGnssObsTDB.uri

    @property
    def _kin_position_tdb(self) -> TDBKinPositionArray:
        return self.qcKinPositionTDB

    @property
    def _rinex_config(self) -> RinexConfig:
        return self.config.rinex_config

    @property
    def _pride_config(self) -> PrideConfig:
        return self.config.pride_config

    @property
    def _rinex_merge_label(self) -> str:
        return "|QC"

    def _reset_tiledb_arrays(self) -> None:
        self.qcShotDataPreTDB = None
        self.qcKinPositionTDB = None
        self.qcShotDataFinalTDB = None
        self.qcGnssObsTDB = None

    def _build_tiledb_arrays(self) -> None:
        """Initialize QC-specific TileDB arrays for the current station context."""
        tiledb = self.workspace.ensure_station()

        if self.qcShotDataPreTDB is None:
            self.qcShotDataPreTDB = TDBShotDataArray(tiledb.qc_shotdata_pre)
            self.qcShotDataPreTDB.consolidate()
        if self.qcKinPositionTDB is None:
            self.qcKinPositionTDB = TDBKinPositionArray(tiledb.qc_kin_position)
            self.qcKinPositionTDB.consolidate()
        if self.qcShotDataFinalTDB is None:
            self.qcShotDataFinalTDB = TDBShotDataArray(tiledb.qc_shotdata)
            self.qcShotDataFinalTDB.consolidate()
        if self.qcGnssObsTDB is None:
            self.qcGnssObsTDB = TDBGNSSObsArray(tiledb.qc_gnss_obs)
            self.qcGnssObsTDB.consolidate()

    @validate_network_station_campaign
    def process_qcpin(self) -> None:
        """Process QC PIN files to generate preliminary shotdata.
        This method retrieves all QC PIN files from the asset catalog, converts them
        into ShotDataFrames using qcjson_to_shotdata, and stores the results
        in the QC-specific TileDB ShotData array.

        Raises:
            NoQCPinFound: If no QC PIN files are found for the current context.
        """
        qcpin_entries: list[AssetEntry] = self.workspace.assets_to_process(
            parent_kind=AssetKind.QCPIN,
            override=self.config.qcpin_config.override,
        )
        if not qcpin_entries:
            response = f"No QCPIN Files Found for {self.current_network_name} {self.current_station_name} {self.current_campaign_name}"
            ProcessLogger.error(response)
            raise NoQCPinFound(response)

        response = f"Found {len(qcpin_entries)} QCPIN Files"
        ProcessLogger.info(response)
        count = 0
        rangea_string_queue = deque()
        processed_asset_queue = deque()
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
                self.workspace.assets,
                len(qcpin_entries),
                stop_event,
            ),
        )
        second_step.start()
        with concurrent.futures.ThreadPoolExecutor(max_workers=50) as executor:
            # futures = executor.map(process_func_partial, qcpin_entries)
            futures = [executor.submit(process_func_partial, entry) for entry in qcpin_entries]
            for future in tqdm(
                concurrent.futures.as_completed(futures),
                total=len(qcpin_entries),
                desc="Processing QCPIN files",
            ):
                success = future.result()
                if success:
                    count += 1
        if len(rangea_string_queue) == 0 and len(processed_asset_queue) == 0:
            # stop the second thread if there are no entries to process
            stop_event.set()

        second_step.join(timeout=10)
        response = f"Processed {count} out of {len(qcpin_entries)} QCPIN Files"
        ProcessLogger.info(response)

    @validate_network_station_campaign
    def update_shotdata(self) -> None:
        """Refine QC shotdata with interpolated high-precision kinematic positions.
        Steps:
        1. Gets merge signature from preliminary shotdata and kinematic
           position arrays
        2. Checks if refinement is needed (via override or merge status)
        3. Merges shotdata with interpolated kinematic positions
        4. Writes refined shotdata to final QC TileDB array
        5. Records merge job in asset catalog
        """
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

        if (
            not self.workspace.is_merge_complete(**merge_job)
            or self.config.position_update_config.override
        ):
            dates.append(dates[-1] + datetime.timedelta(days=1))
            merge_shotdata_qc(
                shotdata_pre=self.qcShotDataPreTDB,
                shotdata=self.qcShotDataFinalTDB,
                kin_position=self.qcKinPositionTDB,
                dates=dates,
            )
            self.workspace.add_merge_job(**merge_job)

    @validate_network_station_campaign
    def run_pipeline(self) -> None:
        """Execute the complete QC data processing pipeline in sequence.
        Pipeline steps (in order):
        1. process_qcpin(): Process QC PIN files to generate shotdata
        2. parse_rangea_logs_from_qcpin(): Extract RANGEA logs for GNSS processing
        3. process_rangea_logs(): Process NOVATEL PIN files into TileDB
        4. get_rinex_files(): Generate RINEX files
        5. process_rinex(): Run PRIDE-PPP on RINEX
        6. process_kin(): Convert KIN files to dataframes
        7. update_shotdata(): Refine shotdata with high-precision positions

        Each step checks if processing is needed via config overrides or
        catalog status.
        """
        ProcessLogger.info(
            f"Starting QC Processing Pipeline for {self.current_network_name} {self.current_station_name} {self.current_campaign_name}"
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
            f"Completed QC Processing Pipeline for {self.current_network_name} {self.current_station_name} {self.current_campaign_name}"
        )
