# Layers and Abstractions — EarthScope SFG Workflows

> **Status**: Draft — May 2026  
> **Purpose**: Define intentional layer boundaries, call out where current code violates them, and provide a roadmap for clean-up.

---

## Table of Contents

1. [System Boundary Map](#1-system-boundary-map)
2. [The Six Layers](#2-the-six-layers)
3. [Dependency Rules](#3-dependency-rules)
4. [Layer-by-Layer Reference](#4-layer-by-layer-reference)
5. [Current Violations](#5-current-violations)
6. [Abstractions Inventory](#6-abstractions-inventory)
7. [Refactoring Roadmap](#7-refactoring-roadmap)

---

## 1. System Boundary Map

The application interacts with the outside world in three places. Everything inside these boundaries should be mediated by ports.

```
┌────────────────────────────────────────────────────────────────┐
│                   earthscope-sfg-workflows                     │
│                                                                │
│   CLI / Notebooks ──► WorkflowHandler ──► StationSession       │
│                                               │                │
│                           ┌──────────────────┼─────────┐      │
│                           │                  │         │      │
│                      AssetCatalog      FileStore   Archive     │
│                           │                  │         │      │
└───────────────────────────┼──────────────────┼─────────┼──────┘
                            ▼                  ▼         ▼
                         SQLite / DB      Local / S3   EarthScope
                                                        Archive SDK
```

**External Boundaries**:
- **Database boundary** — `AssetCatalogPort` (SQLAlchemy adapter, in-memory test adapter)
- **Filesystem / Object Storage boundary** — `FileStorePort` (fsspec adapter, in-memory test adapter)
- **Remote Archive boundary** — `ArchiveSourcePort` (EarthScope SDK adapter)

Every call that crosses a boundary goes through one of these three ports. Nothing else in the codebase should import `boto3`, `fsspec`, or the EarthScope archive SDK directly.

---

## 2. The Six Layers

```
┌──────────────────────────────────────────────────────────────┐
│  6. Interface Layer                                          │
│     CLI commands (earthscope_sfg_cli/)                       │
├──────────────────────────────────────────────────────────────┤
│  5. Orchestration / Workflow Layer                           │
│     WorkflowHandler, StationSession, Pipelines, GARPOS       │
├──────────────────────────────────────────────────────────────┤
│  4. Infrastructure / Adapter Layer                           │
│     SQL catalog, fsspec filestore, EarthScope archive        │
├──────────────────────────────────────────────────────────────┤
│  3. Domain Services Layer                                    │
│     Ingestor, LayoutInspector, filter_shotdata,              │
│     CoordTransformer, GARPOS data_prep                       │
├──────────────────────────────────────────────────────────────┤
│  2. Port Layer                                               │
│     AssetCatalogPort, FileStorePort, ArchiveSourcePort       │
├──────────────────────────────────────────────────────────────┤
│  1. Schema / Domain Model Layer                              │
│     AssetKind, AssetEntry, SFGScope, Layout dataclasses,     │
│     Pydantic filter/pipeline/GARPOS config schemas           │
└──────────────────────────────────────────────────────────────┘
```

Layer `N` may only import from layers **below** it (`N-1`, `N-2`, …). It must never import from layers above it.

---

## 3. Dependency Rules

| Rule | Rationale |
|------|-----------|
| Schema/Domain Models have **zero** internal imports | They are the shared vocabulary; everyone can depend on them |
| Ports depend **only** on domain models | Ports define contracts in terms of domain language, not infrastructure |
| Domain Services depend on **Ports + Domain Models** | Business logic is testable by passing in-memory adapters |
| Infrastructure Adapters implement Ports; they must not be imported by Domain Services | Inversion of control — adapters are plugins |
| Orchestration layer wires Services + Adapters together | It is the only place that sees both sides |
| Interface layer calls Orchestration only | CLI/notebook entry points stay thin |

---

## 4. Layer-by-Layer Reference

### Layer 1 — Schema / Domain Model Layer

**Package locations**:
```
src/earthscope_sfg_workflows/data_mgmt/model.py
src/earthscope_sfg_workflows/prefiltering/schemas.py
src/earthscope_sfg_workflows/modeling/garpos_tools/schemas.py
src/earthscope_sfg_workflows/workflows/pipelines/config.py
src/earthscope_sfg_workflows/config/shotdata_filters.py
src/earthscope_sfg_workflows/config/garpos_config.py
src/earthscope_sfg_workflows/config/file_config.py
```

**What belongs here**:
- Immutable data containers (frozen dataclasses, Pydantic models)
- Enumerations (`AssetKind`, `FileType`, survey type selectors)
- Pure value validation (Pydantic field validators with no I/O)
- Layout structural definitions (`NetworkLayout`, `StationLayout`, etc.)
- Filter/pipeline/GARPOS config schemas

**What does NOT belong here**:
- Any file I/O, database access, subprocess calls
- Business logic that makes decisions based on data
- Knowledge of how/where to load configs from (that is Layer 3/5)

**Key abstractions**:

| Symbol | File | Purpose |
|--------|------|---------|
| `AssetKind` | `data_mgmt/model.py` | Canonical taxonomy of all asset file types |
| `AssetEntry` | `data_mgmt/model.py` | Immutable record of a catalogued file |
| `SFGScope` | `data_mgmt/model.py` | Identity tuple: network→station→campaign→survey |
| `NetworkLayout` / `StationLayout` / `CampaignLayout` / `SurveyLayout` | `data_mgmt/model.py` | Pure path layouts; no I/O |
| `AcousticFilterConfig` / `PingRepliesFilterConfig` / `MaxDistFromCenterConfig` | `prefiltering/schemas.py` | Filter parameter schemas |
| `SV3PipelineConfig` / `QCPipelineConfig` | `pipelines/config.py` | Pipeline-level config trees |
| `GarposObsSchema` / `GarposSiteSchema` | `modeling/garpos_tools/schemas.py` | GARPOS I/O contracts |

---

### Layer 2 — Port Layer

**Package location**:
```
src/earthscope_sfg_workflows/data_mgmt/ports.py
```

**What belongs here**:
- Abstract `Protocol` classes (structural typing, no ABC inheritance required)
- Method signatures expressed purely in domain-model terms
- No imports from Layer 3+

**Key abstractions**:

| Port | Responsibility | Key Methods |
|------|---------------|-------------|
| `AssetCatalogPort` | Persist and query asset entries | `add()`, `update()`, `by_id()`, `by_local_path()`, `assets_for()`, `add_merge_job()`, `is_merge_complete()` |
| `FileStorePort` | Unified filesystem access (local + S3) | `exists()`, `is_file()`, `get_size()`, `list_files()`, `mkdir()`, `get_remote()`, `put_remote()` |
| `ArchiveSourcePort` | Remote archive discovery + download | `authenticate()`, `list_files()`, `download_file()` |

---

### Layer 3 — Domain Services Layer

**Package locations**:
```
src/earthscope_sfg_workflows/data_mgmt/core.py
src/earthscope_sfg_workflows/prefiltering/utils.py
src/earthscope_sfg_workflows/modeling/garpos_tools/functions.py
src/earthscope_sfg_workflows/modeling/garpos_tools/data_prep.py
src/earthscope_sfg_workflows/modeling/garpos_tools/load_utils.py
src/earthscope_sfg_workflows/utils/command_line_utils.py
src/earthscope_sfg_workflows/utils/model_update.py
```

**What belongs here**:
- Classes that accept ports in their constructors (Dependency Injection)
- All domain decision-making (filter logic, ingestion orchestration, coordinate transforms)
- No concrete adapter imports (`sqlalchemy`, `boto3`, `fsspec`, archive SDK)
- Testable with in-memory adapters

**Key abstractions**:

| Class / Function | File | Purpose |
|-----------------|------|---------|
| `Ingestor` | `data_mgmt/core.py` | Discover → detect → catalog → download pipeline over ports |
| `FileManager` | `data_mgmt/core.py` | Directory materialization via `FileStorePort` |
| `LayoutInspector` | `data_mgmt/core.py` | Filesystem introspection over pure layout dataclasses |
| `FileTypeDetector` | `data_mgmt/core.py` | Regex-based filename → `AssetKind` classification |
| `filter_shotdata()` | `prefiltering/utils.py` | Apply acoustic/ping/distance/residual filters to shotdata DataFrame |
| `CoordTransformer` | `modeling/garpos_tools/functions.py` | LLH ↔ ENU ↔ XYZ coordinate transforms (pure math) |
| `prepare_garpos_input()` | `modeling/garpos_tools/data_prep.py` | Assemble GARPOS observation + site files from TileDB |
| `validate_and_merge_config()` | `utils/model_update.py` | Deep-merge Pydantic config trees |
| `run_binary()` | `utils/command_line_utils.py` | Subprocess execution for PRIDE-PPP, conversion tools |

---

### Layer 4 — Infrastructure / Adapter Layer

**Package locations**:
```
src/earthscope_sfg_workflows/data_mgmt/adapters/memory.py
src/earthscope_sfg_workflows/data_mgmt/filestore/disk_filestore.py
src/earthscope_sfg_workflows/data_mgmt/archives/earthscope_archive.py
src/earthscope_sfg_workflows/data_mgmt/catalog/sql_asset_catalog.py
src/earthscope_sfg_workflows/config/env_config.py
```

**What belongs here**:
- Concrete implementations of the three ports
- All `import sqlalchemy`, `import fsspec`, `import boto3`, archive SDK imports
- Environment-specific configuration resolution (AWS credentials, GEOLAB vs LOCAL)
- Testing doubles (in-memory adapters)

**Key abstractions**:

| Adapter | Port Implemented | Notes |
|---------|-----------------|-------|
| `SqlAssetCatalog` | `AssetCatalogPort` | SQLAlchemy + SQLite/Postgres; ORM models internal |
| `FsspecFileStore` | `FileStorePort` | fsspec-backed; handles `s3://` and local paths transparently |
| `EarthScopeArchive` | `ArchiveSourcePort` | EarthScope SDK token auth + listing |
| `InMemoryAssetStore` | `AssetCatalogPort` | Dict-backed; for testing |
| `InMemoryFileStore` | `FileStorePort` | Dict-backed; for testing |
| `EnvConfig` | — | Detects execution environment; resolves AWS credentials |

---

### Layer 5 — Orchestration / Workflow Layer

**Package locations**:
```
src/earthscope_sfg_workflows/workflows/session.py
src/earthscope_sfg_workflows/workflows/workspace.py          (compat shim)
src/earthscope_sfg_workflows/workflows/workflow_handler.py
src/earthscope_sfg_workflows/workflows/base.py               (deprecated)
src/earthscope_sfg_workflows/workflows/pipelines/sv3_pipeline.py
src/earthscope_sfg_workflows/workflows/pipelines/qc_pipeline.py
src/earthscope_sfg_workflows/workflows/pipelines/shotdata_gnss_refinement.py
src/earthscope_sfg_workflows/workflows/midprocess/mid_processing.py
src/earthscope_sfg_workflows/workflows/modeling/garpos_handler.py
src/earthscope_sfg_workflows/workflows/preprocess_ingest/
```

**What belongs here**:
- Wiring: constructs adapters and injects them into domain services
- Scope management: sets network/station/campaign/survey context
- Pipeline sequencing: invokes domain services in order, checks merge status
- Session lifecycle: opens/closes ports, manages TileDB handle lifetimes

**This layer is the ONLY place that may**:
- Import both adapters (Layer 4) and domain services (Layer 3) simultaneously
- Call TileDB directly (TileDB is a storage backend, treated as a Layer 4 concern)
- Spawn external subprocesses for PRIDE-PPP, NovAtel conversions, etc.

**Key abstractions**:

| Class | File | Purpose |
|-------|------|---------|
| `StationSession` | `workflows/session.py` | Central context: owns ports + scope state + TileDB registry; single source of truth for a (network, station) pair |
| `WorkflowHandler` | `workflows/workflow_handler.py` | Public top-level API; manages a pool of `StationSession` objects; entry point for notebooks and CLI |
| `SV3Pipeline` | `pipelines/sv3_pipeline.py` | Sequences NovAtel → RINEX → PRIDE-PPP → KIN → DFOP00 → refined shotdata steps |
| `QCPipeline` | `pipelines/qc_pipeline.py` | Sequences QC PIN → PRIDE-PPP → KIN → refined shotdata steps |
| `IntermediateDataProcessor` | `midprocess/mid_processing.py` | Parses surveys, applies prefilters, writes GARPOS input files |
| `GarposHandler` | `modeling/garpos_handler.py` | Invokes GARPOS solver, iterates over surveys, stores results |

**Scope state model** (inside `StationSession`):
```
network  (fixed at construction)
  └── station  (fixed at construction)
        └── campaign  (mutable — switching resets survey)
              └── survey  (mutable)
```

Mutation of a parent scope cascades resets downward. This is intentional and must be preserved.

**Façade sub-objects** on `StationSession` / `Workspace`:
- `layout` → `LayoutFacade` — path construction + filesystem inspection
- `assets` → `AssetQueryFacade` — scoped catalog reads
- `ingest` → `IngestFacade` — discovery + download orchestration
- `metadata` → `MetadataFacade` — site/campaign/survey metadata cache

---

### Layer 6 — Interface Layer

**Package location**:
```
src/earthscope_sfg_cli/
```

**What belongs here**:
- Click/Typer command definitions
- Argument parsing and validation
- Calls to `WorkflowHandler` (Layer 5) only
- Human-readable output formatting

**What does NOT belong here**:
- Business logic of any kind
- Direct data access
- Config loading beyond what is needed for argument defaults

---

## 5. Current Violations

These are the known places where code crosses layer boundaries in the wrong direction. They are not bugs — they are design debt that makes the code harder to test and reason about.

### V1 — `prefiltering/utils.py` mixes domain logic with TileDB I/O

**Location**: `src/earthscope_sfg_workflows/prefiltering/utils.py`  
**Problem**: `filter_shotdata()` (Layer 3) directly queries a TileDB array for PRIDE residuals.  
TileDB is a storage adapter (Layer 4) but is being called from a domain service.  
**Impact**: `filter_shotdata()` is not testable without a real TileDB array on disk.  
**Fix**: Accept a `DataFrame` of residuals as a parameter. Let Layer 5 perform the TileDB read and pass the result down.

---

### V2 — Scope mutators duplicated across workflow classes

**Location**: `DataHandler`, `SV3Pipeline`, `QCPipeline`, `IntermediateDataProcessor`  
**Problem**: `set_network()`, `set_station()`, `set_campaign()` are re-implemented with slight variations in each class. This is copy-paste logic, not a clean abstraction.  
**Impact**: Divergence risk — one class gets an enhancement that others miss.  
**Fix**: Extract a `ScopedWorkflow` mixin (or rely on `WorkflowBase` decorators consistently) that delegates all scope mutation to `self.workspace`.

---

### V3 — `config/loadconfigs.py` instantiates filter configs by routing logic

**Location**: `src/earthscope_sfg_workflows/config/loadconfigs.py`  
**Problem**: This module both defines routing logic (which survey type maps to which preset) and acts as a factory. The routing table is Layer 1 data; the instantiation is Layer 3/5 behaviour.  
**Impact**: Minor, but callers in Layer 5 import from `config/` to get factory behaviour, blurring the line.  
**Fix**: Move the route → preset mapping to a dict in `shotdata_filters.py` (Layer 1). `loadconfigs.py` becomes a pure Layer 3 factory that calls into that table.

---

### V4 — `validate_and_merge_config()` called ad-hoc throughout Layer 5

**Location**: `workflows/workflow_handler.py`, `pipelines/sv3_pipeline.py`, `pipelines/qc_pipeline.py`  
**Problem**: Config merging (a Layer 3 utility) is invoked at many call sites in Layer 5 rather than being encapsulated at pipeline construction time.  
**Impact**: Easy to forget to call it; callers must know the merge semantics.  
**Fix**: Move the merge call into each pipeline's `__init__` or a `@classmethod from_config(...)` factory.

---

### V5 — `workspace.py` is a shim that duplicates `session.py`

**Location**: `src/earthscope_sfg_workflows/workflows/workspace.py`  
**Problem**: `Workspace` is an alias/thin wrapper over `StationSession`. Having two names for the same concept confuses which one to use.  
**Impact**: New code gets written against `Workspace`; migration target is `StationSession`.  
**Fix**: Deprecate `Workspace` with a clear `DeprecationWarning`. All new code should use `StationSession` directly. Remove `workspace.py` once external callers are updated.

---

### V6 — TileDB handle lifecycle managed across multiple classes

**Location**: `StationSession`, `SV3Pipeline`, `QCPipeline`, `IntermediateDataProcessor`  
**Problem**: TileDB arrays are opened/closed in multiple places with inconsistent patterns (some lazy, some eager).  
**Impact**: Risk of stale handles; hard to reason about when arrays are open.  
**Fix**: Centralise TileDB lifecycle in `StationSession`. Pipelines receive handles from the session, never open them directly. `TileDBRegistry` (already a frozen dataclass) is the right shape for this — just make it the single source.

---

## 6. Abstractions Inventory

This table is the authoritative list of intentional abstractions in the codebase. When adding new functionality, map it to an existing abstraction before creating a new one.

| Abstraction | Layer | Pattern | Notes |
|-------------|-------|---------|-------|
| `AssetCatalogPort` | 2 | Protocol (structural interface) | Single interface for all catalog operations |
| `FileStorePort` | 2 | Protocol (structural interface) | Unified local + S3 access |
| `ArchiveSourcePort` | 2 | Protocol (structural interface) | Remote archive; auth is part of the contract |
| `AssetKind` | 1 | Enum | Canonical file type taxonomy; replaces `FileType` |
| `SFGScope` | 1 | Frozen dataclass | Immutable identity tuple |
| `*Layout` dataclasses | 1 | Value Objects | Path-only layouts; no I/O |
| `Ingestor` | 3 | Domain Service | Port-injected; fully testable |
| `FileTypeDetector` | 3 | Strategy | Pattern-based; extensible |
| `CoordTransformer` | 3 | Domain Service | Pure math; no I/O |
| `TileDBRegistry` | 5 | Frozen dataclass | Immutable snapshot of open TileDB handles |
| `StationSession` | 5 | Context Object | Single source of truth for scope + ports |
| `WorkflowHandler` | 5 | Façade | Public API; session pool manager |
| Pipeline classes | 5 | Command / Step Sequence | Each pipeline encapsulates one end-to-end data flow |
| `WorkflowBase` | 5 | ABC (deprecated) | Being replaced by direct `StationSession` composition |
| `validate_and_merge_config()` | 3 | Utility | Config merging; should be called at construction |

---

## 7. Refactoring Roadmap

These are ordered by impact vs. effort. Items at the top are highest leverage.

### Phase 1 — Enforce boundary hygiene (no public API change)

| # | Task | Violation Fixed | Effort |
|---|------|-----------------|--------|
| 1.1 | Extract residuals DataFrame param from `filter_shotdata()` | V1 | Small |
| 1.2 | Deprecate `Workspace` with `DeprecationWarning`, redirect to `StationSession` | V5 | Trivial |
| 1.3 | Move merge table to `shotdata_filters.py`; make `loadconfigs.py` a pure factory | V3 | Small |

### Phase 2 — Reduce duplication (minor internal refactoring)

| # | Task | Violation Fixed | Effort |
|---|------|-----------------|--------|
| 2.1 | Extract `ScopedWorkflow` mixin with single `set_*` delegation to `workspace` | V2 | Medium |
| 2.2 | Move `validate_and_merge_config()` call into pipeline `__init__` | V4 | Small |

### Phase 3 — Centralise TileDB lifecycle (structural change)

| # | Task | Violation Fixed | Effort |
|---|------|-----------------|--------|
| 3.1 | Make `TileDBRegistry` the only way to access TileDB handles | V6 | Medium |
| 3.2 | Pipelines receive `TileDBRegistry` from `StationSession`; do not open arrays | V6 | Medium |

### Phase 4 — Clean up compatibility shims

| # | Task | Notes | Effort |
|---|------|-------|--------|
| 4.1 | Remove `WorkflowBase` ABC once all subclasses migrated to direct composition | After Phase 2 | Small |
| 4.2 | Remove `workspace.py` shim | After V5 deprecation period | Trivial |
| 4.3 | Remove `FileType` enum; fully migrate to `AssetKind` | After all call sites updated | Small |
| 4.4 | Remove backwards-compat `current_*_name` property aliases on workflow classes | After external callers updated | Trivial |

---

## Appendix — Quick Dependency Check

If you are unsure whether an import is legal, use this decision tree:

```
Is the import from a lower-numbered layer?
  YES → Allowed
  NO  → Is it within the same layer?
    YES → Allowed (but consider if coupling is intentional)
    NO  → VIOLATION — find an alternative (inject it, move it, or split the module)
```

Common pitfall: Layer 3 domain services importing from `workflows/` (Layer 5) — this never happens in the current codebase but would be the worst class of violation if introduced.
