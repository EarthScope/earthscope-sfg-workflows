# EarthScope Seafloor Geodesy Workflows

`earthscope-sfg-workflows` provides **workflow orchestration and data
management** for EarthScope Seafloor Geodesy (SFG) pipelines: discovery,
ingestion, preprocessing, mid-processing, modeling (GARPOS), and the
campaign/station/network lifecycle that ties them together.

It is the orchestration counterpart to
[`earthscope-sfg-tools`](https://github.com/EarthScope/earthscope-sfg-tools),
which provides the low-level parsing, file conversion, and data-model
primitives that workflows compose.

## Repository responsibility

This repository owns:

- **Data discovery and ingestion** — `data_mgmt/` ports & adapters
- **Workspace, asset catalog, and directory layout** — `workflows/workspace.py`
- **Preprocess / midprocess / model orchestration** — `workflows/pipelines/`
  and `workflows/modeling/`
- **Campaign / station / network lifecycle** — `WorkflowHandler` and
  the workspace façades

This repository depends on `earthscope-sfg-tools` for parsing, file
conversion, and core data models.

## Relationship to sibling repositories

| Repo                       | Responsibility                                          |
| -------------------------- | ------------------------------------------------------- |
| `earthscope-sfg-tools`     | Parsing + low-level processing primitives               |
| `earthscope-sfg-workflows` | Orchestration + data management + pipelines (this repo) |
| `es_sfgtools` (legacy)     | Source monorepo being split into the two above          |

See `plans/` for in-flight RFCs and PRDs.

## Installation

This repo uses [Pixi](https://pixi.sh) for environment management and is
configured to coexist with `earthscope-sfg-tools` in the same Pixi workspace.

```bash
pixi install
pixi run lint
pixi run test
```

To install dependencies for a specific Pixi environment, use
`pixi install -e <environment>`.

To enter an environment, run either `pixi shell` or
`pixi shell -e <environment>`.

Integration with GARPOS and PRIDE-PPPAR is bootstrapped via Pixi tasks:

```bash
pixi run setup        # clone + build garpos and pride
pixi run test-setup   # smoke tests for both
```

See `PIXI.md` for full Pixi details.

## Quick start

```python
from pathlib import Path
from earthscope_sfg_workflows.workflows import WorkflowHandler

# Point at your local SFGMain workspace directory.
handler = WorkflowHandler(directory=Path("/path/to/SFGMain"))

# Set the active network / station / campaign context.
handler.set_network_station_campaign("cascadia-gorda", "NCC1", "2024_A_1126")

# Discover files in the EarthScope archive and download them locally.
handler.ingest_discover_archive()
handler.download_data()

# Run the SV3 preprocessing pipeline (NovAtel → RINEX → PRIDE-PPP → shotdata).
handler.preprocess_run_pipeline_sv3()

# (Optional) Run GARPOS inversion.
handler.run_garpos()
```

The CLI is exposed as `sfgtools` (Typer-based, see `src/earthscope_sfg_cli/`):

```bash
sfgtools run path/to/manifest.json
sfgtools preprocess \
    --main-dir ./SFGMain \
    --network cascadia-gorda \
    --campaign 2024_A_1126 \
    --stations NCC1
```

### Environment variables

| Variable | Purpose |
|---|---|
| `WORKING_ENVIRONMENT` | `LOCAL` (default) or `GEOLAB` |
| `MAIN_DIRECTORY_GEOLAB` | Workspace root when running on GEOLAB |
| `S3_SYNC_BUCKET` | S3 bucket name for remote push/pull |

## Package layout

```
earthscope_sfg_workflows/
├── config/             # Environment singleton, file-type config, GARPOS settings
├── data_mgmt/          # Ports & adapters: catalog, file store, archive
│   ├── adapters/       # In-memory fakes for tests
│   ├── archives/       # EarthScope archive adapter + HTTP helpers
│   ├── catalog/        # SQLite asset catalog adapter
│   ├── filestore/      # fsspec-backed local + S3 file store
│   ├── core.py         # FileManager, LayoutInspector (orchestration over ports)
│   ├── model.py        # Immutable layout dataclasses, AssetEntry, IngestReport
│   └── ports.py        # AssetCatalogPort, FileStorePort, ArchiveSourcePort
├── logging/            # GarposLogger, ProcessLogger
├── modeling/
│   └── garpos_tools/   # GARPOS schemas, inversion handler, plotting
├── pipelines/          # SV3 and QC preprocessing pipelines + shotdata refinement
├── prefiltering/       # Shotdata QC filters (SNR, DBV, XC, distance, PRIDE WRMS)
├── services/           # IngestService, ProcessingService, SyncService
├── utils/              # CLI helpers, custom exceptions, config merging
└── workflows/          # Top-level entry points
    ├── session.py          # StationSession — scoped unit of work
    ├── workspace.py        # Workspace — multi-session container
    └── workflow_handler.py # WorkflowHandler — user-facing flat API

earthscope_sfg_cli/
├── __main__.py     # `sfgtools` Typer app
├── commands.py     # run_manifest, run_preprocessing
└── manifest.py     # PipelineManifest (JSON/YAML)
```

## Architecture

The data layer follows the **ports & adapters** (hexagonal) pattern.
Workflows interact only with three ports — `AssetCatalogPort`, `FileStorePort`,
`ArchiveSourcePort` — never with concrete implementations. Adapters live under
`data_mgmt/` subdirectories (SQLite catalog, fsspec local/S3 file store,
EarthScope archive, and in-memory fakes for tests).

`WorkflowHandler` wraps a `Workspace`, which manages a pool of
`StationSession` instances keyed by `(network, station)`. Sessions are
created once and reused on campaign switches — TileDB arrays are opened at
construction and never rebuilt. Services (`IngestService`, `ProcessingService`,
`SyncService`) are lazy properties on each session; they are constructed on
first access.

The `Environment` singleton (`config/env_config.py`) is the single source of
truth for runtime settings (`WORKING_ENVIRONMENT`, `S3_SYNC_BUCKET`,
`MAIN_DIRECTORY_GEOLAB`). Components read from it directly; no environment
variables are threaded through initializer chains.

See `plans/rfc-a-data-mgmt-ports-and-adapters.md` for design notes.

## Development

```bash
pixi run lint         # ruff check
pixi run format       # ruff format
pixi run format-check # ruff format --check
pixi run test         # pytest
```

Docstrings follow [Google style](https://google.github.io/styleguide/pyguide.html#38-comments-and-docstrings).
A repo-local converter (`dev/convert_docstrings.py`) is available for
normalizing legacy NumPy/reST docstrings.

## Docs

API reference pages are auto-generated from docstrings. Regenerate them before
building if you've changed any public interfaces:

```bash
pixi run -e docs python scripts/generate_api_md.py
```

Serve locally with live reload:

```bash
pixi run -e docs docs
```

Build static HTML (output goes to `_build/html/`):

```bash
pixi run -e docs docs-build
```

## License

See `LICENSE`.
