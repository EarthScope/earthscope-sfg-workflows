# Architecture

> **Rendering:** Diagrams use [Mermaid](https://mermaid.js.org) syntax. GitHub, VS Code (with the _Markdown Preview Mermaid Support_ extension), JetBrains IDEs, and the [Mermaid Live Editor](https://mermaid.live) all render them natively.

Two perspectives: **[API user](#view-1--api-user-how-a-campaign-gets-processed)** covers the method calls and data flow a user observes; **[contributor](#view-2--contributor-internal-component-structure)** covers the internal layer boundaries, port interfaces, and adapter wiring.

---

## View 1 — API User: How a Campaign Gets Processed

A typical workflow script calls several logical phases in order through a single `WorkflowHandler` entry point. Data can come from the EarthScope archive (remote path) or from files already on disk (local path) — both converge at the pipeline step.

```mermaid
flowchart LR
    U(["Script / Notebook"])

    subgraph WH["WorkflowHandler — single entry point"]
        direction TB
        A["1 · WorkflowHandler('/data/sfg')"]
        B["2 · set_network_station_campaign\n('NET', 'STA', '2024_A_1')"]

        subgraph INGEST["3 · Ingest  — choose one path"]
            direction LR
            C["3a · ingest_discover_archive()\n+ download_data()\n— fetch from EarthScope archive"]
            C2["3b · ingest_add_local_data(path)\n— catalog files already on disk"]
        end

        E["4 · preprocess_run_pipeline_sv3()"]
        F["5 · midprocess_prep_garpos()"]
        G["6 · modeling_run_garpos()"]
        H["7 · modeling_plot_garpos_results()"]
    end

    ES[("EarthScope Archive\nhttps://data.earthscope.org")]
    DISK[("Local filesystem\nraw files already on disk")]
    TDB[("TileDB arrays\nkin · shotdata · acoustic")]
    PRIDE(["PRIDE-PPP\nkinematic solver"])
    GP(["GARPOS\nacoustic inversion"])
    OUT["Plots + result CSVs\n.png  /GARPOS/results/"]

    U --> A --> B --> INGEST
    C <-->|"list & catalog remote URLs\nthen authenticated HTTP download"| ES
    C2 <-->|"scan & catalog local files"| DISK
    INGEST -->|"raw files on disk, cataloged"| E
    E <-->|"NovAtel→RINEX→kin positions"| PRIDE
    E -->|"rectified shotdata"| TDB
    TDB --> F
    F -->|"filtered shotdata + SVP"| G
    G <-->|"acoustic travel-time inversion"| GP
    G --> H
    H --> OUT

    style WH fill:#f8f9fa,stroke:#6c757d
    style INGEST fill:#f1f8ff,stroke:#90caf9
    style ES fill:#e3f2fd,stroke:#1565C0
    style DISK fill:#e3f2fd,stroke:#1565C0
    style TDB fill:#e8f5e9,stroke:#2E7D32
    style PRIDE fill:#fff3e0,stroke:#E65100
    style GP fill:#fff3e0,stroke:#E65100
    style OUT fill:#f3e5f5,stroke:#6A1B9A
```

### What each step does

| Step | Method | Produces |
|------|--------|----------|
| 1 | `WorkflowHandler(directory)` | Workspace rooted at `directory`; reads `MAIN_DIRECTORY_GEOLAB` env var if `None` |
| 2 | `set_network_station_campaign()` | Creates campaign & station directories; loads site metadata from archive |
| 3a | `ingest_discover_archive()` + `download_data(kinds=…)` | Catalogs remote URLs then downloads raw files (NovAtel, Sonardyne, CTD, …) into `campaign/raw/` |
| 3b | `ingest_add_local_data(path)` | Scans a local directory and catalogs every recognized file — use when raw data is already on disk |
| 4 | `preprocess_run_pipeline_sv3()` | Converts NovAtel → RINEX, runs PRIDE-PPP, builds kinematic + refined shotdata TileDB arrays |
| 5 | `midprocess_prep_garpos()` | Parses survey shotdata CSVs and applies pre-filters (SNR, DBV, XC, distance, PRIDE WRMS) |
| 6 | `modeling_run_garpos()` | Runs GARPOS inversion; writes results to `campaign/processed/<survey>/GARPOS/results/` |
| 7 | `modeling_plot_*()` | Writes diagnostic plots (residuals, time-series) to the GARPOS results directory |

---

## View 2 — Contributor: Internal Component Structure

The package follows a **ports-and-adapters** (hexagonal) pattern. The domain core never touches the filesystem, database, or network directly — all I/O is delegated through three port interfaces (highlighted in yellow). In tests, adapters are replaced with in-memory fakes; in production, real implementations are injected at `Workspace` construction.

```mermaid
flowchart TB
    %% ── Entry points ────────────────────────────────────────────────────
    subgraph ENTRY["Entry Points"]
        WFH["WorkflowHandler\n(notebooks & scripts)"]
        CLI["sfgtools CLI\n(Typer — run / preprocess)"]
    end

    %% ── Config ──────────────────────────────────────────────────────────
    ENV(["Environment singleton\nWORKING_ENVIRONMENT\nS3_SYNC_BUCKET · MAIN_DIRECTORY_GEOLAB"])

    %% ── Orchestration ───────────────────────────────────────────────────
    subgraph ORCH["Orchestration Layer"]
        WS["Workspace\nsession pool keyed by (network, station)\ncontrols port injection"]
        SS["StationSession\nnetwork + station fixed at construction\ncampaign + survey mutable slots\nTileDB arrays opened once"]
    end

    %% ── Services ────────────────────────────────────────────────────────
    subgraph SVC["Service Layer  — lazy @property on StationSession"]
        IS["IngestService\nlocal scan\nremote discover\ndownload (HTTP + S3)"]
        PS["ProcessingService\nSV3Pipeline execution\nQCPipeline execution\nparse_surveys"]
        SY["SyncService\npush station + campaign → S3\npull ← S3"]
    end

    %% ── Modeling ────────────────────────────────────────────────────────
    GH["GarposHandler\nprepare shotdata\nrun GARPOS inversion\nplot results"]

    %% ── Pipelines ───────────────────────────────────────────────────────
    subgraph PIPE["Pipeline Layer"]
        SV3["SV3Pipeline\nNovAtel → RINEX → PRIDE-PPP → shotdata TileDB"]
        QCP["QCPipeline\nQC-PIN → RINEX → PRIDE-PPP → shotdata TileDB"]
    end

    %% ── Domain ──────────────────────────────────────────────────────────
    subgraph DOM["Domain Layer  — pure, no I/O"]
        FM["FileManager + LayoutInspector\nmaterialises DirectoryTree on a FileStorePort"]
        MDL["model.py\nAssetEntry · AssetKind · SFGScope\nDirectoryTree · Layout dataclasses\nIngestReport · FileInfo · ArchiveFile"]
    end

    %% ── Ports ───────────────────────────────────────────────────────────
    subgraph PORTS["Port Interfaces  — dependency-inversion boundary"]
        CAP{{"AssetCatalogPort\nadd · update · by_id\nassets_for · distinct_values"}}
        FSP{{"FileStorePort\nexists · mkdir · get_remote\nput_remote · list_files"}}
        ASP{{"ArchiveSourcePort\nlist_files · download_file\nauthenticate"}}
    end

    %% ── Adapters ────────────────────────────────────────────────────────
    subgraph ADAPT["Production Adapters"]
        SQL["AssetCatalog\ncatalog.sqlite  (SQLAlchemy ORM)"]
        FSS["FsspecFileStore\nlocal path  or  s3://bucket  (fsspec + s3fs)"]
        ESA["EarthScopeArchive\nhttps://data.earthscope.org\nBearer token via EarthScope SDK"]
        MEM["InMemory adapters\n(test fakes — swapped in via Workspace constructor)"]
    end

    %% ── External ────────────────────────────────────────────────────────
    subgraph EXT["External Systems"]
        PRIDE_E(["PRIDE-PPP\nkinematic solver"])
        GP_E(["GARPOS solver"])
        S3_E[("AWS S3\npush/pull processed data")]
        TDB_E[("TileDB arrays\nacoustic · kin · shotdata · gnss_obs")]
        ARCH_E[("EarthScope Seafloor Archive\nhttps://…")]
    end

    %% ── Edges ───────────────────────────────────────────────────────────
    WFH & CLI --> WS
    WS -.->|"reads at construction"| ENV
    WS --> SS
    WFH --> GH
    GH --> SS

    SS --> IS & PS & SY

    IS --> CAP & FSP & ASP
    SY --> FSP
    PS --> SV3 & QCP
    SV3 & QCP --> FM
    FM --> FSP

    CAP -. "implements" .-> SQL & MEM
    FSP -. "implements" .-> FSS & MEM
    ASP -. "implements" .-> ESA & MEM

    IS & PS & GH & FM & SY -.-> MDL

    ESA --> ARCH_E
    FSS -.->|"when root is s3://"| S3_E
    SV3 & QCP --> PRIDE_E
    GH --> GP_E
    SV3 & QCP & GH --> TDB_E

    style PORTS fill:#fff9c4,stroke:#f9a825,color:#000
    style ADAPT fill:#e8f5e9,stroke:#2E7D32
    style EXT fill:#f3e5f5,stroke:#6A1B9A
    style ENTRY fill:#e3f2fd,stroke:#1565C0
    style DOM fill:#fafafa,stroke:#9e9e9e
```

### Layer responsibilities at a glance

| Layer | Classes | Can it do I/O? |
|-------|---------|----------------|
| Entry Points | `WorkflowHandler`, `sfgtools` CLI | No — delegates everything |
| Orchestration | `Workspace`, `StationSession` | No — holds ports, never calls them |
| Service | `IngestService`, `ProcessingService`, `SyncService` | Yes — through ports only |
| Modeling | `GarposHandler` | Yes — calls TileDB + filesystem directly |
| Pipelines | `SV3Pipeline`, `QCPipeline` | Yes — calls PRIDE-PPP, TileDB |
| Domain | `FileManager`, `model.py` | `FileManager` yes (via FileStorePort); `model.py` never |
| Port interfaces | `AssetCatalogPort`, `FileStorePort`, `ArchiveSourcePort` | Protocol definitions — no implementation |
| Adapters | `AssetCatalog`, `FsspecFileStore`, `EarthScopeArchive` | Yes — this is where real I/O happens |

---

## Key design decisions (for contributors)

**Ports-and-adapters boundary.** All three ports are Protocols decorated with `@runtime_checkable`. Pass in-memory fakes at `Workspace(catalog=..., files=..., archive=...)` construction for fully hermetic unit tests — no disk, no network, no SQLite.

**`Environment` singleton.** `WorkflowHandler` and `Workspace` read `WORKING_ENVIRONMENT`, `S3_SYNC_BUCKET`, and `MAIN_DIRECTORY_GEOLAB` from the `Environment` singleton at construction time. No environment variable is threaded through initializer chains — components call the singleton directly when they need a value.

**Session identity.** `StationSession.network` and `StationSession.station` are fixed at construction. TileDB arrays are opened once and held for the session lifetime. Switching campaigns (via `set_campaign`) only touches directory creation and metadata resolution — it never rebuilds arrays.

**Lazy services.** `IngestService`, `ProcessingService`, and `SyncService` are constructed on first access via `@property` on `StationSession`. This keeps construction cheap and avoids circular imports between the session and service modules.

**Pure domain.** `model.py` contains only frozen dataclasses and pure path-math. No imports from services, adapters, or external libraries beyond `upath` and `datetime`. Every layout (`CampaignLayout`, `TileDBLayout`, …) can be instantiated and inspected in tests with zero side-effects.
