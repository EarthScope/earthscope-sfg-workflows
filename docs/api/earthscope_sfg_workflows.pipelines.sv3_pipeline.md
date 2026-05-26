# sv3_pipeline

`earthscope_sfg_workflows.pipelines.sv3_pipeline`

SV3 preprocessing pipeline: NovAtel → RINEX → PRIDE-PPP → kinematic → shotdata.

## class `SV3Pipeline`

End-to-end processor for Sonardyne SV3 / NovAtel GNSS seafloor geodesy data.

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

**Methods**

### `SV3Pipeline.get_rinex_files(self) -> None`

Generate and catalog daily RINEX files from the GNSS observation TileDB array.

After each file is written :meth:`_on_rinex_path` is called for
per-file QC (no-op if ``rinex_qc`` raises ``NotImplementedError``).

Raises
------
NoRinexBuilt
    If ``tdb2rnx`` produces no RINEX files or exits with a non-zero
    return code.

### `SV3Pipeline.pre_process_novatel(self) -> None`

Preprocess Novatel 770 and 000 binary files into TileDB observation arrays.

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

### `SV3Pipeline.process_dfop00(self) -> None`

Process Sonardyne DFOP00 files to generate preliminary shotdata.

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

### `SV3Pipeline.process_kin(self) -> None`

Process KIN files to generate kinematic-position DataFrames.

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

### `SV3Pipeline.process_rinex(self) -> None`

Run PRIDE-PPP on RINEX files to generate KIN and residual files.

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

### `SV3Pipeline.process_svp(self, override: bool = False) -> None`

Process CTD and Seabird files to generate a sound velocity profile (SVP).

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

### `SV3Pipeline.run_intermediate_pipeline(self) -> None`

Run only the intermediate pipeline steps, assuming RINEX already exists.

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

### `SV3Pipeline.run_pipeline(self) -> None`

Execute the complete SV3 data processing pipeline in sequence.

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

### `SV3Pipeline.update_shotdata(self) -> None`

Refine shotdata with interpolated high-precision kinematic positions.

Replaces preliminary GNSS positions in ``shotDataPreTDB`` with
PRIDE-PPP kinematic solutions interpolated to each acoustic ping time,
writing the result to ``shotDataFinalTDB``.

Returns
-------
None
    Returns early without raising if the merge-signature lookup fails.

