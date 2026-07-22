"""SV3 preprocessing pipeline: NovAtel → RINEX → PRIDE-PPP → kinematic → shotdata."""

# stdlib
import dataclasses
import datetime
import json
import multiprocessing
import os
import sys
import threading
from functools import partial, wraps
from pathlib import Path
from typing import Callable

# third-party — monkey-patch targets must be imported before the patches below
import tiledb as _tiledb
from earthscope_sfg_tools.tiledb_integration.arrays import TBDArray as _TBDArray
from pride_ppp import (
    ProcessingMode,
    PrideProcessor,
    kin_to_kin_position_df,
    rinex_get_time_range,
)
from pride_ppp.factories.processor import PrideProcessor as _PrideProcessorCls
from pride_ppp.specifications.config import PRIDEPPPFileConfig as _PRIDEPPPFileConfig

# pride_ppp <= current version omits `ISB model` from generated config_files;
# pdp3 >= 3.2.7 requires it.  Patch write_config_file to inject the line.
_pride_write_config_orig = _PRIDEPPPFileConfig.write_config_file


def _pride_write_config_patched(self, filepath):
    _pride_write_config_orig(self, filepath)
    p = Path(filepath)
    text = p.read_text()
    if "ISB model" not in text:
        patched = []
        for line in text.splitlines():
            patched.append(line)
            if line.startswith("RCK model"):
                patched.append(
                    "ISB model              = Default"
                    "                 ! GNSS receiver inter-system biases to be processed"
                )
        p.write_text("\n".join(patched) + "\n")


_PRIDEPPPFileConfig.write_config_file = _pride_write_config_patched


# pride_ppp _validate_kinfile uses `if kin_df` on a DataFrame — raises ValueError.
# Patch to use `is not None` check instead.
def _pride_validate_kinfile_patched(_self, kin_path, override=False):
    if not override:
        if not kin_path.exists():
            return False
        kin_df = kin_to_kin_position_df(kin_path)
        if kin_df is not None and not kin_df.empty:
            return True
    return False


_PrideProcessorCls._validate_kinfile = _pride_validate_kinfile_patched


# TBDArray.write_df passes the DataFrame directly to tiledb.from_pandas, but
# tiledb requires the sparse dimension ('time') to be the pandas index, not a
# plain column.  The DataFrame returned by kin_to_kin_position_df has time as
# a plain column.  Patch write_df to set it as the index after validation.
def _tbd_write_df_patched(self, df, validate: bool = True):
    if validate:
        df = self.dataframe_schema.validate(df, lazy=True)
    if "time" in df.columns:
        df = df.set_index("time")
    _tiledb.from_pandas(str(self.uri), df, mode="append")


_TBDArray.write_df = _tbd_write_df_patched

# third-party
from earthscope_sfg_tools import tiledb_integration as novb_ops
from earthscope_sfg_tools.novatel_tools.utils import get_metadata, get_metadatav2
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
    rinex_qc,
    tdb2rnx,
)
from earthscope_sfg_workflows.data_mgmt.ports import AssetCatalogPort
from earthscope_sfg_workflows.logging import ProcessLogger
from rich.progress import track

# local
from ..data_mgmt.model import (
    RINEX_KINDS,
    AssetEntry,
    AssetKind,
    CampaignLayout,
    SFGScope,
    TileDBLayout,
    rinex_kind_for_version,
)
from ..data_mgmt.utils import get_merge_signature_shotdata
from .config import PrideConfig, RinexConfig, SV3PipelineConfig
from .exceptions import (
    NoDFOP00Found,
    NoKinFound,
    NoNovatelFound,
    NoRinexBuilt,
    NoRinexFound,
    NoSVPFound,
)
from .shotdata_gnss_refinement import merge_shotdata_kinposition


def _pipeline_method(fn):
    """Wrap a pipeline method so only one runs at a time per instance."""

    @wraps(fn)
    def wrapper(self, *args, **kwargs):
        if not self._lock.acquire(blocking=False):
            raise Exception(
                f"Pipeline is busy: cannot call '{fn.__name__}' while another method is running."
            )
        _t0 = datetime.datetime.now()
        try:
            return fn(self, *args, **kwargs)
        finally:
            elapsed = (datetime.datetime.now() - _t0).total_seconds()
            ProcessLogger.debug(f"{fn.__name__} completed in {elapsed:.1f}s")
            self._lock.release()

    return wrapper


