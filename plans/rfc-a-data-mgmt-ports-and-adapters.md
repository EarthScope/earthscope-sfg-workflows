# RFC A — Deepen `data_mgmt/` via Ports & Adapters

> Status: proposed
> Phase order: **first** (foundation for RFC B)
> Related: [RFC B — Workflow Facade](rfc-b-workflow-facade.md)

## Problem

The `data_mgmt/` package today is split into three subpackages with thin
`handler.py` + `schemas.py` + (`tables.py` | `config.py`) trios. The end-to-end
data lifecycle — *archive discovery → file pull → catalog rows → directory
materialization* — is invisible because it is scattered across nine files and
glued together externally inside `DataHandler` / `WorkflowHandler`.

Concrete shallow-module symptoms:

- `data_mgmt/assetcatalog/handler.py` (`PreProcessCatalogHandler`) is a thin
  SQLAlchemy CRUD wrapper exposing raw SQL (`get_dtype_counts` runs a
  hand-written aggregation; every method opens its own engine context). The
  interface is nearly as wide as the implementation.
- `data_mgmt/directorymgmt/schemas.py` defines Pydantic models
  (`NetworkDir`, `StationDir`, `CampaignDir`, `SurveyDir`, `GARPOSSurveyDir`,
  `TileDBDir`) that *embed I/O* via `.build()`, `.find_rectified_shotdata()`,
  `.is_garpos_directory()`, `.load_from_path()`. Data shape and side effects
  are co-mingled, defeating both unit testing and serialization-without-disk.
- `data_mgmt/ingestion/archive_pull.py` directly imports `EarthScopeClient`,
  `boto3`, and `requests` and calls them inline from business logic. There is
  no seam at the external-service boundary.
- `AssetType` is duplicated between
  [src/earthscope_sfg_workflows/config/file_config.py](../src/earthscope_sfg_workflows/config/file_config.py)
  and
  [src/earthscope_sfg_workflows/data_mgmt/assetcatalog/schemas.py](../src/earthscope_sfg_workflows/data_mgmt/assetcatalog/schemas.py).
- Adding a new directory tier or asset kind requires changes across four
  files in two subpackages.

The catalog database is on its way to being deployed in the cloud against an
**Amazon RDS Postgres** instance instead of a local SQLite file. SQLite stays
as the local-development / test backing. This makes the catalog interface a
**genuine port boundary**, not a speculative one.

## Proposed Interface

Three explicit ports. The deep module owns all domain logic; transports are
injected. Pure dataclasses replace Pydantic-with-I/O.

### Pure data layer (`data_mgmt/model.py`)

```python
class AssetKind(Enum):
    """Single source of truth. Removes the duplicate in config/file_config.py."""
    NOVATEL, NOVATEL770, NOVATEL000, DFOP00, SONARDYNE, RINEX2, KIN,
    SEABIRD, CTD, LEVERARM, MASTER, QCPIN, NOVATELPIN, KINPOSITION,
    ACOUSTIC, SITECONFIG, ATDOFFSET, SVP, SHOTDATA, IMUPOSITION,
    KINRESIDUALS, GNSSOBSTDB, BCOFFLOAD


@dataclass(frozen=True)
class CampaignScope:
    network: str
    station: str
    campaign: str
    survey: str | None = None  # only required for GARPOS-shaped operations


@dataclass(frozen=True)
class AssetEntry:
    """Pure data. No I/O methods."""
    id: int | None
    kind: AssetKind
    scope: CampaignScope
    local_path: Path | None = None
    remote_path: str | None = None
    remote_type: str | None = None
    is_processed: bool = False
    timestamp_data_start: datetime | None = None
    timestamp_data_end: datetime | None = None
    timestamp_created: datetime | None = None
    parent_id: int | None = None

    def is_addressable(self) -> bool:
        return bool(self.local_path or self.remote_path)


@dataclass(frozen=True)
class DirectoryTree:
    """Pure path math for the entire workspace hierarchy."""
    root: Path

    def network_dir(self, network: str) -> Path: ...
    def station_dir(self, scope: CampaignScope) -> Path: ...
    def campaign_dir(self, scope: CampaignScope) -> Path: ...
    def survey_dir(self, scope: CampaignScope) -> Path: ...
    def tiledb(self, scope: CampaignScope) -> "TileDBLayout": ...
    def garpos(self, scope: CampaignScope) -> "GARPOSLayout": ...

@dataclass(frozen=True)
class TileDBLayout: ...   # pure paths only
@dataclass(frozen=True)
class GARPOSLayout: ...   # pure paths only
```

### Ports (`data_mgmt/ports.py`)

