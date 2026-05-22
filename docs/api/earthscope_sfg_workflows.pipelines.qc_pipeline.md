# qc_pipeline

`earthscope_sfg_workflows.pipelines.qc_pipeline`

QC pipeline: processes Sonardyne QC PIN files through PRIDE-PPP to refined shotdata.

## class `QCPipeline`

Orchestrate the QC data processing pipeline for seafloor geodesy.

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
run_pipeline()
    Execute the complete QC data processing pipeline in sequence.

**Methods**

### `QCPipeline.get_rinex_files(self) -> None`

Generate and catalog daily RINEX files from the QC GNSS observation TileDB array.

Raises
------
NoRinexBuilt
    If ``tdb2rnx`` produces no RINEX files or exits with a non-zero
    return code.

### `QCPipeline.process_kin(self) -> None`

Process KIN files to generate QC kinematic-position DataFrames.

Raises
------
NoKinFound
    If no KIN files are found for the active campaign in the catalog.

### `QCPipeline.process_qcpin(self) -> None`

Process QC PIN files to generate preliminary shotdata and GNSS observations.

Raises
------
NoQCPinFound
    If no QC PIN files are cataloged for the active campaign.

### `QCPipeline.process_rinex(self) -> None`

Run PRIDE-PPP on QC RINEX files to generate KIN and residual files.

Raises
------
NoRinexFound
    If no processable QC RINEX files are found in the catalog.

### `QCPipeline.run_pipeline(self) -> None`

Execute the complete QC data processing pipeline in sequence.

Steps run in order:

1. :meth:`process_qcpin` — QC PIN files → preliminary shotdata + GNSS obs.
2. :meth:`get_rinex_files` — GNSS obs TileDB → daily RINEX files.
3. :meth:`process_rinex` — RINEX → KIN + residual files via PRIDE-PPP.
4. :meth:`process_kin` — KIN files → kinematic-position DataFrames.
5. :meth:`update_shotdata` — merge kinematic positions into final shotdata.

Each step's expected exception is caught and logged so that the
remaining steps still execute.

### `QCPipeline.update_shotdata(self) -> None`

Refine QC shotdata with interpolated high-precision kinematic positions.

Returns
-------
None
    Returns early without raising if the merge-signature lookup fails.


## `process_single_qcpin(entry: earthscope_sfg_workflows.data_mgmt.model.AssetEntry, shotdata_df_queue: collections.deque, rangea_string_queue: collections.deque, processed_asset_queue: collections.deque) -> bool`

Parse a single QC PIN file and append results to the shared queues.

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

## `rangea_string_epoch(gnss_obs_tdb: earthscope_sfg_tools.tiledb_integration.arrays.TDBGNSSObsArray, rangea_string_queue: collections.deque, stop_event: threading.Event) -> None`

Flush RANGEA string batches from the queue to the GNSS observation TileDB array.

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
