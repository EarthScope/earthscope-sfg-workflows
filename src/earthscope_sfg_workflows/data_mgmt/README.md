# `data_mgmt` — Ports & Adapters

Ports & adapters (hexagonal) layer for managing seafloor-geodesy data
assets. Workflows interact only with the **ports** here; concrete I/O
backends live in `adapters/`.

See `plans/rfc-a-data-mgmt-ports-and-adapters.md` for the full RFC.

## Modules

| Module           | Purpose                                                                  |
| ---------------- | ------------------------------------------------------------------------ |
| `model.py`       | Pure data classes: `AssetEntry`, `CampaignScope`, `DirectoryTree`, etc.  |
| `ports.py`       | Abstract ports: `AssetStore`, `FileStore`, `ArchiveSource`               |
| `core.py`        | Domain services: `TreeBuilder`, `Ingestor`, `LayoutInspector`            |
| `utils.py`       | Helpers (path normalization, scope serialization)                        |
| `_archive_urls.py` | URL templates for the EarthScope archive                               |
| `adapters/`      | Concrete implementations of the ports (see below)                        |

## Adapters

| Adapter                | Implements         | Backend                          |
| ---------------------- | ------------------ | -------------------------------- |
| `local_fs`             | `FileStore`        | Local filesystem                 |
| `s3_fs`                | `FileStore`        | S3 (`boto3` / `cloudpathlib`)    |
| `sql`                  | `AssetStore`       | SQLAlchemy (SQLite/Postgres)     |
| `earthscope_archive`   | `ArchiveSource`    | EarthScope public archive (HTTP) |
| `memory`               | All three          | In-memory fakes for tests        |

## Usage sketch

```python
from earthscope_sfg_workflows.data_mgmt import (
    CampaignScope, TreeBuilder, Ingestor,
)
from earthscope_sfg_workflows.data_mgmt.adapters.local_fs import LocalFileStore
from earthscope_sfg_workflows.data_mgmt.adapters.sql import SqlAssetStore

scope = CampaignScope(network="cascadia-gorda", station="NCC1",
                      campaign="2024_A_1126", survey=None)
files  = LocalFileStore(root="/path/to/SFGMain")
assets = SqlAssetStore(url="sqlite:///./SFGMain/catalog.sqlite")
tree   = TreeBuilder(files=files, root=files.root)
tree.ensure_campaign(scope)
```

## Testing

In-memory adapters in `adapters/memory.py` provide deterministic fakes
suitable for unit tests. Contract tests live in
`tests/test_data_mgmt_contracts.py`.
