# PRD — `WorkflowBase` + Data-Mgmt Facades (Phase 4 of RFC A)

> Companion to [rfc-a-data-mgmt-ports-and-adapters.md](../rfc-a-data-mgmt-ports-and-adapters.md).
> Locks Phase 4 ("migrate callers") interface decisions before code is written.

## Problem Statement

The `data_mgmt/` ports & adapters layer (`AssetStore`, `FileStore`, `ArchiveSource`, `Ingestor`, `TreeBuilder`, `DirectoryTree`, frozen `AssetEntry`) is in place, but every workflow caller still imports and operates against the old shallow handlers (`PreProcessCatalogHandler`, `DirectoryHandler`) and Pydantic-with-I/O directory schemas (`NetworkDir`, `GARPOSSurveyDir`, …). As a result:

- The new ports are dead weight — nothing in `workflows/` depends on them.
- Callers mutate `AssetEntry.is_processed = True` and `entry.timestamp_data_end = ...` directly on what should be a pure value object.
- `WorkflowABC` carries a 12-field `WorkflowContext` (network/station/campaign/survey × name/dir/metadata) that duplicates `CampaignScope`.
- `list_campaign_files(...)` composes EarthScope archive URLs at the call site instead of in the domain core.
- `GARPOSSurveyDir.find_rectified_shotdata()`, `is_garpos_directory()`, `load_from_path()` keep I/O glued to data shape, defeating the whole point of the new layer.

A single-caller proof-of-concept is impossible: `WorkflowABC` is the hub through which six pipeline subclasses share state, so the migration must land as one coordinated change. Shim layers preserving the old API are explicitly rejected — the legacy types must become deletable.

## Solution

Introduce a new minimal `WorkflowBase` that owns ports + scope state, plus four navigable façades that constitute the *only* way subclasses interact with the data layer:

- `workspace.layout.*` — directory paths and materialization
- `workspace.metadata.*` — lazy-loaded Site/Campaign/Survey metadata
- `workspace.assets.*` — scoped catalog queries + writeback
- `workspace.ingest.*` — discover/ingest/download orchestration

Subclasses (`WorkflowHandler`, `DataHandler`, `GarposHandler`, `IntermediateDataProcessor`, `QCPipeline`, `SV3Pipeline`) inherit from `WorkflowBase` (preserves current threading/shared-TileDB patterns) but reach the data layer **only** through `self.workspace.*` façades. Direct access to `self.catalog` / `self.files` / `self.archive` is not exposed; raw ports stay encapsulated.

Three new domain primitives are added under `data_mgmt/` to make this possible without leaking I/O into call sites:

- `Ingestor.discover_campaign(scope)` — composes the four canonical EarthScope archive URLs (raw, metadata, RINEX 1Hz, RINEX 10Hz) inside the domain core; replaces `list_campaign_files`.
- `LayoutInspector(files)` — `FileStore`-backed I/O queries over pure layout dataclasses (`is_garpos_directory`, `find_rectified_shotdata`, etc.). Replaces I/O methods on `GARPOSSurveyDir` / `NetworkDir`.
- `AssetEntry` stays frozen. The `assets` façade exposes an `update(entry, **changes)` helper that returns the new entry and writes through `AssetStore.update`.

After migration, `data_mgmt/assetcatalog/`, `data_mgmt/directorymgmt/`, and `data_mgmt/ingestion/` are deleted. The duplicate `AssetType` in `config/file_config.py` is removed.

## User Stories

