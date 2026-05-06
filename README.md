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

handler = WorkflowHandler(directory=Path("/path/to/SFGMain"))
handler.workspace.set_scope(
    network="cascadia-gorda",
    station="NCC1",
    campaign="2024_A_1126",
)

# Discover and ingest assets for the active campaign.
handler.data_handler.discover()
handler.data_handler.ingest()
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

## Package layout

```
earthscope_sfg_workflows/
├── config/         # Static config: file types, GARPOS site, filter levels
├── data_mgmt/      # Ports & adapters: AssetStore, FileStore, ArchiveSource
│   └── adapters/   # local_fs, s3_fs, sql, earthscope_archive, memory (tests)
├── logging/        # Process-aware loggers (ProcessLogger, BaseLogger)
├── modeling/
│   └── garpos_tools/   # GARPOS schemas, data prep, plotting helpers
├── prefiltering/   # Shotdata QC filters (SNR, DBV, XC, distance, WRMS, …)
├── utils/          # CLI helpers, custom warnings, model_update
└── workflows/      # Top-level orchestration
    ├── base.py             # WorkflowBase
    ├── facades.py          # Layout / Metadata / Assets / Ingest façades
    ├── workspace.py        # Workspace = scope + façades
    ├── workflow_handler.py # Backwards-compatible legacy entry point
    ├── pipelines/          # qc, sv2_ops, sv3, shotdata_gnss_refinement
    ├── midprocess/         # Mid-processing helpers
    ├── modeling/           # garpos_handler
    └── preprocess_ingest/  # data_handler

earthscope_sfg_cli/
├── __main__.py     # `sfgtools` Typer app
├── commands.py     # run_manifest, run_preprocessing
└── manifest.py     # PipelineManifest (JSON/YAML)
```

## Architecture

The data layer follows the **ports & adapters** (hexagonal) pattern.
Workflows interact only with the ports — `AssetStore`, `FileStore`,
`ArchiveSource` — never with concrete implementations. Adapters live under
`data_mgmt/adapters/` (local FS, S3, SQL catalog, EarthScope archive, plus
in-memory fakes for tests).

`WorkflowHandler` owns a single `Workspace`, which exposes four façades —
`layout`, `metadata`, `assets`, `ingest` — to subclasses of `WorkflowBase`.
Façades are constructed per access against the workspace's *current* scope,
so they never drift if scope mutates between calls.

See `plans/rfc-a-data-mgmt-ports-and-adapters.md` and
`plans/rfc-b-workflow-facade.md` for design notes.

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

## License

See `LICENSE`.
