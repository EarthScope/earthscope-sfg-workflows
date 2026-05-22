# EarthScope SFG Workflows

`earthscope-sfg-workflows` provides **workflow orchestration and data management** for EarthScope Seafloor Geodesy (SFG) pipelines: discovery, ingestion, preprocessing, mid-processing, modeling (GARPOS), and the campaign/station/network lifecycle that ties them together.

It is the orchestration counterpart to [`earthscope-sfg-tools`](https://github.com/EarthScope/earthscope-sfg-tools), which provides the low-level parsing, file conversion, and data-model primitives that workflows compose.

---

## What's in this repo

| Area | Description |
| --- | --- |
| `data_mgmt/` | Ports & adapters: asset catalog, file store, archive access |
| `workflows/workspace.py` | Workspace, asset catalog, and directory layout |
| `pipelines/` | SV3 and QC preprocessing pipelines, shotdata refinement |
| `modeling/garpos_tools/` | GARPOS inversion schemas, handler, and plotting |
| `services/` | Ingest, processing, and sync services |
| `workflows/workflow_handler.py` | `WorkflowHandler` — the user-facing flat API |

---

## Quick start

```python
from pathlib import Path
from earthscope_sfg_workflows.workflows import WorkflowHandler

handler = WorkflowHandler(directory=Path("/path/to/SFGMain"))
handler.set_network_station_campaign("cascadia-gorda", "NCC1", "2024_A_1126")

handler.ingest_discover_archive()
handler.download_data()
handler.preprocess_run_pipeline_sv3()
handler.run_garpos()
```

The CLI is exposed as `sfgtools`:

```bash
sfgtools run path/to/manifest.json
sfgtools preprocess \
    --main-dir ./SFGMain \
    --network cascadia-gorda \
    --campaign 2024_A_1126 \
    --stations NCC1
```

---

## Docs sections

- [**Installation**](installation.md) — environment setup with Pixi, GARPOS, and PRIDE-PPPAR
- [**Development**](development.md) — linting, testing, and contributing
- [**API Reference**](api/index.md) — auto-generated module documentation

---

## Architecture

The data layer follows the **ports & adapters** (hexagonal) pattern. Workflows interact only with three ports — `AssetCatalogPort`, `FileStorePort`, `ArchiveSourcePort` — never with concrete implementations.

`WorkflowHandler` wraps a `Workspace`, which manages a pool of `StationSession` instances keyed by `(network, station)`. Services (`IngestService`, `ProcessingService`, `SyncService`) are lazy properties on each session, constructed on first access.

The `Environment` singleton (`config/env_config.py`) is the single source of truth for runtime settings.

---

## Environment variables

| Variable | Purpose |
| --- | --- |
| `WORKING_ENVIRONMENT` | `LOCAL` (default) or `GEOLAB` |
| `MAIN_DIRECTORY_GEOLAB` | Workspace root when running on GEOLAB |
| `S3_SYNC_BUCKET` | S3 bucket name for remote push/pull |
