# `workflows` — Orchestration

Top-level orchestration layer. Owns the `Workspace`, wires the data-mgmt
ports into façades, and exposes domain workflows (preprocess, midprocess,
modeling, QC).

See `plans/rfc-b-workflow-facade.md` for the design.

## Layers

| Module                 | Purpose                                                           |
| ---------------------- | ----------------------------------------------------------------- |
| `base.py`              | `WorkflowBase` — base class for any workflow that needs a `Workspace`. |
| `workspace.py`         | `Workspace` — current scope (network/station/campaign/survey) + façades. |
| `facades.py`           | `LayoutFacade`, `MetadataFacade`, `AssetsFacade`, `IngestFacade`. |
| `workflow_handler.py`  | `WorkflowHandler` — backwards-compatible high-level entry point.  |

## Subpackages

| Path                  | Purpose                                                         |
| --------------------- | --------------------------------------------------------------- |
| `pipelines/`          | QC, SV2 ops, SV3, shotdata GNSS refinement, plotting            |
| `midprocess/`         | Mid-processing helpers between preprocess and modeling          |
| `modeling/`           | `garpos_handler.py` — GARPOS run management                     |
| `preprocess_ingest/`  | `data_handler.py` — discovery + ingestion façade                |

## Façades

Subclasses of `WorkflowBase` interact with the data layer **only** through
the four façades exposed on `self.workspace`:

```python
self.workspace.layout    # LayoutFacade — paths, materialization
self.workspace.metadata  # MetadataFacade — site / station metadata
self.workspace.assets    # AssetsFacade — read/write asset catalog
self.workspace.ingest    # IngestFacade — discover + register
```

Each façade is a frozen dataclass constructed on every property access. It
captures the workspace's *current* scope at construction time and never
drifts if the caller mutates scope between calls.

## Quick example

```python
from pathlib import Path
from earthscope_sfg_workflows.workflows import WorkflowHandler

handler = WorkflowHandler(directory=Path("/path/to/SFGMain"))
handler.workspace.set_scope(
    network="cascadia-gorda", station="NCC1", campaign="2024_A_1126",
)
handler.data_handler.discover()
handler.data_handler.ingest()
```