class SV3Pipeline:
    """End-to-end processor for Sonardyne SV3 / NovAtel GNSS seafloor geodesy data.

    **Stage order and data flow** (run via :meth:`run_pipeline` / ``job="all"``)::

        Novatel 770 ──► TileDB GNSS obs ──► RINEX files ──► PRIDE-PPP ──► KIN files
        Novatel 000 ──► TileDB GNSS obs (secondary) + IMU positions              │
                                                                                   ▼
        DFOP00 files ──────────────────────────────────────────► shotdata_pre ──► shotdata_final
                                                               (acoustic ranges)  (+ positions)
        CTD / Seabird ──► SVP CSV  (processed independently; no blocking deps)

    Each stage checks the asset catalog to avoid redundant work.  Override
    behaviour is controlled per-stage via :class:`SV3PipelineConfig`.

    **Individual stages** (run via ``job=<name>``)::

        "process_novatel"   → pre_process_novatel()        Novatel → TileDB
        "build_rinex"       → get_rinex_files()             TileDB GNSS → RINEX
        "run_pride"         → process_rinex()               RINEX → KIN
        "process_kinematic" → process_kin()                 KIN → TileDB kinematic positions
        "process_dfop00"    → process_dfop00()              DFOP00 → preliminary shotdata
        "refine_shotdata"   → update_shotdata()             merge kin + acoustic → final shotdata
        "process_svp"       → process_svp()                 CTD/Seabird → SVP CSV
        "intermediate"      → run_intermediate_pipeline()   stages 3–7 (skips Novatel + RINEX)
        "all"               → run_pipeline()                stages 1–7 in order

    All catalog reads/writes flow through ``self.catalog``.  TileDB arrays are
    opened once in ``__init__`` and shared across stage methods.

    Attributes
    ----------
    scope : SFGScope
        Active network/station/campaign scope.
    catalog : AssetCatalogPort
        Asset catalog for tracking data provenance.
    config : SV3PipelineConfig
        Configuration for all pipeline stages.
    shotDataPreTDB : TDBShotDataArray
        Preliminary shotdata TileDB array (before position refinement).
    kinPositionTDB : TDBKinPositionArray
        High-precision kinematic position TileDB array.
    imuPositionTDB : TDBIMUPositionArray
        IMU-derived position TileDB array (from Novatel 000 files).
    shotDataFinalTDB : TDBShotDataArray
        Final shotdata TileDB array (after position refinement).
    gnssObsTDBURI : str or Path
        URI for the primary GNSS observation TileDB array.
    gnssObsTDB_secondaryURI : str or Path
        URI for the secondary GNSS observation TileDB array (Novatel 000).

    Methods
    -------
    pre_process_novatel()
        Preprocess Novatel 770 and 000 binary files into TileDB arrays.
    get_rinex_files()
        Generate and catalog daily RINEX files from the GNSS observation array.
    process_rinex()
        Run PRIDE-PPP on RINEX files to generate KIN and residual files.
    process_kin()
        Process KIN files to generate kinematic-position DataFrames.
    process_dfop00()
        Process Sonardyne DFOP00 files to generate preliminary shotdata.
    update_shotdata()
        Refine shotdata with interpolated high-precision kinematic positions.
    process_svp(override=False)
        Process CTD and Seabird files to generate sound velocity profiles.
    run_pipeline()
        Execute the complete SV3 data processing pipeline in sequence.
    run_intermediate_pipeline()
        Run the intermediate pipeline steps (skips Novatel and RINEX generation).
    """

    def __init__(
        self,
        catalog: AssetCatalogPort,
        scope: SFGScope | None = None,
        config: SV3PipelineConfig | None = None,
        campaign_layout: CampaignLayout | None = None,
        tiledb_layout: TileDBLayout | None = None,
        *,
        network: str | None = None,
        station: str | None = None,
        campaign: str | None = None,
    ):
        """Initialise the SV3Pipeline with a scope, catalog, and configuration.

        Parameters
        ----------
        catalog : AssetCatalogPort
            Asset catalog for provenance tracking.
        scope : SFGScope, optional
            Pre-built scope (preferred). Must have a hydrated station layout.
        config : SV3PipelineConfig, optional
            Pipeline configuration; defaults to :class:`SV3PipelineConfig`.
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
        self.config = config if config is not None else SV3PipelineConfig()
        if scope is None:
            if network is None or station is None:
                raise ValueError("Must provide either scope or network/station.")
            scope = SFGScope.from_ids(network=network, station=station, campaign=campaign)

        self.scope: SFGScope = scope
        self.catalog: AssetCatalogPort = catalog
        self._campaign_layout = campaign_layout
        self._tiledb_layout = tiledb_layout

        tiledb = tiledb_layout
        self.shotDataPreTDB = TDBShotDataArray(tiledb.shotdata_pre)
        self.shotDataPreTDB.consolidate()
        self.kinPositionTDB = TDBKinPositionArray(tiledb.kin_position)
        self.kinPositionTDB.consolidate()
        self.imuPositionTDB = TDBIMUPositionArray(tiledb.imu_position)
        self.imuPositionTDB.consolidate()
        self.shotDataFinalTDB = TDBShotDataArray(tiledb.shotdata)
        self.shotDataFinalTDB.consolidate()

        # Store GNSS URIs for later use
        self.gnssObsTDBURI = tiledb.gnss_obs
        self.gnssObsTDB_secondaryURI = tiledb.gnss_obs_secondary

    def _on_rinex_path(self, path: Path) -> None:
        """Run per-file QC on a freshly generated RINEX file; no-op if not implemented."""
        try:
            rinex_qc(path)
        except NotImplementedError:
            ProcessLogger.debug("rinex_qc not yet implemented, skipping per-file QC")

    @_pipeline_method
    def pre_process_novatel(self) -> None:
        """Preprocess Novatel 770 and 000 binary files into TileDB observation arrays.

        Processing steps:

        1. **Novatel 770** — extracts GNSS observations into the primary TileDB
           GNSS observation array via ``novatel_770_2tile``.
        2. **Novatel 000** — extracts GNSS observations into the secondary array
           and IMU positions into ``imuPositionTDB`` via ``nov0002tile``.

        Both steps skip work when a completed merge job already exists in the
        catalog (unless ``config.novatel_config.override`` is ``True``).

        Raises
        ------
        NoNovatelFound
            If neither Novatel 770 nor Novatel 000 files are found for the
            active campaign.
        """
        found_novatel_770 = False
        found_novatel_000 = False

        novatel_770_entries: list[AssetEntry] = self.catalog.assets_for(
            network=self.scope.network,
            station=self.scope.station,
            campaign=self.scope.campaign,
            kind=AssetKind.NOVATEL770,
        )

        if novatel_770_entries:
            found_novatel_770 = True
            ProcessLogger.info(
                f"Processing {len(novatel_770_entries)} Novatel 770 files for {self.scope.network} {self.scope.station} {self.scope.campaign}. This may take a few minutes..."
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
                    ProcessLogger.error(f"Error processing Novatel 770 files: {e}")

            else:
                response = f"Novatel 770 Data Already Processed for {self.scope.network} {self.scope.station} {self.scope.campaign}"
                ProcessLogger.info(response)
        else:
            ProcessLogger.info(
                f"No Novatel 770 Files Found to Process for {self.scope.network} {self.scope.station} {self.scope.campaign}"
            )

        ProcessLogger.info(
            f"Processing Novatel 000 data for {self.scope.network} {self.scope.station} {self.scope.campaign}"
        )
        novatel_000_entries: list[AssetEntry] = self.catalog.assets_for(
            network=self.scope.network,
            station=self.scope.station,
            campaign=self.scope.campaign,
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
                    ProcessLogger.error(f"Error processing Novatel 000 files: {e}")
                    sys.exit(1)

        else:
            ProcessLogger.info(
                f"No Novatel 000 Files Found to Process for {self.scope.network} {self.scope.station} {self.scope.campaign}"
            )

        if not found_novatel_770 and not found_novatel_000:
            raise NoNovatelFound(
                f"No Novatel 770 or 000 files found for {self.scope.network} {self.scope.station} {self.scope.campaign}. Cannot proceed with GNSS processing."
            )

    @_pipeline_method
    def process_dfop00(self) -> None:
        """Process Sonardyne DFOP00 files to generate preliminary shotdata.

        Steps:

        1. Retrieves all cataloged DFOP00 files for the active campaign.
        2. Skips if a completed merge job already records this set (idempotency).
        3. Converts each file to a shotdata DataFrame (acoustic ping-reply
           sequences) using ``sv3_ops.dfop00_to_shotdata`` in a process pool.
        4. Writes DataFrames to the preliminary shotdata TileDB array.
        5. Records a merge job and marks individual entries as processed in the
           asset catalog.

        Raises
        ------
        NoDFOP00Found
            If no DFOP00 files are cataloged for the active campaign.
        """

        # 1. Get all catalogued DFOP00 files (not just unprocessed ones).
        dfop00_entries: list[AssetEntry] = self.catalog.assets_for(
            network=self.scope.network,
            station=self.scope.station,
            campaign=self.scope.campaign,
            kind=AssetKind.DFOP00,
        )
        if not dfop00_entries:
            response = f"No DFOP00 Files Found to Process for {self.scope.network} {self.scope.station} {self.scope.campaign}"
            ProcessLogger.error(response)
            raise NoDFOP00Found(response)

        merge_signature = {
            "parent_type": AssetKind.DFOP00.value,
            "child_type": AssetKind.SHOTDATAPRE.value,
            "parent_ids": [x.id for x in dfop00_entries],
        }

        # 2. Skip if all files have already been merged into shotdata (idempotency).
        if not self.config.dfop00_config.override and self.catalog.is_merge_complete(
            **merge_signature
        ):
            ProcessLogger.info(
                f"DFOP00 data already merged for {self.scope.network} "
                f"{self.scope.station} {self.scope.campaign}, skipping."
            )
            return

        ProcessLogger.info(f"Found {len(dfop00_entries)} DFOP00 Files to Process")
        count = 0
        processed_ids: list[int] = []

        # 3–4. Process files and write to TileDB.
        _ctx = multiprocessing.get_context("fork")
        with _ctx.Pool() as pool:
            _dfop00_to_shotdata = partial(sv3_ops.dfop00_to_shotdata, logger=ProcessLogger.logger)
            results = pool.imap(_dfop00_to_shotdata, [x.local_path for x in dfop00_entries])
            for shotdata_df, dfo_entry in track(
                zip(results, dfop00_entries, strict=False),
                total=len(dfop00_entries),
                description="Processing DFOP00 Files",
            ):
                if shotdata_df is not None and not shotdata_df.empty:
                    self.shotDataPreTDB.write_df(shotdata_df)
                    count += 1
                    if dfo_entry.id is not None:
                        processed_ids.append(dfo_entry.id)
                    ProcessLogger.debug(f" Processed {dfo_entry.local_path}")
                else:
                    ProcessLogger.error(f"Failed to Process {dfo_entry.local_path}")

        # 5. Record the merge job and mark individual entries as processed.
        if count > 0:
            self.catalog.add_merge_job(**merge_signature)
            self.catalog.mark_processed_bulk(processed_ids)

        ProcessLogger.info(
            f"Generated {count} ShotData dataframes From {len(dfop00_entries)} DFOP00 Files"
        )

    @_pipeline_method
    def update_shotdata(self) -> None:
        """Refine shotdata with interpolated high-precision kinematic positions.

        Replaces preliminary GNSS positions in ``shotDataPreTDB`` with
        PRIDE-PPP kinematic solutions interpolated to each acoustic ping time,
        writing the result to ``shotDataFinalTDB``.

        Returns
        -------
        None
            Returns early without raising if the merge-signature lookup fails.
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
        """Process CTD and Seabird files to generate a sound velocity profile (SVP).

        Processing order:

        1. Tries each CTD file with ``CTD_to_svp_v2``, then ``CTD_to_svp_v1``.
        2. If no CTD file yields a valid SVP, tries each Seabird file with
           ``seabird_to_soundvelocity``.

        The first successful SVP is written to
        ``<campaign_root>/<station>_svp.csv`` and processing stops.

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
        svp_df_destination = self._campaign_layout.root / f"{self.scope.station}_svp.csv"
        if svp_df_destination.exists() and not override:
            return

        # Get the CTD and Seabird files to process
        ctd_entries: list[AssetEntry] = self.catalog.assets_for(
            network=self.scope.network,
            station=self.scope.station,
            campaign=self.scope.campaign,
            kind=AssetKind.CTD,
        )
        seabird_entries: list[AssetEntry] = self.catalog.assets_for(
            network=self.scope.network,
            station=self.scope.station,
            campaign=self.scope.campaign,
            kind=AssetKind.SEABIRD,
        )

        if not ctd_entries and not seabird_entries:
            response = f"No CTD or SEABIRD Files Found to Process for {self.scope.network} {self.scope.station} {self.scope.campaign}"
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
                        ctd_entry = dataclasses.replace(ctd_entry, is_processed=True)
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
                    seabird_entry = dataclasses.replace(seabird_entry, is_processed=True)
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

    def _build_rinex_meta(self) -> str:
        """Create RINEX metadata JSON files for the current campaign if absent.

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

    @_pipeline_method
    def get_rinex_files(self) -> None:
        """Generate and catalog daily RINEX files from the GNSS observation TileDB array.

        After each file is written :meth:`_on_rinex_path` is called for
        per-file QC (no-op if ``rinex_qc`` raises ``NotImplementedError``).

        Raises
        ------
        NoRinexBuilt
            If ``tdb2rnx`` produces no RINEX files or exits with a non-zero
            return code.
        """
        rinex_version = self._build_rinex_meta()
        rinex_kind = rinex_kind_for_version(rinex_version)
        rinex_cfg: RinexConfig = self.config.rinex_config
        rinex_dest = self._campaign_layout.rinex

        year = (
            rinex_cfg.processing_year
            if rinex_cfg.processing_year != -1
            else int(self.scope.campaign.split("_")[0])
        )
        gnss_uri = self._tiledb_layout.gnss_obs

        parent_ids = (
            f"N-{self.scope.network}"
            f"|ST-{self.scope.station}"
            f"|SV-{self.scope.campaign}"
            f"|TDB-{gnss_uri}"
            f"|YEAR-{year}"
        )
        merge_signature = {
            "parent_type": AssetKind.GNSSOBSTDB.value,
            "child_type": rinex_kind.value,
            "parent_ids": [parent_ids],
        }

        if rinex_cfg.override or not self.catalog.is_merge_complete(**merge_signature):
            ProcessLogger.info(
                f"Generating RINEX files for {self.scope.network} "
                f"{self.scope.station} {year}. This may take a few minutes..."
            )
            try:
                # tdb2rnx writes RINEX files to CWD; run from rinex_dest.
                # Remove any pre-existing .rnx files so the post-run glob is clean.
                rinex_dest.mkdir(parents=True, exist_ok=True)
                for _stale in rinex_dest.glob("*.rnx"):
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
                    raise NoRinexBuilt(f"tdb2rnx exited with code {result.returncode}")

                rinex_paths = sorted(rinex_dest.glob("*.rnx"))

                if not rinex_paths:
                    ProcessLogger.warning(
                        f"No RINEX files generated for "
                        f"{self.scope.network} {self.scope.station} {year}."
                    )
                    raise NoRinexBuilt("No RINEX files were built.")

                rinex_entries: list[AssetEntry] = []
                upload_count = 0

                for rinex_path in rinex_paths:
                    # TODO generate summary qc report and stash it in the directory.
                    self._on_rinex_path(rinex_path)
                    start, end = rinex_get_time_range(rinex_path)
                    entry = AssetEntry(
                        kind=rinex_kind,
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
                    f"Generated {len(rinex_entries)} RINEX files spanning "
                    f"{rinex_entries[0].timestamp_data_start} to "
                    f"{rinex_entries[-1].timestamp_data_end}"
                )
                ProcessLogger.debug(
                    f"Added {upload_count} out of {len(rinex_entries)} RINEX files to the catalog"
                )

            except NoRinexBuilt:
                raise

            except NotImplementedError as e:
                ProcessLogger.warning(f"tdb2rnx not yet available: {e}")
                raise NoRinexBuilt("tdb2rnx is not yet implemented") from e

            except Exception as e:
                ProcessLogger.error(f"Error generating RINEX files: {e}")
                raise NoRinexBuilt(f"RINEX generation failed: {e}") from e

        else:
            rinex_entries = self.catalog.assets_for(
                network=self.scope.network,
                station=self.scope.station,
                campaign=self.scope.campaign,
                kind=rinex_kind,
            )
            ProcessLogger.info(
                f"RINEX already generated for {self.scope.network} "
                f"{self.scope.station} {year} — skipping tdb2rnx. "
                f"Found {len(rinex_entries)} catalog entries."
            )

    @_pipeline_method
    def process_rinex(self) -> None:
        """Run PRIDE-PPP on RINEX files to generate KIN and residual files.

        Steps:

        1. Retrieves unprocessed RINEX entries from the asset catalog.
        2. Filters to entries that have a local path on disk.
        3. Runs ``PrideProcessor.process_batch`` to convert RINEX → KIN files.
        4. Creates :class:`~earthscope_sfg_workflows.data_mgmt.model.AssetEntry`
           records for each KIN and residual file and adds them to the catalog.

        Raises
        ------
        NoRinexFound
            If no processable RINEX files are found in the catalog for the
            active campaign.
        """
        pride_cfg: PrideConfig = self.config.pride_config

        ProcessLogger.info(
            f"Running PRIDE-PPPAR on RINEX for {self.scope.network} "
            f"{self.scope.station} {self.scope.campaign}. "
            "This may take a few minutes..."
        )

        pride_dir = self._campaign_layout.intermediate / "pride"
        intermediate_dir = self._campaign_layout.intermediate
        pride_dir.mkdir(parents=True, exist_ok=True)

        rinex_entries: list[AssetEntry] = self.catalog.assets_to_process(
            network=self.scope.network,
            station=self.scope.station,
            campaign=self.scope.campaign,
            override=pride_cfg.override,
        )
        rinex_entries = [
            e for e in rinex_entries if e.local_path is not None and e.kind in RINEX_KINDS
        ]

        if not rinex_entries:
            msg = (
                f"No RINEX files found to process for "
                f"{self.scope.network} {self.scope.station} "
                f"{self.scope.campaign}"
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

        for result in track(
            processor.process_batch(
                [e.local_path for e in rinex_entries],
                max_workers=pride_cfg.n_processes,
                override=pride_cfg.override,
            ),
            description=(
                f"Processing RINEX with PRIDE-PPPAR for "
                f"{self.scope.network} {self.scope.station} "
                f"{self.scope.campaign} using {pride_cfg.n_processes} workers"
            ),
            total=len(rinex_entries),
        ):
            rinex_entry = rinex_path_map.get(result.rinex_path)
            if result.kin_path is not None:
                kin_count += 1
                rinex_entry = dataclasses.replace(rinex_entry, is_processed=True)
                self.catalog.update(rinex_entry)  # mark RINEX as processed

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

            # Handle both attribute names used across pipeline versions
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
            f"{len(rinex_entries)} RINEX files, added {upload_count} to catalog"
        )

    @_pipeline_method
    def process_kin(self) -> None:
        """Process KIN files to generate kinematic-position DataFrames.

        Steps:

        1. Retrieves unprocessed KIN entries from the asset catalog.
        2. Converts each KIN file to a structured DataFrame via
           ``kin_to_kin_position_df``.
        3. Writes the DataFrame to ``kinPositionTDB``.
        4. Marks each successfully processed file in the asset catalog.

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
            override=self.config.rinex_config.override,  # use RINEX override to control KIN processing
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
        for entry in track(kin_entries, description="Processing KIN files"):
            try:
                df = kin_to_kin_position_df(entry.local_path)
                if df is not None:
                    # PRIDE outputs 0-360 longitudes; schema requires -180 to 180.
                    if "longitude" in df.columns:
                        df["longitude"] = df["longitude"].where(
                            df["longitude"] <= 180, df["longitude"] - 360
                        )
                    self.kinPositionTDB.write_df(df)
                    processed_count += 1
                    self.catalog.update(dataclasses.replace(entry, is_processed=True))
            except Exception as e:
                ProcessLogger.error(f"Error processing {entry.local_path}: {e}")

        ProcessLogger.info(
            f"Generated {processed_count} KinPosition dataframes from {len(kin_entries)} KIN files"
        )

    @_pipeline_method
    def run_pipeline(self) -> None:
        """Execute the complete SV3 data processing pipeline in sequence.

        Steps run in order:

        1. :meth:`pre_process_novatel` — Novatel binary files → TileDB arrays.
        2. :meth:`get_rinex_files` — GNSS obs TileDB → daily RINEX files.
        3. :meth:`process_rinex` — RINEX → KIN + residual files via PRIDE-PPP.
        4. :meth:`process_kin` — KIN files → kinematic-position DataFrames.
        5. :meth:`process_dfop00` — DFOP00 files → preliminary shotdata.
        6. :meth:`update_shotdata` — merge kinematic positions into final shotdata.
        7. :meth:`process_svp` — CTD/Seabird files → SVP CSV.

        Each step's expected exception is caught so that the remaining steps
        still execute.
        """

        ProcessLogger.info(
            f"Starting SV3 Processing Pipeline for {self.scope.network} {self.scope.station} {self.scope.campaign}"
        )
        try:
            self.pre_process_novatel()
        except NoNovatelFound as e:
            ProcessLogger.warning(f"Skipping Novatel processing: {e}")

        try:
            self.get_rinex_files()
        except NoRinexBuilt as e:
            ProcessLogger.warning(f"Skipping RINEX generation: {e}")

        try:
            self.process_rinex()
        except NoRinexFound as e:
            ProcessLogger.warning(f"Skipping PRIDE-PPP: {e}")

        try:
            self.process_kin()
        except NoKinFound as e:
            ProcessLogger.warning(f"Skipping KIN processing: {e}")

        try:
            self.process_dfop00()
        except NoDFOP00Found as e:
            ProcessLogger.warning(f"Skipping DFOP00 processing: {e}")

        self.update_shotdata()

        try:
            self.process_svp()
        except NoSVPFound as e:
            ProcessLogger.warning(f"Skipping SVP processing: {e}")

        ProcessLogger.info(
            f"Completed SV3 Processing Pipeline for {self.scope.network} {self.scope.station} {self.scope.campaign}"
        )

    @_pipeline_method
    def run_intermediate_pipeline(self) -> None:
        """Run only the intermediate pipeline steps, assuming RINEX already exists.

        Skips Novatel preprocessing and RINEX generation, enabling faster
        iteration on acoustic processing and position refinement.

        Steps run in order:

        1. :meth:`process_rinex` — RINEX → KIN + residual files via PRIDE-PPP.
        2. :meth:`process_kin` — KIN files → kinematic-position DataFrames.
        3. :meth:`process_dfop00` — DFOP00 files → preliminary shotdata.
        4. :meth:`update_shotdata` — merge kinematic positions into final shotdata.
        5. :meth:`process_svp` — CTD/Seabird files → SVP CSV.

        Each step's expected exception is caught so that the remaining steps
        still execute.
        """

        ProcessLogger.info(
            f"Starting SV3 Intermediate Pipeline for {self.scope.network} {self.scope.station} {self.scope.campaign}"
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
            f"Completed SV3 Intermediate Pipeline for {self.scope.network} {self.scope.station} {self.scope.campaign}"
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