1. As a workflow author, I want a single `WorkflowBase` constructor signature, so that I do not have to thread `directory`, `s3_sync_bucket`, and `asset_catalog` through every subclass.
2. As a workflow author, I want to write `self.workspace.assets.assets(AssetKind.QCPIN)`, so that scoped catalog queries read like prose and require no `CampaignScope` plumbing at the call site.
3. As a workflow author, I want a single `workspace.layout.campaign()` call to obtain campaign paths, so that I do not navigate `current_network_dir.stations[...]campaigns[...]`.
4. As a workflow author, I want `workspace.layout.ensure_campaign()` to materialize the directory and return paths, so that I never call `DirectoryHandler.build()` or `*Dir.build()` separately.
5. As a workflow author, I want frozen `AssetEntry`, so that two threads cannot silently disagree about `is_processed`.
6. As a workflow author, I want `workspace.assets.update(entry, is_processed=True)` to return a new `AssetEntry` and persist atomically, so that the writeback contract is explicit.
7. As a mid-process workflow author, I want `workspace.metadata.site()` to return `None` until I call `workspace.load_site_metadata(site)`, so that metadata loading is explicit and traceable.
8. As a CLI operator, I want `workspace.ingest.discover_campaign()` to take only the active scope, so that the four EarthScope archive URLs are composed in one place.
9. As a CLI operator, I want `workspace.ingest.local(source_dir)` and `workspace.ingest.download(kinds=[...])`, so that ingestion is one method call regardless of source.
10. As a test author, I want to construct `WorkflowBase(workspace=Workspace(...with InMemory adapters...))`, so that no test touches disk, network, or DB.
11. As a test author, I want `Workspace.for_test(network=..., station=..., campaign=...)`, so that scope setup in tests is one line.
12. As a GARPOS pipeline author, I want `workspace.layout.garpos_survey()` to return a pure `GARPOSLayout` and `workspace.inspector.is_garpos_directory(layout)` to do the I/O check, so that path math and I/O stay separate.
13. As a GARPOS pipeline author, I want `workspace.inspector.find_rectified_shotdata(layout)` to work over any `FileStore` (local, S3, in-memory), so that the same code runs in production and in tests.
14. As a maintainer, I want `data_mgmt/assetcatalog/`, `data_mgmt/directorymgmt/`, and `data_mgmt/ingestion/` removed at the end of the migration, so that there is exactly one way to do each thing.
15. As a maintainer, I want `AssetType` removed from `config/file_config.py`, so that `AssetKind` is the single source of truth.
16. As a maintainer, I want a Ruff `tidy-imports` ban on `earthscope_sfg_workflows.data_mgmt.{assetcatalog,directorymgmt,ingestion}`, so that the deleted subpackages cannot regrow.
17. As a workflow author, I want `set_network()`, `set_station()`, `set_campaign()`, `set_survey()` to validate hierarchy and cascade resets, so that invalid scope state cannot be observed.
18. As a workflow author, I want `workspace.scope` to return a `CampaignScope` or raise on incomplete scope, so that pipeline functions never receive partial identifiers.
19. As a CLI operator, I want `set_network_station_campaign(network, station, campaign)` as a single call, so that the common case is one line.
20. As a maintainer, I want every façade method to be type-annotated against a `Protocol` defined in `data_mgmt`, so that swapping `Postgres` / `SQLite` / `InMemory` is a config change.

## Implementation Decisions

### Modules to add or modify

- `data_mgmt/core.py` — extend `Ingestor` with `discover_campaign(scope) -> IngestReport`. The four EarthScope URL templates move from `data_mgmt/ingestion/archive_pull.py` into a private `data_mgmt/_archive_urls.py` consumed by `Ingestor`. URL composition is no longer caller-visible.
- `data_mgmt/core.py` — add `LayoutInspector(files: FileStore)` with at minimum `is_garpos_directory(layout)`, `find_rectified_shotdata(layout)`, `find_master_xml(layout)`. Methods take pure layout dataclasses (`GARPOSLayout`, `CampaignLayout`) and use `FileStore.list_files` / `exists`. No imports of the legacy `*Dir` classes.
- `data_mgmt/__init__.py` — re-export `LayoutInspector` and the new `Ingestor.discover_campaign`. No other surface changes.
- `workflows/base.py` (new) — `WorkflowBase` class. Constructor: `(workspace: Workspace, *, mid_process: bool = False)`. No port properties, no directory-handler properties. Exposes only `self.workspace`, `self.mid_process_workflow` (read-only).
- `workflows/workspace.py` (new) — `Workspace` class. Holds ports, current scope, and metadata cache. Exposes:
  - Scope mutators: `set_network`, `set_station`, `set_campaign`, `set_survey`, `set_network_station_campaign`, with cascade-reset semantics matching today's `WorkflowContext`.
  - Scope reader: `scope -> CampaignScope` (raises if incomplete), plus `network_name`, `station_name`, `campaign_name`, `survey_name` properties.
  - Metadata loaders: `load_site_metadata(site)`, `load_campaign_metadata(campaign)`, `load_survey_metadata(survey)`.
  - Façade properties (each returns a fresh frozen façade dataclass on access): `layout`, `metadata`, `assets`, `ingest`. Allocation is intentional and cheap.
  - Resource management: `close()`, `__enter__`/`__exit__`.
  - Test factory: `Workspace.for_test(*, root=..., network=..., station=..., campaign=..., survey=None)` constructing a workspace with `InMemoryAssetStore` + `InMemoryFileStore` + `FakeArchive`.
