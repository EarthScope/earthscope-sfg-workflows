# External Imports
import datetime
import sys
import threading
from functools import partial, wraps
from multiprocessing import Pool
from pathlib import Path
import json
from typing import Callable
import warnings
from pride_ppp import kin_to_kin_position_df, PrideProcessor, ProcessingResult,ProcessingMode

from earthscope_sfg_workflows.data_mgmt.ports import AssetCatalogPort
from earthscope_sfg_workflows.logging import ProcessLogger
from earthscope_sfg_tools import tiledb_integration as novb_ops
from earthscope_sfg_tools.tiledb_integration import rinex_qc
from earthscope_sfg_tools.seafloor_site_tools.soundspeed_operations import (
    CTD_to_svp_v1,
    CTD_to_svp_v2,
    seabird_to_soundvelocity,
)
from earthscope_sfg_tools.sonardyne_tools import sv3_operations as sv3_ops
from earthscope_sfg_tools.tiledb_integration import (
    TDBIMUPositionArray,
    TDBKinPositionArray,
    TDBShotDataArray,
)
from tqdm.auto import tqdm
from earthscope_sfg_tools.tiledb_integration import TDBKinPositionArray, tile2rinex

# Local imports
from ...data_mgmt.model import AssetEntry, AssetKind, SFGScope, TileDBLayout
from ...data_mgmt.utils import get_merge_signature_shotdata
from ..base import validate_network_station_campaign
from ..session import StationSession as Workspace, _build_default_workspace
from .config import PrideConfig, RinexConfig, SV3PipelineConfig
from .exceptions import (
    NoDFOP00Found,
    NoKinFound,
    NoLocalData,
    NoNovatelFound,
    NoRinexBuilt,
    NoRinexFound,
    NoSVPFound,
)
from .gnss_rinex_base import GnssRinexPipelineBase
from .shotdata_gnss_refinement import merge_shotdata_kinposition


def _pipeline_method(fn):
    """Decorator that ensures only one pipeline method runs at a time per instance."""
    @wraps(fn)
    def wrapper(self, *args, **kwargs):
        if not self._lock.acquire(blocking=False):
            raise Exception(
                f"Pipeline is busy: cannot call '{fn.__name__}' while another method is running."
            )
        try:
            return fn(self, *args, **kwargs)
        finally:
            self._lock.release()
    return wrapper


