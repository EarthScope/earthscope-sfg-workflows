# Environments

> **Rendering:** Diagrams use [Mermaid](https://mermaid.js.org) syntax. GitHub, VS Code (with the _Markdown Preview Mermaid Support_ extension), JetBrains IDEs, and the [Mermaid Live Editor](https://mermaid.live) all render them natively.

The package supports two named environments: **LOCAL** (the default) for developer laptops and local clusters, and **GEOLAB** for the managed EarthScope cloud compute environment. The active environment is read from the `WORKING_ENVIRONMENT` environment variable at startup.

---

## Environment detection

`Environment.load_working_environment()` is called once before any `WorkflowHandler` or `Workspace` is constructed. It reads three environment variables and populates the class-level `Environment` singleton.

```mermaid
flowchart TD
    START(["Process start"])
    LOAD["Environment.load_working_environment()"]
    CHECK_ENV{"WORKING_ENVIRONMENT\n env var?"}
    LOCAL_SET["_working_environment = LOCAL\n(default)"]
    GEOLAB_SET["_working_environment = GEOLAB"]
    CHECK_MD{"MAIN_DIRECTORY_GEOLAB\nenv var set?"}
    SET_MD["_main_directory_GEOLAB = value"]
    WARN_MD["⚠ warn: MAIN_DIRECTORY_GEOLAB not set"]
    CHECK_S3{"S3_SYNC_BUCKET\nenv var set?"}
    SET_S3["_s3_sync_bucket = value"]
    WARN_S3["⚠ warn: S3_SYNC_BUCKET not set"]
    DONE(["Environment singleton ready"])

    START --> LOAD --> CHECK_ENV
    CHECK_ENV -->|"'LOCAL' or missing"| LOCAL_SET
    CHECK_ENV -->|"'GEOLAB'"| GEOLAB_SET
    GEOLAB_SET --> CHECK_MD
    CHECK_MD -->|"set"| SET_MD --> CHECK_S3
    CHECK_MD -->|"missing"| WARN_MD --> CHECK_S3
    LOCAL_SET --> CHECK_S3
    CHECK_S3 -->|"set"| SET_S3 --> DONE
    CHECK_S3 -->|"missing"| WARN_S3 --> DONE

    style WARN_MD fill:#fff3e0,stroke:#E65100
    style WARN_S3 fill:#fff3e0,stroke:#E65100
```

---

## Workspace root resolution

The root directory determines where all local data (raw files, TileDB arrays, GARPOS results) is stored. Resolution order is the same for both environments, but GEOLAB pre-populates `MAIN_DIRECTORY_GEOLAB` so the fallback is always used.

```mermaid
flowchart LR
    CALL["WorkflowHandler(directory=…)\nor Workspace(root_dir=…)"]

    CHECK1{"explicit\ndirectory param?"}
    USE1["root = directory"]

    CHECK2{"MAIN_DIRECTORY_GEOLAB\nenv var set?"}
    USE2["root = MAIN_DIRECTORY_GEOLAB"]

    USE3["root = '.' (cwd)"]

    CALL --> CHECK1
    CHECK1 -->|"yes"| USE1
    CHECK1 -->|"no"| CHECK2
    CHECK2 -->|"yes (GEOLAB sets this)"| USE2
    CHECK2 -->|"no (LOCAL default)"| USE3

    style USE2 fill:#e3f2fd,stroke:#1565C0
    style USE3 fill:#e8f5e9,stroke:#2E7D32
```

### Typical root in each environment

| Environment | Typical root | Set by |
|-------------|-------------|--------|
| LOCAL | Explicit path passed to `WorkflowHandler` | Developer |
| GEOLAB | `$MAIN_DIRECTORY_GEOLAB` | EarthScope cluster config |

---

## Required and optional environment variables

```mermaid
flowchart LR
    subgraph SHARED["Both environments"]
        S3["S3_SYNC_BUCKET\noptional — enables S3 push/pull\nwarns if absent"]
        AWS1["AWS_PROFILE  or\nAWS_ACCESS_KEY_ID +\nAWS_SECRET_ACCESS_KEY +\nAWS_SESSION_TOKEN\noptional — required only for S3 ops"]
        ES["EARTHSCOPE_TOKEN\nneeded for archive download\n(handled by EarthScope SDK)"]
    end

    subgraph LOCAL_BOX["LOCAL"]
        LWE["WORKING_ENVIRONMENT=LOCAL\n(default — may be omitted)"]
    end

    subgraph GEOLAB_BOX["GEOLAB"]
        GWE["WORKING_ENVIRONMENT=GEOLAB\nrequired"]
        GMD["MAIN_DIRECTORY_GEOLAB\nrequired — workspace root\nwarns + uses cwd if absent"]
    end

    style SHARED fill:#f8f9fa,stroke:#6c757d
    style LOCAL_BOX fill:#e8f5e9,stroke:#2E7D32
    style GEOLAB_BOX fill:#e3f2fd,stroke:#1565C0
```

### Variable reference table

| Variable | LOCAL | GEOLAB | Effect when missing |
|----------|-------|--------|---------------------|
| `WORKING_ENVIRONMENT` | Optional (`LOCAL` default) | Must be `"GEOLAB"` | Defaults to `LOCAL` |
| `MAIN_DIRECTORY_GEOLAB` | Ignored | Required | Warning; falls back to `"."` |
| `S3_SYNC_BUCKET` | Optional | Optional | Warning; S3 sync unavailable |
| `AWS_PROFILE` | Optional | Optional | Falls back to explicit key vars |
| `AWS_ACCESS_KEY_ID` / `AWS_SECRET_ACCESS_KEY` | Optional | Optional | Warning if S3 ops attempted |
| `AWS_SESSION_TOKEN` | Optional | Optional | Omitted if not set |

---

## S3 sync behaviour

S3 sync is available in **both** environments — it is gated only on `S3_SYNC_BUCKET` being set, not on the value of `WORKING_ENVIRONMENT`.

```mermaid
flowchart TD
    OP(["Sync operation requested\n(push / pull)"])
    CHECK_BUCKET{"S3_SYNC_BUCKET\nenv var set?"}
    AVAIL["S3 sync available\nin both LOCAL and GEOLAB"]
    WARN_PUSH["⚠ warn: no remote configured\n(push is a no-op)"]
    RAISE_PULL["✗ raise RuntimeError\n'S3_SYNC_BUCKET not set'\n(pull would silently skip data)"]

    CHECK_TYPE{"push or pull?"}

    OP --> CHECK_BUCKET
    CHECK_BUCKET -->|"set"| AVAIL
    CHECK_BUCKET -->|"not set"| CHECK_TYPE
    CHECK_TYPE -->|"push"| WARN_PUSH
    CHECK_TYPE -->|"pull"| RAISE_PULL

    style AVAIL fill:#e8f5e9,stroke:#2E7D32
    style WARN_PUSH fill:#fff3e0,stroke:#E65100
    style RAISE_PULL fill:#ffebee,stroke:#C62828
```

#### Auto-sync during mid-processing

`midprocess_parse_surveys()` automatically calls `sync_from_s3()` (pull) if `S3_SYNC_BUCKET` is configured. This is the only workflow step that triggers S3 access without an explicit user call.

---

## Behaviour differences at a glance

| Feature | LOCAL | GEOLAB |
|---------|-------|--------|
| Workspace root source | Explicit param → cwd | `MAIN_DIRECTORY_GEOLAB` env var |
| Archive download | EarthScope SDK (same) | EarthScope SDK (same) |
| S3 push / pull | Available when `S3_SYNC_BUCKET` set | Available when `S3_SYNC_BUCKET` set |
| AWS credential source | `AWS_PROFILE` or key vars (same) | `AWS_PROFILE` or key vars (same) |
| CLI entry point | `sfgtools run` or `sfgtools preprocess` (same) | Same |
| Auto-sync on mid-process | Yes, if `S3_SYNC_BUCKET` set | Yes, if `S3_SYNC_BUCKET` set |

---

## Survey parsing

`midprocess_parse_surveys()` has one environment-sensitive behaviour: it automatically pulls data from S3 before parsing when `S3_SYNC_BUCKET` is configured.  This pull is triggered by the bucket setting, not by `WORKING_ENVIRONMENT` — it happens in both environments.

```mermaid
flowchart TD
    CALL(["midprocess_parse_surveys(override=…)"])
    CHECK_BUCKET{"S3_SYNC_BUCKET\nconfigured?"}
    PULL["sync_from_s3(overwrite=override)\npull campaign data from S3\nbefore parsing"]
    PARSE["session.pipeline.parse_surveys()\nread TileDB shotdata\nwrite per-survey CSVs"]
    DONE(["Survey CSVs written"])

    CALL --> CHECK_BUCKET
    CHECK_BUCKET -->|"yes (either environment)"| PULL --> PARSE
    CHECK_BUCKET -->|"no"| PARSE
    PARSE --> DONE

    style PULL fill:#e3f2fd,stroke:#1565C0
```

The parsing logic itself — reading TileDB arrays, filtering, writing CSVs — is identical in both environments.

---

## TileDB arrays and GARPOS

> **Note:** The behaviour described in this section reflects **intended design**, not the current implementation. Neither remote TileDB URIs nor the GEOLAB-only GarposHandler guard are implemented yet.

### Current state

TileDB arrays are always built and opened as **local directories** under `<workspace_root>/<network>/<station>/TileDB/`. The path is derived from `TileDBLayout.for_station()` and is a plain `UPath` rooted at the workspace directory — there are no environment checks and no S3 URI construction anywhere in the array-creation path.

`GarposHandler` has no environment guards and runs identically in both environments.

### Intended design (not yet implemented)

The intended split is:

| Step | LOCAL | GEOLAB (intended) |
|------|-------|-------------------|
| Preprocessing (SV3/QC pipelines) | Build TileDB arrays locally | Build TileDB arrays locally |
| TileDB storage | Local filesystem under workspace root | Remote S3 URI (`s3://bucket/…/TileDB/`) |
| GARPOS modeling | Not intended for production use | Primary use case — open remote TileDB URIs directly |
| GarposHandler guard | Should warn / raise | Unrestricted |

The path to implementing this is:

1. `TileDBLayout.for_station()` would need to accept (or derive from `Environment`) an optional S3 root, producing `s3://bucket/net/sta/TileDB/…` URIs when in GEOLAB.
2. All TileDB array constructors (`TDBShotDataArray`, `TDBKinPositionArray`, etc.) accept `UPath` / `str` already — remote URIs would work as long as TileDB's S3 driver is configured.
3. `GarposHandler` (or `WorkflowHandler.modeling_run_garpos`) could check `Environment.working_environment()` and raise `NotImplementedError` in LOCAL until the feature is complete.

---

## Setup checklists

### LOCAL setup

```
□ Install the package: pip install earthscope-sfg-workflows
□ (Optional) export WORKING_ENVIRONMENT=LOCAL   # this is the default
□ (Optional) export S3_SYNC_BUCKET=s3://your-bucket
□ (Optional) export AWS_PROFILE=your-profile    # or set key/secret/token vars
□ Authenticate with EarthScope SDK for archive access
□ Pass an explicit directory to WorkflowHandler('/path/to/data')
```

### GEOLAB setup

```
□ export WORKING_ENVIRONMENT=GEOLAB
□ export MAIN_DIRECTORY_GEOLAB=/path/to/shared/data   # required
□ (Optional) export S3_SYNC_BUCKET=s3://your-bucket
□ (Optional) export AWS_PROFILE=your-profile           # or key/secret/token vars
□ EarthScope SDK credentials are typically pre-configured in the cluster
□ WorkflowHandler() with no directory arg resolves root from MAIN_DIRECTORY_GEOLAB
```