- `workflows/facades.py` (new) — four frozen façade dataclasses:
  - `LayoutFacade(_tree, _builder, _inspector, _scope)` — `network()`, `station()`, `campaign()`, `tiledb_station()`, `garpos_survey()`, plus `ensure_*` variants. Read-only path I/O queries delegate to `_inspector`.
  - `MetadataFacade(_site, _campaign, _survey)` — `site()`, `campaign()`, `survey()`. Returns `None` until loaded.
  - `AssetQueryFacade(_catalog, _scope)` — `assets(kind=None)`, `by_id`, `by_local_path`, `count_by_kind`, `update(entry, **changes)`. The `update` helper is the only sanctioned write path; it constructs a `replace(...)` and calls `_catalog.update`.
  - `IngestFacade(_ingestor, _scope)` — `local(source_dir)`, `discover_campaign()`, `discover_archive(url)`, `download(kinds=None)`.
- `workflows/utils/protocols.py` — `WorkflowABC` is replaced by an alias `WorkflowABC = WorkflowBase` for one release, then deleted. `WorkflowContext` is deleted. Decorators `validate_network_station_campaign` and `validate_network_station` are rewritten to read `self.workspace.scope` and remain importable.
- `workflows/workflow_handler.py`, `workflows/preprocess_ingest/data_handler.py`, `workflows/modeling/garpos_handler.py`, `workflows/midprocess/mid_processing.py`, `workflows/pipelines/qc_pipeline.py`, `workflows/pipelines/sv3_pipeline.py`, `workflows/pipelines/plotting.py` — all rewritten to use `self.workspace.*` façades. Direct `self.asset_catalog`, `self.directory_handler`, `self.current_*_dir`, `self.current_*_metadata` references are eliminated.
- `earthscope_sfg_cli/commands.py` — replace `list_campaign_files` with `workspace.ingest.discover_campaign()`.
- `config/workspace.py` — replace deferred `NetworkDir` imports with `DirectoryTree` + `LayoutInspector` usage.
- `config/file_config.py` — `AssetType` deleted (was already aliased internally to `AssetKind`).

### Modules to delete (end of migration)

- `data_mgmt/assetcatalog/` (handler, schemas, tables — the SQLAlchemy ORM definitions move to `data_mgmt/adapters/sql.py` if not already there)
- `data_mgmt/directorymgmt/` (handler, schemas, config)
- `data_mgmt/ingestion/` (archive_pull, datadiscovery, config — URL templates relocated to `data_mgmt/_archive_urls.py`)
- `data_mgmt/utils.py` deletion or relocation depending on contents
- All references in `tests/test_submodule_imports.py`

### Architectural decisions

- **Inheritance retained.** `WorkflowBase` is a class, not a function. Pipelines that share threaded TileDB writers via `self` continue to work. The functional/orchestrator design (Design 3 from `/design-an-interface`) is deferred.
- **Façades are the only public seam.** Subclasses must not access `workspace._catalog`, `workspace._files`, `workspace._archive` directly. Internal-use only. Enforce via leading underscore and Ruff `_private` rule on the workflows package.
- **Frozen `AssetEntry` is not relaxed.** Every mutation site is rewritten to go through `workspace.assets.update(entry, ...)`.
- **Façades are constructed per-access.** Each property call allocates a small frozen dataclass. No caching. This keeps `Workspace` mutation safe (façades observe the current scope at construction time).
- **Cascade resets live in `Workspace`, not subclasses.** `set_station(...)` clears campaign+survey+campaign_metadata+survey_metadata. Site metadata is preserved across station changes only when the station id is unchanged; otherwise it is also cleared.
- **Metadata is explicit, never auto-loaded.** `mid_process_workflow=True` means subclasses are *expected* to call `workspace.load_site_metadata(...)`; the base does not silently load Site JSON during `set_station()`.
- **No async retrofit.** Methods stay synchronous to match today's pipelines.
- **Ports stay in `data_mgmt/ports.py`.** `Workspace` is in `workflows/` because it is workflow-specific (knows about metadata types from `earthscope-sfg-tools`). Keeps `data_mgmt/` standalone-importable.
- **Postgres/Alembic remain Phase 3 work**, out of scope for this PRD.

### API contracts (sketch)

