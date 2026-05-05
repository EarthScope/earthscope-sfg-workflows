# RFC B — Replace `WorkflowABC` with `Workflow` / `CampaignWorkflow` Facade

> Status: proposed
> Phase order: **second** (depends on RFC A's pure data layer)
> Related: [RFC A — Data Management Ports & Adapters](rfc-a-data-mgmt-ports-and-adapters.md)

## Problem

The workflow layer has a 6-class inheritance tangle around
[src/earthscope_sfg_workflows/workflows/utils/protocols.py](../src/earthscope_sfg_workflows/workflows/utils/protocols.py)::`WorkflowABC`:

- `WorkflowHandler`, `DataHandler`, `IntermediateDataProcessor`,
  `GarposHandler`, `SV3Pipeline`, `QCPipeline` all inherit from
  `WorkflowABC`. Each duplicates `current_network_*`, `current_station_*`,
  `current_campaign_*`, `current_survey_*` flat attributes plus the matching
  `*_dir` / `*_metadata` triples — fifteen-plus mutable fields per instance.
- `WorkflowHandler` also *composes* the other five and forwards 20+ method
  calls. Two inheritance paths to the same context, kept in sync by hand.
- Hierarchical state mutates via `set_network_station_campaign(...)` and is
  guarded by `@validate_network_station_campaign` decorators that check only
  "non-None" — they cannot detect "wrong context for this call" or "you
  forgot to run prep first."
- A `WorkflowContext` value object exists alongside the flat attributes;
  both representations are kept and neither is canonical.
- The CLI ([src/earthscope_sfg_cli/commands.py](../src/earthscope_sfg_cli/commands.py))
  drives every pipeline by `wfh.set_network_station_campaign(...)` followed
  by a forwarded method call. The pattern is repeated four times across
  `run_manifest`, `run_preprocessing`, `run_midprocess_pipeline`,
  `run_modeling`. None of this code is unit-tested.

Adding a new pipeline stage today requires deciding whether to inherit
`WorkflowABC` or add a method to `WorkflowHandler`, then duplicating context
plumbing and re-deriving the decorator chain. There is no clear entry point
for tests — every class needs `DirectoryHandler`, `PreProcessCatalogHandler`,
and TileDB initialization at construction time.

## Proposed Interface

A two-level facade. Construction is once; context is captured *immutably* per
campaign call; pipelines run as plain methods or via uniform job objects.

### Top level (`workflows/__init__.py`)

```python
class Workflow:
    """Owns workspace-scoped resources. Construct once per process."""

    def __init__(
        self,
        directory: Path | str | None = None,
        *,
        catalog: AssetStore | None = None,
        files: FileStore | None = None,
        archive: ArchiveSource | None = None,
        s3_sync_bucket: str | None = None,
    ) -> None:
        """All deps from RFC A are injectable. Sensible local defaults."""

    def for_campaign(
        self,
        network: str,
        station: str,
        campaign: str,
        *,
        survey: str | None = None,
    ) -> "CampaignWorkflow":
        """Capture an immutable scope. Returns a fresh fluent handle."""

    def list_networks(self) -> list[str]: ...
    def list_stations(self, network: str) -> list[str]: ...
    def list_campaigns(self, network: str, station: str) -> list[str]: ...
```

### Campaign level (`workflows/campaign.py`)

```python
class CampaignWorkflow:
    """Fluent, immutable, scoped-to-one-campaign API.

    Every fluent method returns ``self`` to support chaining.
    A uniform ``run(job)`` method exists for manifest-driven callers.
    """

    scope: CampaignScope          # frozen; constructor argument

    # --- Uniform job runner (preferred for manifest / batch) ---
    def run(self, job: WorkflowJob) -> JobResult: ...

    # --- Fluent convenience (preferred for notebooks / scripts) ---
    def ingest(self, *, directory: Path | None = None) -> "CampaignWorkflow": ...
    def ingest_from_archive(
        self,
        *,
        remote_uris: list[str] | None = None,
        kinds: list[AssetKind] | None = None,
        download: bool = True,
    ) -> "CampaignWorkflow": ...
    def run_sv3(
        self,
        *,
        job: SV3Stage = SV3Stage.ALL,
        config: SV3PipelineConfig | None = None,
        secondary_config: SV3PipelineConfig | None = None,
    ) -> "CampaignWorkflow": ...
    def run_qc(
        self,
        *,
        job: QCStage = QCStage.ALL,
        config: QCPipelineConfig | None = None,
    ) -> "CampaignWorkflow": ...
    def prep_garpos(
        self,
        *,
        survey: str | None = None,
        custom_filters: dict | None = None,
        override: bool = False,
    ) -> "CampaignWorkflow": ...
    def run_garpos(
        self,
        *,
        survey: str | None = None,
        run_id: str = "Test",
        iterations: int = 1,
        override: bool = False,
        custom_settings: InversionParams | None = None,
    ) -> "CampaignWorkflow": ...

    # --- Read-only inspection ---
    def list_surveys(self) -> list[str]: ...
    def latest_garpos_run(self, survey: str | None = None) -> str | None: ...
```

### Job dataclasses (`workflows/jobs.py`)

```python
@dataclass(frozen=True)
class IngestJob:
    scope: CampaignScope
    local_directory: Path | None = None
    remote_uris: list[str] | None = None
    kinds: list[AssetKind] | None = None
    download: bool = True

@dataclass(frozen=True)
class PreprocessJob:
    scope: CampaignScope
    pipeline: Literal["sv3", "qc"] = "sv3"
    stage: SV3Stage | QCStage = SV3Stage.ALL
    primary_config: dict | None = None
    secondary_config: dict | None = None

@dataclass(frozen=True)
class MidprocessJob:
    scope: CampaignScope
    action: Literal["parse_surveys", "prep_garpos"] = "prep_garpos"
    survey: str | None = None
    custom_filters: dict | None = None
    override: bool = False
    write_intermediate: bool = False

@dataclass(frozen=True)
class ModelJob:
    scope: CampaignScope
    survey: str | None = None
    iterations: int = 1
    run_id: str = "Test"
    override: bool = False
    custom_settings: InversionParams | None = None

WorkflowJob = IngestJob | PreprocessJob | MidprocessJob | ModelJob

@dataclass(frozen=True)
class JobResult:
    job: WorkflowJob
    success: bool
    output: object = None
    error: BaseException | None = None
```

### Usage example — rewritten `cli/commands.py::run_manifest`

```python
def run_manifest(manifest):
    wf = Workflow(directory=manifest.main_directory)

    for j in manifest.ingestion_jobs:
        scope = CampaignScope(j.network, j.station, j.campaign)
        wf.for_campaign(*scope.tuple).run(IngestJob(scope, local_directory=j.directory))

    for j in manifest.download_jobs:
        scope = CampaignScope(j.network, j.station, j.campaign)
        urls = list_campaign_files(**j.model_dump())
        wf.for_campaign(*scope.tuple).run(
            IngestJob(scope, remote_uris=urls, download=True)
        )

    for j in manifest.process_jobs:
        scope = CampaignScope(j.network, j.station, j.campaign)
        wf.for_campaign(*scope.tuple).run(
            PreprocessJob(scope, stage=j.job_type,
                          primary_config=j.global_config,
                          secondary_config=j.secondary_config)
        )

    for j in manifest.garpos_jobs:
        scope = CampaignScope(j.network, j.station, j.campaign)
        cfg  = validate_and_merge_config(j.global_config, j.secondary_config)
        cw   = wf.for_campaign(*scope.tuple)
        cw.run(MidprocessJob(scope, action="prep_garpos",
                             custom_filters=cfg.filter_config and cfg.filter_config.model_dump(),
                             override=cfg.override))
        for survey in (j.surveys or [None]):
            cw.run(ModelJob(scope, survey=survey, run_id=cfg.run_id,
                            iterations=cfg.iterations, override=cfg.override,
                            custom_settings=cfg.inversion_params))
```

### Usage example — notebook / script (fluent)

```python
(Workflow("/data")
   .for_campaign("CAS", "NCB1", "2026_S")
   .ingest(directory=Path("./incoming"))
   .ingest_from_archive(kinds=[AssetKind.NOVATEL, AssetKind.RINEX2])
   .run_sv3()
   .prep_garpos()
   .run_garpos(run_id="v1", iterations=2))
```

## Dependency Strategy

**Category: in-process** orchestration over the **local-substitutable +
ports-&-adapters** infrastructure delivered by RFC A.

`Workflow` is the single composition root. Its constructor performs the only
DI in the system:

```python
class Workflow:
    def __init__(self, directory=None, *, catalog=None, files=None,
                 archive=None, s3_sync_bucket=None):
        self._tree     = DirectoryTree(root=Path(directory or env_root()))
        self._files    = files   or LocalFileStore()
        self._catalog  = catalog or default_catalog(self._tree, self._files)
        self._archive  = archive or EarthScopeArchive()
        self._builder  = TreeBuilder(self._tree, self._files)
        self._ingestor = Ingestor(self._catalog, self._files, self._archive,
                                  FileTypeDetector(), self._tree)
        self._builder.ensure_workspace()
```

`CampaignWorkflow` holds **immutable** references to the same shared
infrastructure plus a frozen `CampaignScope`. Pipeline implementations
(`SV3Pipeline`, `QCPipeline`, `IntermediateDataProcessor`, `GarposHandler`)
**stop inheriting `WorkflowABC`** and become plain classes (or modules) that
take `(scope, deps)` as parameters:

```python
def run_sv3_pipeline(
    scope: CampaignScope,
    *,
    deps: WorkflowDeps,           # tree, files, catalog, archive
    config: SV3PipelineConfig,
    stage: SV3Stage = SV3Stage.ALL,
) -> SV3Result: ...
```

`WorkflowDeps` is a small frozen dataclass bundling the four ports plus the
`DirectoryTree` and `Ingestor`. It is the only object passed across the
campaign-workflow / pipeline boundary.

`WorkflowABC`, `WorkflowContext`, and the `validate_*` decorators are
**deleted**. There is no replacement — invariants are encoded by:

- `CampaignScope` being frozen (you cannot put a campaign in an invalid
  state).
- `Workflow.for_campaign(...)` validating the scope exists or creating it
  fail-fast at construction time.
- Pipeline functions taking `scope: CampaignScope` as a required argument
  (you cannot call them without a scope).

## Testing Strategy

### New boundary tests to write

- `Workflow` construction with all RFC A in-memory adapters: assert
  workspace materializes, catalog is queryable, and `for_campaign` returns a
  `CampaignWorkflow` whose scope is frozen.
- `CampaignWorkflow.run(IngestJob(...))` end-to-end with `FakeArchive`
  seeded with a campaign worth of files: asserts catalog rows, skipped
  count, and idempotent re-run.
- `CampaignWorkflow.run(PreprocessJob(stage=SV3Stage.PROCESS_NOVATEL))`
  with stub TileDB / `earthscope_sfg_tools` fakes: asserts shotdata-pre
  TileDB array is written; covers QC pipeline threading paths similarly.
- `CampaignWorkflow.run(ModelJob(...))` with a fake GARPOS solver
  (`load_lib` returns a stub): asserts result-processing and plot-output
  paths.
- Fluent-chain test: same flow as the manifest test, expressed as
  `.for_campaign(...).ingest(...).run_sv3(...).run_garpos(...)`. Asserts
  parity with the job-driven path.
- `cli/commands.py::run_manifest` test: feed a synthetic manifest, assert
  on the captured `JobResult` sequence.

### Old tests to delete

- All `WorkflowABC` / `validate_network_station_campaign` decorator tests —
  none exist today, so deletion is trivial.
- Smoke import tests stay.

### Test environment needs

- All RFC A in-memory adapters.
- A `FakeTileDBStore` and a `FakeEarthScopeSFGTools` introduced in this RFC,
  to mock the cross-package boundary that pipelines call into. These mocks
  replace `earthscope_sfg_tools.tiledb_integration`,
  `earthscope_sfg_tools.novatel_tools`, `earthscope_sfg_tools.sonardyne_tools`
  at the module-import level (or via a thin `WorkflowDeps.tiledb` /
  `.novatel` / `.sonardyne` attribute that callers go through).
- `pytest-mock` for the GARPOS subprocess seam.

## Implementation Recommendations

- **`Workflow` owns**: the workspace root, the four ports (catalog, files,
  archive, optional S3 sync bucket), the `DirectoryTree`, the `Ingestor`,
  and a metadata cache. Nothing else is workspace-scoped state.
- **`CampaignWorkflow` owns**: an immutable `CampaignScope` plus references
  to the parent `Workflow`'s deps. It is cheap to construct, and a fresh
  instance per `for_campaign(...)` call is the right design — there is no
  per-campaign mutable state worth caching across calls.
- **Pipelines own**: their own algorithms. They take `(scope, deps,
  config)` as plain arguments. They do not subclass anything.
- **What to expose**: `Workflow`, `CampaignWorkflow`, `CampaignScope`, the
  four `*Job` classes, `JobResult`, `SV3Stage` / `QCStage` enums, and
  `WorkflowDeps`. Nothing else from `workflows/` is public.
- **What to hide**: TileDB array lifecycle, logger redirection, metadata
  loading, S3 sync timing, decorator-style validation, the existence of the
  individual pipeline classes.
- **Caller migration**: every existing import from
  `earthscope_sfg_workflows.workflows.workflow_handler`,
  `earthscope_sfg_workflows.workflows.preprocess_ingest.data_handler`,
  `earthscope_sfg_workflows.workflows.midprocess.mid_processing`,
  `earthscope_sfg_workflows.workflows.modeling.garpos_handler`,
  `earthscope_sfg_workflows.workflows.pipelines.sv3_pipeline`,
  `earthscope_sfg_workflows.workflows.pipelines.qc_pipeline`, and
  `earthscope_sfg_workflows.workflows.utils.protocols` is rewritten to
  import from `earthscope_sfg_workflows.workflows`. The old modules become
  internal (`_pipelines/`, `_garpos/`, etc.).
- **CLI rewrite**: each of `run_preprocessing`, `run_midprocess_pipeline`,
  `run_modeling`, `run_manifest` collapses to a `Workflow(...)` construction
  + a `for-loop / .run(job)` body. Estimated reduction: ~150 → ~40 lines in
  [src/earthscope_sfg_cli/commands.py](../src/earthscope_sfg_cli/commands.py).
- **Dead code removal**: while we're here, delete the
  fully-commented-out
  [src/earthscope_sfg_workflows/workflows/pipelines/sv2_ops.py](../src/earthscope_sfg_workflows/workflows/pipelines/sv2_ops.py)
  and the duplicate `get_survey_filter_config()` in
  [src/earthscope_sfg_workflows/workflows/midprocess/utils.py](../src/earthscope_sfg_workflows/workflows/midprocess/utils.py).

## Phasing inside this RFC

1. Land `WorkflowDeps`, `CampaignScope`, the `*Job` dataclasses, and the
   `SV3Stage`/`QCStage` enums. Pure data; no behavior. Compiles in
   isolation.
2. Land `Workflow` and `CampaignWorkflow` shells that *delegate to the
   existing `WorkflowHandler`* internally. CLI is migrated to the new
   facade; behavior is unchanged. This proves the interface against real
   callers before touching internals.
3. Convert each pipeline class (`SV3Pipeline`, `QCPipeline`,
   `IntermediateDataProcessor`, `GarposHandler`) to a plain
   `(scope, deps, config) -> result` function/class. Rewire
   `CampaignWorkflow` to call them directly. Delete `WorkflowHandler`,
   `DataHandler`, and `WorkflowABC`.
4. Delete decorators, `WorkflowContext`, dead `sv2_ops.py`, duplicate
   `get_survey_filter_config`.
5. Land the test suite from the Testing Strategy section. Aim for
   `CampaignWorkflow.run(job)` paths at ~70% coverage minimum.