```python
@runtime_checkable
class AssetStore(Protocol):
    """Persistence port. SQLite (local), Postgres/RDS (cloud), in-memory (tests)."""
    def add(self, asset: AssetEntry) -> AssetEntry: ...        # returns id-assigned copy
    def update(self, asset: AssetEntry) -> bool: ...
    def assets_for(self, scope: CampaignScope, kind: AssetKind | None = None) -> list[AssetEntry]: ...
    def by_id(self, asset_id: int) -> AssetEntry | None: ...
    def by_local_path(self, path: Path) -> list[AssetEntry]: ...
    def delete(self, scope: CampaignScope, kind: AssetKind | None = None) -> int: ...
    def count_by_kind(self, scope: CampaignScope) -> dict[AssetKind, int]: ...
    def close(self) -> None: ...


@runtime_checkable
class FileStore(Protocol):
    """Filesystem/object-store port. Local, S3 (via cloudpathlib), in-memory (tests)."""
    def exists(self, path: Path) -> bool: ...
    def is_file(self, path: Path) -> bool: ...
    def is_dir(self, path: Path) -> bool: ...
    def list_files(self, directory: Path, recursive: bool = False) -> list[FileInfo]: ...
    def read_bytes(self, path: Path) -> bytes: ...
    def write_bytes(self, path: Path, data: bytes) -> None: ...
    def mkdir(self, path: Path, parents: bool = True) -> None: ...
    def remove(self, path: Path) -> bool: ...
    def get_size(self, path: Path) -> int | None: ...
    def close(self) -> None: ...


@runtime_checkable
class ArchiveSource(Protocol):
    """External archive port. EarthScope SDK + boto3 (prod), Fake (tests)."""
    def list_files(self, directory_url: str) -> list[ArchiveFile]: ...
    def download_file(self, file_url: str, dest_path: Path) -> None: ...
    def authenticate(self, profile: str | None = None) -> bool: ...
    def close(self) -> None: ...


class ArchiveError(Exception): ...
class ArchiveAuthError(ArchiveError): ...
class ArchiveNotFoundError(ArchiveError): ...
```

### Domain core (`data_mgmt/core.py`)

```python
class FileTypeDetector:
    """Stateless filename → AssetKind. Pure function."""
    def detect(self, filename: str) -> AssetKind | None: ...


class TreeBuilder:
    """Materializes pure DirectoryTree paths against a FileStore."""
    def __init__(self, tree: DirectoryTree, files: FileStore): ...
    def ensure_workspace(self) -> None: ...
    def ensure_campaign(self, scope: CampaignScope) -> None: ...
    def ensure_garpos_survey(self, scope: CampaignScope) -> None: ...


class Ingestor:
    """End-to-end: discover → detect → catalog → optionally download.

    Logic lives here; all I/O is delegated to ports.
    """
    def __init__(
        self,
        catalog: AssetStore,
        files: FileStore,
        archive: ArchiveSource,
        detector: FileTypeDetector,
        tree: DirectoryTree,
    ): ...

    def ingest_local(
        self, scope: CampaignScope, source_dir: Path,
    ) -> IngestReport: ...

    def discover_archive(
        self, scope: CampaignScope, archive_url: str,
    ) -> IngestReport: ...

    def download(
        self,
        scope: CampaignScope,
        kinds: list[AssetKind] | None = None,
    ) -> IngestReport: ...


@dataclass(frozen=True)
class IngestReport:
    cataloged: int
    downloaded: int
    skipped: int
    errors: list[str]
```

### Public facade (`data_mgmt/__init__.py`)

```python
from .model import AssetKind, AssetEntry, CampaignScope, DirectoryTree
from .ports import AssetStore, FileStore, ArchiveSource, ArchiveError
from .core import Ingestor, TreeBuilder, FileTypeDetector, IngestReport
from .adapters import (
    SqliteAssetStore, PostgresAssetStore,
    LocalFileStore, S3FileStore,
    EarthScopeArchive, FakeArchive, InMemoryAssetStore, InMemoryFileStore,
)
```

### Usage example

```python
# Production (cloud, RDS Postgres)
catalog = PostgresAssetStore(dsn=os.environ["RDS_DSN"])
files   = S3FileStore(bucket="es-sfg-cloud")
archive = EarthScopeArchive(profile="prod")

tree = DirectoryTree(root=Path("/mnt/data"))
ingestor = Ingestor(catalog, files, archive, FileTypeDetector(), tree)
TreeBuilder(tree, files).ensure_workspace()

scope = CampaignScope("CAS", "NCB1", "2026_S")
TreeBuilder(tree, files).ensure_campaign(scope)
report = ingestor.ingest_local(scope, source_dir=Path("/incoming"))
report = ingestor.download(scope, kinds=[AssetKind.NOVATEL, AssetKind.RINEX2])

# Local dev (SQLite + local fs + real archive)
catalog = SqliteAssetStore(db_path=tree.root / "catalog.sqlite")
files   = LocalFileStore()
ingestor = Ingestor(catalog, files, EarthScopeArchive(), FileTypeDetector(), tree)

# Tests (no I/O at all)
ingestor = Ingestor(
    catalog=InMemoryAssetStore(),
    files=InMemoryFileStore(),
    archive=FakeArchive(seeded={"http://...": [b"...payload..."]}),
    detector=FileTypeDetector(),
    tree=DirectoryTree(root=Path("/virtual")),
)
```

## Dependency Strategy

**Category: Ports & Adapters (mixed local-substitutable + true-external).**

