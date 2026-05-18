# `earthscope_sfg_cli` — `sfgtools` CLI

Typer-based command line entry point for SFG workflows.

Installed as the `sfgtools` script (see `pyproject.toml`).

## Commands

| Command                | Description                                                         |
| ---------------------- | ------------------------------------------------------------------- |
| `sfgtools run <file>`  | Run a full pipeline from a manifest file (`.json` / `.yaml`).       |
| `sfgtools preprocess`  | Run preprocessing for a network/campaign/station list.              |

## Manifest format

Manifests are loaded by `PipelineManifest.from_json` / `from_yaml`
(`manifest.py`). See `dev/preprocess-manifest.json` and
`dev/NCC1-preproc-manifest.json` for working examples.

## Modules

| Module        | Purpose                                                  |
| ------------- | -------------------------------------------------------- |
| `__main__.py` | Typer app and command wiring (`sfgtools` entry point).   |
| `commands.py` | `run_manifest`, `run_preprocessing` execution functions. |
| `manifest.py` | `PipelineManifest` schema (JSON/YAML).                   |
| `utils.py`    | Shared CLI helpers.                                      |