- `Workspace.scope` raises `ValueError` if any of network/station/campaign is unset. The error message names the missing level. Existing decorators are rewritten in terms of this property.
- `LayoutFacade.garpos_survey()` raises `ValueError` if `scope.survey is None`. `ensure_garpos_survey()` does the same and additionally calls `TreeBuilder.ensure_garpos_survey`.
- `AssetQueryFacade.update(entry, **changes) -> AssetEntry` returns the persisted entry. If no rows were updated, raises `LookupError` with the asset id.
- `IngestFacade.discover_campaign() -> IngestReport` requires a complete scope; uses `Ingestor.discover_campaign` which composes raw, metadata, RINEX 1Hz, RINEX 10Hz URLs and merges results.
- `LayoutInspector` methods accept pure layouts and return `Path`, `bool`, or `list[Path]`. Never raise on missing files; return `None`/`False`/`[]` instead. Exceptions are reserved for `FileStore`-level failures.

## Testing Decisions

A good test in this codebase exercises **observable behavior at the façade boundary** — never reaches into `workspace._catalog` or asserts on internal state. Every test runs without disk, network, or DB by using `Workspace.for_test()`.

Modules to test:

- `Workspace` — scope-cascade-reset semantics, metadata-clear semantics, `scope` property error path, `for_test` factory.
- `LayoutFacade` — path returns match `DirectoryTree` for the active scope; `ensure_*` methods materialize via `InMemoryFileStore` and a follow-up `files.is_dir(...)` check observes the result.
- `AssetQueryFacade` — `assets(kind=...)` filters; `update(entry, is_processed=True)` returns a new frozen entry whose subsequent `assets_for(...)` query reflects the change; `update` of a deleted entry raises `LookupError`.
- `IngestFacade` — `discover_campaign()` against a `FakeArchive` seeded with four URL→bytes mappings produces the expected `IngestReport.cataloged` count and asset kinds; idempotent on retry.
- `LayoutInspector` — parameterized tests over `[InMemoryFileStore, LocalFileStore (tmp_path)]` for `is_garpos_directory` and `find_rectified_shotdata`. Same assertions across both adapters.
- `WorkflowBase` smoke — instantiate with `Workspace.for_test()`, call a façade method, assert no port leaks (`workspace._catalog` is not a public attribute).
- One end-to-end migration test per pipeline subclass: instantiate the subclass with `Workspace.for_test()`, drive a minimal happy-path scenario, assert observable side effects (catalog rows, materialized files) — not internal state.

Prior art:

- `tests/test_data_mgmt_contracts.py` already parameterizes adapter contract tests over `[InMemoryAssetStore, SqlAssetStore]`. The same pattern extends to `LayoutInspector` and the new façades.
- `tests/test_data_mgmt_adapters_prod.py` covers production-only adapters; remains unchanged.

The legacy `tests/test_submodule_imports.py` entries for the deleted subpackages are removed as part of the migration; replacement smoke tests assert the new façade modules import cleanly.

## Out of Scope

- Postgres adapter rollout, Alembic baseline, CI testcontainers job (Phase 3 of RFC A; tracked separately).
- Async/functional pipeline refactor of `QCPipeline` and `SV3Pipeline` (Design 3 from the interface exploration).
- Threading and queue redesign inside `QCPipeline` — preserved as-is, just rewired to read assets through `workspace.assets`.
- RFC B (Workflow Facade) — depends on this work but is its own document.
- Renaming `WorkflowHandler` or any user-facing CLI surface.
- TileDB array lifecycle changes beyond moving instantiation calls to use the new façades.

## Further Notes

- Migration ordering inside the PR: (1) land `Workspace`, façades, `LayoutInspector`, `Ingestor.discover_campaign` with tests; (2) rewrite subclasses one file per commit, each commit green; (3) delete legacy subpackages and `AssetType` duplicate; (4) update import-ban Ruff rule.
- Estimated diff: ~250 LOC new (`workspace.py` + `facades.py` + `LayoutInspector`), ~300 LOC mechanical rewrites across 8 caller files, ~150 LOC test additions, ~600 LOC deletions in legacy subpackages. Net negative.
- Decorators `validate_network_station_campaign` / `validate_network_station` are kept (they're a useful guardrail) but rewired to `self.workspace.scope`.
- The existing `WorkflowContext` dataclass is deleted; the public `current_*_name` getter/setter properties on `WorkflowHandler` are deleted in the same PR (callers already migrated).
- After this PRD lands, RFC A's Phase 4 is complete and Phase 5 (delete old code + remove re-exports) is folded into the same PR. There is no transitional alias period — the legacy imports become hard errors.
