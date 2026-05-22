# processing_service

`earthscope_sfg_workflows.services.processing_service`

PipelineService — pipeline construction and execution for a StationSession.

## class `ProcessingService`

Pipeline construction and execution scoped to a :class:`StationSession`.

Owns the cached pipeline instances (``SV3Pipeline``, ``QCPipeline``) and
all config-merging logic so that :class:`StationSession` stays free of
pipeline concerns.

Attributes
----------
config : _Config or None
    Default pipeline configuration applied when no per-call config is
    provided.

Methods
-------
get_sv3(config, secondary_config)
    Return a configured ``SV3Pipeline`` for the current scope.
run_sv3(job, config, secondary_config)
    Run an ``SV3Pipeline`` job for the current scope.
get_qc(config, secondary_config)
    Return a configured ``QCPipeline`` for the current scope.
run_qc(job, config)
    Run a ``QCPipeline`` job for the current scope.
parse_surveys(survey_id, override, write_intermediate)
    Parse surveys and write CSVs into survey directories.

**Methods**

### `ProcessingService.get_qc(self, config: "'QCPipelineConfig | None'" = None, secondary_config: "'_Config | None'" = None) -> 'QCPipeline'`

Return a configured ``QCPipeline`` for the current scope.

Parameters
----------
config : QCPipelineConfig or None, optional
    Primary config override. Default is ``None``.
secondary_config : _Config or None, optional
    Secondary config applied on top of the primary merge. Default is
    ``None``.

Returns
-------
QCPipeline
    Fully configured pipeline instance (cached between calls).

### `ProcessingService.get_sv3(self, config: "'_Config | None'" = None, secondary_config: "'_Config | None'" = None) -> 'SV3Pipeline'`

Return a configured ``SV3Pipeline`` for the current scope.

Config merging order: defaults → construction config → *config* →
*secondary_config*.

Parameters
----------
config : _Config or None, optional
    Primary config override. Defaults to the value set at construction
    when ``None``.
secondary_config : _Config or None, optional
    Secondary config applied on top of the primary merge. Default is
    ``None``.

Returns
-------
SV3Pipeline
    Fully configured pipeline instance (cached between calls).

### `ProcessingService.parse_surveys(self, survey_id: 'str | None' = None, *, override: 'bool' = False, write_intermediate: 'bool' = False) -> 'None'`

Parse surveys for the active campaign and write CSVs into survey dirs.

Parameters
----------
survey_id : str or None, optional
    Restrict processing to the survey with this ID. When ``None`` all
    surveys in the campaign are processed. Default is ``None``.
override : bool, optional
    When ``True``, overwrite existing CSV files. Default is ``False``.
write_intermediate : bool, optional
    When ``True``, also write kinematic-position and IMU-position CSVs
    alongside the shotdata CSV. Default is ``False``.

Raises
------
ValueError
    If site metadata is not loaded on the session.
ValueError
    If campaign metadata is not loaded on the session.
ValueError
    If *survey_id* is specified but not found in the campaign metadata.

### `ProcessingService.run_qc(self, job: "Literal['all', 'process_qcpin', 'build_rinex', 'run_pride', 'process_kinematic', 'refine_shotdata']" = 'all', config: "'QCPipelineConfig | None'" = None) -> 'None'`

Run a ``QCPipeline`` *job* for the current scope.

Parameters
----------
job : str, optional
    Name of the pipeline job to execute. Must be one of the keys in
    ``QC_JOBS``. Default is ``"all"``.
config : QCPipelineConfig or None, optional
    Config override passed to :meth:`get_qc`. Default is ``None``.

Raises
------
AssertionError
    If *job* is not a recognised ``QC_JOBS`` key.
Exception
    Re-raises any exception produced by the pipeline job after logging
    the error.

### `ProcessingService.run_sv3(self, job: "Literal['all', 'intermediate', 'process_novatel', 'build_rinex', 'run_pride', 'process_kinematic', 'process_dfop00', 'refine_shotdata', 'process_svp']" = 'all', config: "'_Config | None'" = None, secondary_config: "'_Config | None'" = None) -> 'None'`

Run an ``SV3Pipeline`` *job* for the current scope.

Parameters
----------
job : str, optional
    Name of the pipeline job to execute. Must be one of the keys in
    ``SV3_JOBS``. Default is ``"all"``.
config : _Config or None, optional
    Primary config override passed to :meth:`get_sv3`. Default is
    ``None``.
secondary_config : _Config or None, optional
    Secondary config override passed to :meth:`get_sv3`. Default is
    ``None``.

Raises
------
AssertionError
    If *job* is not a recognised ``SV3_JOBS`` key.
Exception
    Re-raises any exception produced by the pipeline job after logging
    the error.

