# `earthscope_sfg_cli` — `sfgtools` CLI

Typer-based command line entry point for SFG workflows.

Installed as the `sfgtools` script (see `pyproject.toml`).

## Commands

| Command                | Description                                                         |
| ---------------------- | ------------------------------------------------------------------- |
| `sfgtools run <file>`  | Run a full pipeline from a manifest file (`.json` / `.yaml`).       |
| `sfgtools preprocess`  | Run preprocessing for a network/campaign/station list.              |

## Modules

| Module        | Purpose                                                  |
| ------------- | -------------------------------------------------------- |
| `__main__.py` | Typer app and command wiring (`sfgtools` entry point).   |
| `commands.py` | `run_manifest`, `run_preprocessing` execution functions. |
| `manifest.py` | `PipelineManifest` schema (JSON/YAML).                   |
| `utils.py`    | Shared CLI helpers.                                      |

---

## Configuration File

This section outlines how to build a configuration file and run it to perform
pre-processing and modelling on seafloor GNSS-acoustic data.

The configuration file allows users to schedule jobs for a given network,
station, and campaign. It is consumed by `sfgtools run <file>` (CLI) or
`PipelineManifest.load(path)` (Python API).

See `dev/preprocess-manifest.json` and `dev/NCC1-preproc-manifest.json` in the
repository root for working examples.

### `projectDir`

> Note: not required when working in GeoLab.

The primary directory where all processing, ingestion, and downloaded data are
stored. The application reads and writes data from/to this directory during
execution.

**JSON**
```json
{
  "projectDir": "/Users/user/Project/SeaFloorGeodesy/Data/SFGMain",
  "operations": [],
  "globalConfig": {}
}
```

**YAML**
```yaml
projectDir: "/Users/user/Project/SeaFloorGeodesy/Data/SFGMain"
operations: []
globalConfig: {}
```

---

### `operations`

A list of operation objects. Each operation specifies a network / station /
campaign context and a list of jobs to run for that context.

| Field      | Description                                        |
| ---------- | -------------------------------------------------- |
| `network`  | Network associated with the station.               |
| `station`  | Specific station in the network.                   |
| `campaign` | Campaign name related to the data collection.      |
| `jobs`     | List of job objects (see [Jobs](#jobs) below).     |

**JSON**
```json
{
  "projectDir": "/Users/user/Project/SeaFloorGeodesy/Data/SFGMain",
  "operations": [
    {
      "network": "cascadia-gorda",
      "station": "NDP1",
      "campaign": "2023_from_john",
      "jobs": []
    }
  ],
  "globalConfig": {}
}
```

**YAML**
```yaml
projectDir: "/Users/user/Project/SeaFloorGeodesy/Data/SFGMain"
operations:
  - network: "cascadia-gorda"
    station: "NDP1"
    campaign: "2023_from_john"
    jobs: []
globalConfig: {}
```

---

### Jobs

Each job object must have a `type` field. The supported job types are:

| Type             | Description                                      |
| ---------------- | ------------------------------------------------ |
| `ingestion`      | Ingest raw data files from a local directory.    |
| `download`       | Download remote assets from the archive.         |
| `preprocessing`  | Run the SV3 preprocessing pipeline.              |
| `garpos`         | Run GARPOS acoustic positioning inversion.       |

#### `ingestion`

Processes data from a local directory and adds it to the project.

**JSON**
```json
{
  "type": "ingestion",
  "directory": "/Users/user/Project/SeaFloorGeodesy/Data/Cascadia2023/NDP1/HR"
}
```

**YAML**
```yaml
type: "ingestion"
directory: "/Users/user/Project/SeaFloorGeodesy/Data/Cascadia2023/NDP1/HR"
```

---

#### `download`

Checks for available remote assets and downloads them.

**JSON**
```json
{
  "type": "download"
}
```

**YAML**
```yaml
type: "download"
```

---

#### `preprocessing`

Runs the SV3 preprocessing pipeline (NovAtel → RINEX → PRIDE-PPP →
shotdata). Per-job configuration overrides can be provided under `config`;
unset fields inherit from `globalConfig`.

**JSON**
```json
{
  "type": "preprocessing",
  "config": {
    "rinex_config": { "override": true, "time_interval": 24, "modulo_millis": 1000 },
    "pride_config": { "sample_frequency": 1 }
  }
}
```

**YAML**
```yaml
type: "preprocessing"
config:
  rinex_config:
    override: true
    time_interval: 24
    modulo_millis: 1000   # decimate to 1 Hz
  pride_config:
    sample_frequency: 1
```

---

#### `garpos`

Runs GARPOS acoustic positioning inversion.
See [INDEX.md](INDEX.md#garpos-config) for full config details.

**JSON**
```json
{
  "type": "garpos",
  "config": {
    "run_id": 1,
    "override": true,
    "inversion_params": { "rejectcriteria": 2.5, "log_lambda": [0] }
  }
}
```

**YAML**
```yaml
type: "garpos"
config:
  run_id: 1
  override: true
  inversion_params:
    rejectcriteria: 2.5
    log_lambda:
      - 0
```

---

### `globalConfig`

Top-level defaults for all `preprocessing` jobs in this manifest. Individual
jobs may override specific keys under their own `config` block.

See [INDEX.md](INDEX.md) for per-field documentation.

---

### `garposConfig`

Top-level defaults for all `garpos` jobs in this manifest. Individual jobs
may override specific keys under their own `config` block.

See [INDEX.md](INDEX.md#garpos-config) for per-field documentation.

---

### Complete example

**JSON**
```json
{
  "projectDir": "/Users/user/Project/SeaFloorGeodesy/Data/SFGMain",
  "operations": [
    {
      "network": "cascadia-gorda",
      "station": "NDP1",
      "campaign": "2023_from_john",
      "jobs": [
        {
          "type": "ingestion",
          "directory": "/Users/user/Project/SeaFloorGeodesy/Data/Cascadia2023/NDP1/HR"
        },
        {
          "type": "preprocessing",
          "config": {
            "rinex_config": { "override": true, "time_interval": 24 },
            "pride_config": { "sample_frequency": 1 }
          }
        }
      ]
    }
  ],
  "globalConfig": {}
}
```

**YAML**
```yaml
projectDir: "/Users/user/Project/SeaFloorGeodesy/Data/SFGMain"
operations:
  - network: "cascadia-gorda"
    station: "NDP1"
    campaign: "2023_from_john"
    jobs:
      - type: "ingestion"
        directory: "/Users/user/Project/SeaFloorGeodesy/Data/Cascadia2023/NDP1/HR"
      - type: "preprocessing"
        config:
          rinex_config:
            override: true
            time_interval: 24
          pride_config:
            sample_frequency: 1
globalConfig: {}
```

---

## Usage

### CLI

```bash
sfgtools run path/to/manifest.json
```

### Python

```python
from earthscope_sfg_cli.manifest import PipelineManifest
from earthscope_sfg_cli.commands import run_manifest

manifest = PipelineManifest.load("/path/to/manifest.json")
run_manifest(manifest)
```

---

## Configuration reference

See [INDEX.md](INDEX.md) for detailed documentation on every configuration
field (`NovatelConfig`, `RinexConfig`, `PrideConfig`, `DFOP00Config`,
`PositionUpdateConfig`, and `GarposConfig`).