class SV3Pipeline:
    """Orchestrates the end-to-end processing of Sonardyne SV3 and Novatel GNSS data for seafloor geodesy.
    This class manages a comprehensive workflow for processing seafloor geodesy
    data, including:

    1. **GNSS Data Preprocessing**:
       - Processes Novatel 770 binary files (primary GNSS observations)
       - Processes Novatel 000 binary files (secondary GNSS + IMU positions)
       - Stores observations in TileDB arrays for efficient access

    2. **RINEX Generation**:
       - Converts TileDB GNSS observations to daily RINEX files
       - Manages RINEX metadata and file organization

    3. **Precise Point Positioning**:
       - Downloads GNSS product files (SP3, OBX, ATT)
       - Runs PRIDE-PPPAR for high-precision positioning
       - Generates kinematic (KIN) and residual files

    4. **Kinematic Position Processing**:
       - Converts KIN files to structured dataframes
       - Stores kinematic positions in TileDB for interpolation

    5. **Acoustic Data Processing**:
       - Processes Sonardyne DFOP00 files (acoustic ping-reply sequences)
       - Generates preliminary shotdata with acoustic ranges

    6. **Shotdata Refinement**:
       - Interpolates high-precision GNSS positions to acoustic ping times
       - Refines shotdata with improved position estimates

    7. **Sound Velocity Profile Processing**:
       - Processes CTD and Seabird files
       - Generates sound velocity profiles for acoustic corrections

    The pipeline operates on a hierarchical directory structure
    (network/station/campaign) and uses TileDB for efficient storage and
    retrieval of time-series data.

    Attributes:
        workspace: Manages the project directory structure and data layer access (catalog reads/writes flow through ``workspace.assets``).
        config: Configuration settings for all pipeline steps, including Novatel, RINEX, PRIDE, DFOP00, and position update configs.
        shotDataPreTDB: Preliminary shotdata (before position refinement).
        kinPositionTDB: High-precision kinematic positions.
        imuPositionTDB: IMU-derived positions (from Novatel 000).
        shotDataFinalTDB: Final shotdata (after position refinement).
        gnssObsTDBURI: Primary GNSS observation array (from Novatel 770).
        gnssObsTDB_secondaryURI: Secondary GNSS observation array (from Novatel 000).
        Methods:
        -------:
        set_network_station_campaign(network, station, campaign): Set the current processing context and initialize directories and TileDB arrays.
        _build_rinex_metadata(): Prepare metadata for RINEX file generation from GNSS observations.
        pre_process_novatel(): Preprocess Novatel 770 and 000 binary files into TileDB arrays.
        get_rinex_files(): Generate daily RINEX files from TileDB GNSS observations.
        process_rinex(): Process RINEX files using PRIDE-PPPAR to generate Kinematic files.
        process_kin(): Convert Kinematic files to structured dataframes and store in TileDB.
        process_dfop00(): Process Sonardyne DFOP00 files to generate preliminary shotdata.
        update_shotdata(): Refine shotdata by interpolating high-precision GNSS positions.
        process_svp(): Process CTD and Seabird files to generate sound velocity profiles.
        run_pipeline(): Execute the full processing pipeline in sequence.
    """

    def __init__(
        self,
        catalog: AssetCatalogPort,
        scope: SFGScope | None = None,
        config: SV3PipelineConfig | None = None,
        *,
        network: str | None = None,
        station: str | None = None,
        campaign: str | None = None,

    ):
        """Initializes the SV3Pipeline with a workspace and configuration.
        Args:
            directory: Root path of the data tree. Used to build a default :class:`Workspace` when ``workspace`` is not provided.
            s3_sync_bucket: S3 bucket name/URI for sync operations.
            config: Configuration settings for the pipeline. If None, uses default configuration. Defaults to None.
            workspace: Pre-constructed workspace. Preferred over ``directory``.
        """

        self._lock = threading.RLock()
        self.config = config if config is not None else SV3PipelineConfig()
        if scope is None:
            if network is None or station is None:
                raise ValueError("Must provide either scope or network/station.")
            scope = SFGScope.from_ids(network=network, station=station, campaign=campaign)

        self.scope: SFGScope = scope
        self.catalog: AssetCatalogPort = catalog

        tiledb: TileDBLayout = self.scope.station.layout.tiledb
        if self.shotDataPreTDB is None:
            self.shotDataPreTDB = TDBShotDataArray(tiledb.shotdata_pre)

        if self.kinPositionTDB is None:
            self.kinPositionTDB = TDBKinPositionArray(tiledb.kin_position)
        if self.imuPositionTDB is None:
            self.imuPositionTDB = TDBIMUPositionArray(tiledb.imu_position)
        if self.shotDataFinalTDB is None:
            self.shotDataFinalTDB = TDBShotDataArray(tiledb.shotdata)

        # Store GNSS URIs for later use
        self.gnssObsTDBURI = tiledb.gnss_obs
        self.gnssObsTDB_secondaryURI = tiledb.gnss_obs_secondary

    def _on_rinex_path(self, path: Path) -> None:
        """Call :func:`rinex_qc` on each generated RINEX file."""
        rinex_qc(path)

    @property
    def current_network_name(self) -> str:
        """Active network name; alias for `self.scope.network.name`."""
        return self.scope.network.name

    @property
    def current_campaign_name(self) -> str | None:
        """Active campaign name; alias for `self.scope.campaign.name`."""
        return self.scope.campaign.name

    @property
    def current_station_name(self) -> str:
        """Active station name; alias for `self.scope.station.name`."""
        return self.scope.station.name

    @_pipeline_method
    def pre_process_novatel(self) -> None:
        """Preprocess Novatel 770 and 000 binary files for the current context.
        Processing steps:
        1. **Novatel 770**: Extracts GNSS observations to primary TileDB array
        2. **Novatel 000**: Extracts GNSS observations to secondary array + IMU
           positions

        Both steps check if processing is needed (via override config or merge
        status) and update the asset catalog upon completion.

        Raises:
            Exception: If no Novatel 770 or 000 files are found.
        """

        """
        Process Novatel 770 files
        1. Query asset catalog for Novatel 770 files for current context
        2. If files exist, check if processing is needed (override or not merged)
        3. Call novatel_770_2tile to process files into TileDB GNSS observation array
        4. Update asset catalog with merge job
        """
        found_novatel_770 = False
        found_novatel_000 = False

        novatel_770_entries: list[AssetEntry] = self.catalog.assets_for(
            network=self.scope.network.name,
            station=self.scope.station.name,
            campaign=self.scope.campaign.name,
            kind=AssetKind.NOVATEL770,
        )

        if novatel_770_entries:
            found_novatel_770 = True
            ProcessLogger.info(
                f"Processing {len(novatel_770_entries)} Novatel 770 files for {self.current_network_name} {self.current_station_name} {self.current_campaign_name}. This may take a few minutes..."
            )
            merge_signature = {
                "parent_type": AssetKind.NOVATEL770.value,
                "child_type": AssetKind.GNSSOBSTDB.value,
                "parent_ids": [x.id for x in novatel_770_entries],
            }
            if self.config.novatel_config.override or not self.catalog.is_merge_complete(
                **merge_signature
            ):
                try:
                    novb_ops.novatel_770_2tile(
                        files=[x.local_path for x in novatel_770_entries],
                        gnss_obs_tdb=self.gnssObsTDBURI,
                        n_procs=self.config.novatel_config.n_processes,
                        logger=ProcessLogger.logger,
                    )

                    self.catalog.add_merge_job(**merge_signature)
                    response = f"Added merge job for {len(novatel_770_entries)} Novatel 770 Entries to the catalog"
                    ProcessLogger.info(response)
                except Exception as e:
                    if (
                        message := ProcessLogger.error(f"Error processing Novatel 770 files: {e}")
                    ) is not None:
                        print(message)
                    
            else:
                response = f"Novatel 770 Data Already Processed for {self.current_network_name} {self.current_station_name} {self.current_campaign_name}"
                ProcessLogger.info(response)
        else:
            ProcessLogger.info(
                f"No Novatel 770 Files Found to Process for {self.current_network_name} {self.current_station_name} {self.current_campaign_name}"
            )

        """
        Process Novatel 000 files
        1. Query asset catalog for Novatel 000 files for current context
        2. If files exist, check if processing is needed (override or not merged)
        3. Call nov0002tile to process files into TileDB GNSS observation array + IMU positions
        4. Update asset catalog with merge job
        
        """
        ProcessLogger.info(
            f"Processing Novatel 000 data for {self.current_network_name} {self.current_station_name} {self.current_campaign_name}"
        )
        novatel_000_entries: list[AssetEntry] = self.catalog.assets_for(
            network=self.scope.network.name,
            station=self.scope.station.name,
            campaign=self.scope.campaign.name,
            kind=AssetKind.NOVATEL000,
        )

        if novatel_000_entries:
            found_novatel_000 = True
            merge_signature = {
                "parent_type": AssetKind.NOVATEL000.value,
                "child_type": AssetKind.GNSSOBSTDB.value,
                "parent_ids": [x.id for x in novatel_000_entries],
            }
            if self.config.novatel_config.override or not self.catalog.is_merge_complete(
                **merge_signature
            ):
                try:
                    novb_ops.nov0002tile(
                        files=[x.local_path for x in novatel_000_entries],
                        gnss_obs_tdb=self.gnssObsTDB_secondaryURI,
                        position_tdb=self.imuPositionTDB.uri,
                        n_procs=self.config.novatel_config.n_processes,
                        logger=ProcessLogger.logger,
                    )

                    self.catalog.add_merge_job(**merge_signature)
                    ProcessLogger.info(
                        f"Added merge job for {len(novatel_000_entries)} Novatel 000 Entries to the catalog"
                    )
                except Exception as e:
                    if (
                        message := ProcessLogger.error(f"Error processing Novatel 000 files: {e}")
                    ) is not None:
                        print(message)
                    sys.exit(1)

        else:
            ProcessLogger.info(
                f"No Novatel 000 Files Found to Process for {self.current_network_name} {self.current_station_name} {self.current_campaign_name}"
            )

        if not found_novatel_770 and not found_novatel_000:
            raise NoNovatelFound(
                f"No Novatel 770 or 000 files found for {self.current_network_name} {self.current_station_name} {self.current_campaign_name}. Cannot proceed with GNSS processing."
            )

    @_pipeline_method
    def process_dfop00(self) -> None:
        """Process Sonardyne DFOP00 files to generate preliminary shotdata.
        Steps:
        1. Retrieves DFOP00 files needing processing
        2. Converts each file to shotdata dataframe (acoustic ping-reply
           sequences)
        3. Writes dataframes to preliminary shotdata TileDB array
        4. Marks files as processed in asset catalog

        Uses multiprocessing for efficient parallel processing.
        """

        # 1. Get the DFOP00 files to process
        dfop00_entries: list[AssetEntry] = self.catalog.assets_to_process(
            network=self.scope.network.name,
            station=self.scope.station.name,
            campaign=self.scope.campaign.name,
            parent_kind=AssetKind.DFOP00,
            override=self.config.dfop00_config.override,
        )
        if not dfop00_entries:
            response = f"No DFOP00 Files Found to Process for {self.current_network_name} {self.current_station_name} {self.current_campaign_name}"
            ProcessLogger.error(response)
            raise NoDFOP00Found(response)

        response = f"Found {len(dfop00_entries)} DFOP00 Files to Process"
        ProcessLogger.info(response)
        count = 0

        # 2. Process DFOP00 files to generate shotdata dataframes
        with Pool() as pool:
            _dfop00_to_shotdata = partial(sv3_ops.dfop00_to_shotdata, logger=ProcessLogger.logger)
            results = pool.imap(_dfop00_to_shotdata, [x.local_path for x in dfop00_entries])
            for shotdata_df, dfo_entry in tqdm(
                zip(results, dfop00_entries, strict=False),
                total=len(dfop00_entries),
                desc="Processing DFOP00 Files",
            ):
                if shotdata_df is not None and not shotdata_df.empty:
                    self.shotDataPreTDB.write_df(shotdata_df)  # write to pre-shotdata
                    count += 1
                    dfo_entry.is_processed = True

                    self.catalog.update(dfo_entry)  # mark as processed
                    ProcessLogger.debug(f" Processed {dfo_entry.local_path}")
                else:
                    ProcessLogger.error(f"Failed to Process {dfo_entry.local_path}")

        response = f"Generated {count} ShotData dataframes From {len(dfop00_entries)} DFOP00 Files"
        ProcessLogger.info(response)

    @_pipeline_method
    def update_shotdata(self):
        """Refine shotdata with interpolated high-precision kinematic positions."""
        """Refine shotdata with interpolated high-precision kinematic positions.
        
        Steps:
        1. Gets merge signature from preliminary shotdata and kinematic
           position arrays
        2. Checks if refinement is needed (via override or merge status)
        3. Merges shotdata with interpolated kinematic positions
        4. Writes refined shotdata to final TileDB array
        5. Records merge job in asset catalog
        
        This step significantly improves position accuracy by replacing GNSS
        positions with interpolated PRIDE-PPP solutions.
        """

        ProcessLogger.info("Updating shotdata with interpolated KinPosition data")

        # 1. Get the merge signature
        try:
            merge_signature, dates = get_merge_signature_shotdata(
                self.shotDataPreTDB, self.kinPositionTDB
            )
        except Exception as e:
            ProcessLogger.error(e)
            return
        merge_job = {
            "parent_type": AssetKind.KINPOSITION.value,
            "child_type": AssetKind.SHOTDATA.value,
            "parent_ids": merge_signature,
        }
        # 2. Check if processing is needed
        if (
            not self.catalog.is_merge_complete(**merge_job)
            or self.config.position_update_config.override
        ):
            # 3. Merge shotdata with interpolated kinematic positions
            merge_shotdata_kinposition(
                shotdata_pre=self.shotDataPreTDB,
                shotdata=self.shotDataFinalTDB,
                kin_position=self.kinPositionTDB,
                position_data=self.imuPositionTDB,
                dates=dates,
            )
            self.catalog.add_merge_job(**merge_job)

    @_pipeline_method
    def process_svp(self, override: bool = False) -> None:
        """Process CTD and Seabird files to generate sound velocity profiles (SVP).
        Processing order:
        1. Tries CTD files with CTD_to_svp_v2
        2. If that fails, tries CTD_to_svp_v1
        3. If still no success, tries Seabird files

        The first successful SVP is saved to the campaign directory and
        processing stops.

        Args:
            override: If True, forces reprocessing even if SVP file exists. Default is False.
        """
        svp_df_destination = self.scope.campaign.layout.root / f"{self.current_station_name}_svp.csv"
        if svp_df_destination.exists() and not override:
            return

        # Get the CTD and Seabird files to process
        ctd_entries: list[AssetEntry] = self.catalog.assets_for(network=self.scope.network.name,
            station=self.scope.station.name,
            campaign=self.scope.campaign.name,
            kind=AssetKind.CTD)
        seabird_entries: list[AssetEntry] = self.catalog.assets_for(network=self.scope.network.name,
            station=self.scope.station.name,
            campaign=self.scope.campaign.name,
            kind=AssetKind.SEABIRD)

        if not ctd_entries and not seabird_entries:
            response = f"No CTD or SEABIRD Files Found to Process for {self.current_network_name} {self.current_station_name} {self.current_campaign_name}"
            ProcessLogger.error(response)
            raise NoSVPFound(response)

        ctd_processing_functions = [CTD_to_svp_v2, CTD_to_svp_v1]

        # Try processing CTD files first
        for ctd_entry in ctd_entries:
            for function in ctd_processing_functions:
                try:
                    svp_df = function(ctd_entry.local_path)
                    if not svp_df.empty:
                        svp_df.to_csv(svp_df_destination, index=False)
                        ctd_entry.is_processed = True
                        self.catalog.update(ctd_entry)  # mark as processed
                        ProcessLogger.info(
                            f"Processed SVP data from CTD file {ctd_entry.local_path} to dataframe with {function.__name__}"
                        )
                        ProcessLogger.info(f"Saved SVP dataframe to {str(svp_df_destination)}")
                        return
                except Exception as e:
                    ProcessLogger.error(
                        f"Error processing CTD file {ctd_entry.local_path} with {function.__name__}: {e}"
                    )
                    continue

        # If no CTD files produced SVP, try Seabird files
        for seabird_entry in seabird_entries:
            try:
                svp_df = seabird_to_soundvelocity(seabird_entry.local_path, ProcessLogger.logger)
                if not svp_df.empty:
                    svp_df.to_csv(svp_df_destination, index=False)
                    seabird_entry.is_processed = True
                    self.catalog.update(seabird_entry)  # mark as processed

                    ProcessLogger.info(
                        f"Processed SVP data from Seabird file {seabird_entry.local_path} and saved to {str(svp_df_destination)}"
                    )
                    return
            except Exception as e:
                ProcessLogger.error(
                    f"Error processing Seabird file {seabird_entry.local_path}: {e}"
                )
                continue

    def _build_rinex_meta(self) -> None:
        """Create RINEX metadata JSON files for the current campaign if absent.

        Writes ``rinex_metav2.json`` and ``rinex_metav1.json`` into
        ``<campaign_root>/metadata/`` and updates the rinex config's
        ``settings_path`` to point at the v2 file.
        """
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

        self._rinex_config.settings_path = rinex_metav2

    @_pipeline_method
    def get_rinex_files(self) -> None:
        """Generate and catalog daily RINEX files from the GNSS observation TileDB array.

        Uses :attr:`_gnss_obs_uri` as the source TileDB, and
        :attr:`_rinex_merge_label` to distinguish SV3 from QC merge records.
        After each file is created, :meth:`_on_rinex_path` is called (useful
        for per-file QC; no-op by default).

        Raises:
            NoRinexBuilt: If ``tile2rinex`` produces no files.
        """
        rinex_cfg:RinexConfig = self.config.rinex_config
        rinex_dest = self.scope.campaign.layout.rinex

        year = (
            rinex_cfg.processing_year
            if rinex_cfg.processing_year != -1
            else int(self.current_campaign_name.split("_")[0])
        )
        gnss_uri = self.scope.station.layout.tiledb.gnss_obs

        ProcessLogger.info(
            f"Generating RINEX files for {self.current_network_name} "
            f"{self.current_station_name} {year}. This may take a few minutes..."
        )

        parent_ids = (
            f"N-{self.current_network_name}"
            f"|ST-{self.current_station_name}"
            f"|SV-{self.current_campaign_name}"
            f"|TDB-{gnss_uri}"
            f"|YEAR-{year}"
        )
        merge_signature = {
            "parent_type": AssetKind.GNSSOBSTDB.value,
            "child_type": AssetKind.RINEX2.value,
            "parent_ids": [parent_ids],
        }

        if rinex_cfg.override or not self.catalog.is_merge_complete(
            **merge_signature
        ):
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
                        f"No RINEX files generated for "
                        f"{self.current_network_name} {self.current_station_name} {year}."
                    )
                    raise NoRinexBuilt("No RINEX files were built.")

                rinex_entries: list[AssetEntry] = []
                upload_count = 0

                for rinex_path in rinex_paths:
                    # TODO generate summary qc report and stash it in the directory.
                    self._on_rinex_path(rinex_path)
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
                    f"Generated {len(rinex_entries)} RINEX files spanning "
                    f"{rinex_entries[0].timestamp_data_start} to "
                    f"{rinex_entries[-1].timestamp_data_end}"
                )
                ProcessLogger.debug(
                    f"Added {upload_count} out of {len(rinex_entries)} RINEX files to the catalog"
                )

            except NoRinexBuilt:
                raise

            except Exception as e:
                if (
                    message := ProcessLogger.error(f"Error generating RINEX files: {e}")
                ) is not None:
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
                f"RINEX already generated for {self.current_network_name}, "
                f"{self.current_station_name}, {year}. "
                f"Found {len(rinex_entries)} entries."
            )

    @_pipeline_method
    def process_rinex(self) -> None:
        """Run PRIDE-PPP on RINEX files to generate KIN and residual files.

        Steps:
        1. Retrieves RINEX files needing processing from the asset catalog.
        2. Filters to entries with a local path.
        3. Runs ``PrideProcessor.process_batch`` to convert RINEX → KIN.
        4. Creates :class:`~...data_mgmt.model.AssetEntry` records for each
           KIN and residual file and adds them to the catalog.

        Raises:
            NoRinexFound: If no processable RINEX files are found.
        """
        pride_cfg:PrideConfig = self.config.pride_config

        ProcessLogger.info(
            f"Running PRIDE-PPPAR on RINEX for {self.current_network_name} "
            f"{self.current_station_name} {self.current_campaign_name}. "
            "This may take a few minutes..."
        )

        pride_dir = self.scope.campaign.layout.pride
        intermediate_dir = self.scope.campaign.layout.intermediate

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
                f"No RINEX files found to process for "
                f"{self.current_network_name} {self.current_station_name} "
                f"{self.current_campaign_name}"
            )
            ProcessLogger.error(msg)
            raise NoRinexFound(msg)

        ProcessLogger.info(f"Found {len(rinex_entries)} RINEX files to process")

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
                f"Processing RINEX with PRIDE-PPPAR for "
                f"{self.current_network_name} {self.current_station_name} "
                f"{self.current_campaign_name} using {pride_cfg.n_processes} workers"
            ),
            total=len(rinex_entries),
        ):
            rinex_entry = rinex_path_map.get(result.rinex_path)
            if result.kin_path is not None:
                kin_count += 1
                rinex_entry.is_processed = True
                rinex_entry = self.catalog.update(rinex_entry)  # mark RINEX as processed

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

            # Handle both attribute names used across pipeline versions
            res_path = getattr(result, "res_path", None) or getattr(
                result, "residual_path", None
            )
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
            f"{len(rinex_entries)} RINEX files, added {upload_count} to catalog"
        )

    @_pipeline_method
    def process_kin(self) -> None:
        """Process KIN files to generate kinematic-position dataframes.

        Steps:
        1. Retrieves KIN files needing processing from the asset catalog.
        2. Converts each KIN file to a structured dataframe via
           ``kin_to_kin_position_df``.
        3. Writes the dataframe to :attr:`_kin_position_tdb`.
        4. Marks each file as processed in the asset catalog.

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
            override=self.config.rinex_config.override,  # use RINEX override to control KIN processing
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
        for entry in tqdm(kin_entries, desc="Processing KIN files"):
            try:
                df = kin_to_kin_position_df(entry.local_path)
                if df is not None:
                    processed_count += 1
                    entry.is_processed = True
                    self.catalog.update(entry)
                    self.kinPositionTDB.write_df(df)
            except Exception as e:
                ProcessLogger.error(f"Error processing {entry.local_path}: {e}")

        ProcessLogger.info(
            f"Generated {processed_count} KinPosition dataframes from {len(kin_entries)} KIN files"
        )

    @_pipeline_method
    def run_pipeline(self) -> None:
        """Execute the complete SV3 data processing pipeline in sequence.
        Pipeline steps (in order):
        1. pre_process_novatel(): Process Novatel GNSS data
        2. get_rinex_files(): Generate RINEX files
        3. process_rinex(): Run PRIDE-PPP on RINEX
        4. process_kin(): Convert KIN files to dataframes
        5. process_dfop00(): Process acoustic data
        6. update_shotdata(): Refine shotdata with high-precision positions
        7. process_svp(): Generate sound velocity profile

        Each step checks if processing is needed via config overrides or
        catalog status.
        """

        ProcessLogger.info(
            f"Starting SV3 Processing Pipeline for {self.current_network_name} {self.current_station_name} {self.current_campaign_name}"
        )
        try:
            self.pre_process_novatel()
        except NoNovatelFound:
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

        try:
            self.process_dfop00()
        except NoDFOP00Found:
            pass

        self.update_shotdata()

        try:
            self.process_svp()
        except NoSVPFound:
            pass

        ProcessLogger.info(
            f"Completed SV3 Processing Pipeline for {self.current_network_name} {self.current_station_name} {self.current_campaign_name}"
        )

    @_pipeline_method
    def run_intermediate_pipeline(self) -> None:
        """Run only the intermediate steps of the SV3 pipeline. This assumes rinex is already downloaded
        Intermediate steps include:
        1. process_rinex(): Run PRIDE-PPP on RINEX
        2. process_kin(): Convert KIN files to dataframes
        3. process_dfop00(): Process acoustic data to preliminary shotdata
        4. update_shotdata(): Refine shotdata with interpolated kinematic
           positions
        5. process_svp(): Generate sound velocity profile

        This allows for faster iteration on acoustic processing and position
        refinement without re-running the full GNSS processing steps.
        """

        ProcessLogger.info(
            f"Starting SV3 Intermediate Pipeline for {self.current_network_name} {self.current_station_name} {self.current_campaign_name}"
        )

        try:
            self.process_rinex()
        except NoRinexFound:
            pass

        try:
            self.process_kin()
        except NoKinFound:
            pass

        try:
            self.process_dfop00()
        except NoDFOP00Found:
            pass

        self.update_shotdata()

        try:
            self.process_svp()
        except NoSVPFound:
            pass

        ProcessLogger.info(
            f"Completed SV3 Intermediate Pipeline for {self.current_network_name} {self.current_station_name} {self.current_campaign_name}"
        )


SV3_JOBS: dict[str, Callable[["SV3Pipeline"], None]] = {
    "all": lambda p: p.run_pipeline(),
    "intermediate": lambda p: p.run_intermediate_pipeline(),
    "process_novatel": lambda p: p.pre_process_novatel(),
    "build_rinex": lambda p: p.get_rinex_files(),
    "run_pride": lambda p: p.process_rinex(),
    "process_kinematic": lambda p: p.process_kin(),
    "process_dfop00": lambda p: p.process_dfop00(),
    "refine_shotdata": lambda p: p.update_shotdata(),
    "process_svp": lambda p: p.process_svp(),
}