| Concern | Port | Production adapter | Local adapter | Test adapter |
|---|---|---|---|---|
| Catalog persistence | `AssetStore` | `PostgresAssetStore` (RDS) | `SqliteAssetStore` | `InMemoryAssetStore` |
| Filesystem / object store | `FileStore` | `S3FileStore` (cloudpathlib) | `LocalFileStore` | `InMemoryFileStore` |
| External archive | `ArchiveSource` | `EarthScopeArchive` (SDK + boto3 + requests) | same | `FakeArchive` |

`Postgres` and `Sqlite` adapters share a SQLAlchemy ORM definition (existing
`tables.py` is preserved as the schema for both). The differences are dialect
URL, connection pooling, and migration story — both adapters implement the
same `AssetStore` Protocol. Schema migrations are handled by Alembic; this
RFC introduces the migration baseline.

`InMemory*` adapters are first-class: every test in this package's test suite
must work without touching disk, network, or DB.

## Testing Strategy

**Replace, don't layer.** New boundary tests at the `Ingestor` /
`AssetStore` / `TreeBuilder` interfaces; delete the old shallow CRUD tests
once they're written (none exist today, so deletion is mostly trivial).

### New boundary tests to write

- `Ingestor.ingest_local` end-to-end with `InMemoryFileStore` +
  `InMemoryAssetStore`: writes to the file store, expects N catalog rows of
  expected kinds, verifies skipped/error counts.
- `Ingestor.discover_archive` with `FakeArchive` seeded with URL → bytes:
  expects catalog rows with `remote_path` set, `local_path` unset.
- `Ingestor.download` flips `local_path` from None → real path; idempotent on
  retry.
- `AssetStore` contract test: parameterized over `[InMemoryAssetStore,
  SqliteAssetStore, PostgresAssetStore]` (Postgres in CI via testcontainers
  or `pytest-postgresql`). Same assertions over insert / query / count /
  delete semantics.
- `TreeBuilder.ensure_campaign` with `InMemoryFileStore`: asserts the
  expected set of paths exists.
- `FileTypeDetector.detect` parameterized over the full filename catalog
  (~30 cases); pure unit test.
- Pure-data tests for `DirectoryTree` path math, `AssetEntry.is_addressable`.

### Old tests to delete

None — current `tests/` only holds smoke `test_package_import.py` and
`test_submodule_imports.py`. We keep import smoke tests but expect the
boundary suite to grow to ~80% coverage of `data_mgmt/`.

### Test environment needs

- `pytest-postgresql` or `testcontainers-python` for the Postgres contract
  test (CI-only; local dev uses SQLite).
- No filesystem fixtures required outside `InMemoryFileStore`.

## Implementation Recommendations

Durable guidance, decoupled from current paths:

- **`data_mgmt/` owns**: the asset taxonomy, the directory tree shape, the
  catalog schema, the file-discovery logic, the archive-pull control flow.
- **`data_mgmt/` exposes**: `DirectoryTree`, `Ingestor`, `TreeBuilder`,
  `AssetCatalog`-style queries (via the `AssetStore` port), `AssetKind`,
  `CampaignScope`, `AssetEntry`, plus the three Protocols and the bundled
  adapters. Nothing else is public.
- **`data_mgmt/` hides**: SQLAlchemy session lifecycle, Alembic migrations,
  EarthScope token refresh, boto3/cloudpathlib client construction, S3
  vs. local path coercion, Pydantic JSON serialization of the tree (becomes
  internal to a single `tree_io` module).
- **Caller migration**: every existing import from
  `data_mgmt.assetcatalog.handler`, `data_mgmt.directorymgmt.handler`,
  `data_mgmt.directorymgmt.schemas`, `data_mgmt.ingestion.archive_pull`, and
  `data_mgmt.ingestion.datadiscovery` is rewritten to import from
  `data_mgmt` (the package facade). The three subpackages become internal
  modules (`_catalog/`, `_tree/`, `_archive/`) with leading underscore to
  signal privacy. Ruff/ban-imports rule is added in
  [pyproject.toml](../pyproject.toml) to enforce this.
- **Removing the duplicate `AssetType`**:
  [src/earthscope_sfg_workflows/config/file_config.py](../src/earthscope_sfg_workflows/config/file_config.py)
  re-exports `AssetKind as AssetType` for one release, then is deleted.
- **Migration of the catalog DB**: introduce Alembic with one baseline
  revision matching current `tables.py`. Postgres rollout is a config flip
  (`AssetStore` injection point), not a code change for callers.

## Phasing inside this RFC

1. Land `model.py` (pure dataclasses) + `ports.py` (Protocols) + `core.py`
   (`Ingestor`, `TreeBuilder`, `FileTypeDetector`) without removing existing
   code. New module compiles in isolation.
2. Land `adapters/` with `InMemory*`, `Local*`, `Sqlite*`, `Fake*`. Land the
   contract test suite parameterized over implementations.
3. Land `Postgres*` adapter + Alembic baseline + CI testcontainers job.
4. Migrate callers (`workflows/preprocess_ingest/data_handler.py`,
   `workflows/workflow_handler.py`, CLI) to the facade. Old subpackages
   become aliasing re-exports for one release.
5. Delete old code and re-exports.
