# config

`earthscope_sfg_workflows.pipelines.config`

Pydantic configuration models for the SV3/QC pipelines and ancillary tools.

## class `DFOP00Config`

Settings for DFOP00 raw-file processing.

**Fields**

| Name | Type | Description |
|---|---|---|
| `override` | `bool` |  |

## class `NovatelConfig`

Settings for Novatel raw-file processing.

**Fields**

| Name | Type | Description |
|---|---|---|
| `override` | `bool` |  |
| `n_processes` | `int` |  |

## class `PositionUpdateConfig`

Settings for position-update interpolation onto shotdata.

**Fields**

| Name | Type | Description |
|---|---|---|
| `override` | `bool` |  |
| `lengthscale` | `float` |  |
| `plot` | `bool` |  |

## class `PrepSiteData`

Paths and identifiers needed to prepare per-site preprocessing inputs.

**Fields**

| Name | Type | Description |
|---|---|---|
| `network` | `str` |  |
| `station` | `str` |  |
| `campaign` | `str` |  |
| `inter_dir` | `Path` |  |
| `pride_dir` | `Path` |  |
| `gnss_obs_data_dest` | `str \| pathlib.Path` |  |
| `kin_position_data_dest` | `str \| pathlib.Path` |  |
| `shot_data_dest` | `str \| pathlib.Path` |  |

## class `PrideConfig`

Pipeline-level configuration for PRIDE processing.
Holds settings that control the pipeline behavior (concurrency, overrides)
separately from PrideCLIConfig which holds pdp3 CLI flags.

**Fields**

| Name | Type | Description |
|---|---|---|
| `cli` | `PrideCLIConfig` |  |
| `override` | `bool` |  |
| `n_processes` | `int` |  |
| `override_products_download` | `bool` |  |

## class `QCPinConfig`

Configuration for QC PIN file processing.

**Fields**

| Name | Type | Description |
|---|---|---|
| `override` | `bool` |  |
| `n_processes` | `int` |  |

## class `QCPipelineConfig`

Configuration for the QC Pipeline.

**Fields**

| Name | Type | Description |
|---|---|---|
| `qcpin_config` | `QCPinConfig` |  |
| `pride_config` | `PrideConfig` |  |
| `rinex_config` | `RinexConfig` |  |
| `position_update_config` | `PositionUpdateConfig` |  |

**Methods**

### `QCPipelineConfig.to_yaml(self, filepath: pathlib.Path)`

Serialize this config to YAML at `filepath`.

### `QCPipelineConfig.update(self, update_dict: dict) -> 'QCPipelineConfig'`

Update the object with values from a dict and return a new copy.


## class `RinexConfig`

Settings for RINEX generation/decimation from TileDB GNSS data.

**Fields**

| Name | Type | Description |
|---|---|---|
| `override` | `bool` |  |
| `n_processes` | `int` |  |
| `settings_path` | `pathlib.Path \| None` |  |
| `time_interval` | `int \| None` |  |
| `processing_year` | `int \| None` | Processing year to query tiledb |
| `modulo_millis` | `int` | Decimation modulo in milliseconds (e.g., 1000 for 1 Hz, 15000 for 15s). If 0, no decimation. LLI from skipped epochs is propagated. |
| `use_secondary` | `bool` | If True, uses the secondary GNSS observation data for processing. |

## class `SV3PipelineConfig`

Top-level config bundling all SV3 pipeline stage configs.

**Fields**

| Name | Type | Description |
|---|---|---|
| `pride_config` | `PrideConfig` |  |
| `novatel_config` | `NovatelConfig` |  |
| `rinex_config` | `RinexConfig` |  |
| `dfop00_config` | `DFOP00Config` |  |
| `position_update_config` | `PositionUpdateConfig` |  |

**Methods**

### `SV3PipelineConfig.to_yaml(self, filepath: pathlib.Path)`

Serialize this config to YAML at `filepath`.

### `SV3PipelineConfig.update(self, update_dict: dict) -> 'SV3PipelineConfig'`

Return a new config with values from `update_dict` merged over current values.

