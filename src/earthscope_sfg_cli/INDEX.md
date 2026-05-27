# Configuration Reference

Detailed documentation for every configuration class used in the
`globalConfig` and `garposConfig` sections of a pipeline manifest.

All classes are defined in
`src/earthscope_sfg_workflows/pipelines/config.py` and
`src/earthscope_sfg_cli/manifest.py`.

---

## NovatelConfig

Settings for Novatel raw-file processing (`novatel_config`).

| Field         | Type   | Default         | Description                                           |
| ------------- | ------ | --------------- | ----------------------------------------------------- |
| `override`    | `bool` | `false`         | If `true`, existing processed data will be replaced.  |
| `n_processes` | `int`  | CPU count       | Number of parallel processes for Novatel processing.  |

---

## RinexConfig

Settings for RINEX generation/decimation from TileDB GNSS data
(`rinex_config`).

| Field              | Type            | Default    | Description                                                                    |
| ------------------ | --------------- | ---------- | ------------------------------------------------------------------------------ |
| `override`         | `bool`          | `false`    | If `true`, existing RINEX files will be replaced.                              |
| `n_processes`      | `int`           | CPU count  | Number of parallel processes for RINEX generation.                             |
| `time_interval`    | `int` \| `null` | `1`        | Time interval in hours for RINEX file pagination. Higher values (max `24`) are faster but consume more memory. |
| `processing_year`  | `int` \| `null` | `-1`       | Calendar year to generate RINEX files from the TileDB array. `-1` reads the year from the first four digits of the campaign name. |
| `modulo_millis`    | `int`           | `0`        | Decimation modulo in milliseconds (see note below). `0` disables decimation.   |
| `use_secondary`    | `bool`          | `false`    | If `true`, uses secondary GNSS observation data.                               |

> **`modulo_millis` note**: Only epochs where `epoch_time_ms % modulo_millis == 0`
> are kept; Loss-of-Lock Indicators from skipped epochs are propagated to the
> next written epoch. Examples: `1000` for 1 Hz output, `15000` for 15-second
> intervals.

---

## DFOP00Config

Settings for DFOP00 raw-file processing (`dfop00_config`).

| Field      | Type   | Default | Description                                          |
| ---------- | ------ | ------- | ---------------------------------------------------- |
| `override` | `bool` | `false` | If `true`, existing processed data will be replaced. |

---

## PositionUpdateConfig

Settings for position-update interpolation onto shotdata
(`position_update_config`).

| Field         | Type    | Default | Description                                                      |
| ------------- | ------- | ------- | ---------------------------------------------------------------- |
| `override`    | `bool`  | `false` | If `true`, existing data will be replaced.                       |
| `plot`        | `bool`  | `false` | If `true`, plots the updated shotdata.                           |
| `lengthscale` | `float` | `0.1`   | Interpolation length scale in seconds (range: 0.1 – 1.0).       |

---

## PrideConfig

Configuration for PRIDE PPP processing (`pride_config`).

| Field                       | Type                    | Default                          | Description                                                              |
| --------------------------- | ----------------------- | -------------------------------- | ------------------------------------------------------------------------ |
| `override`                  | `bool`                  | `false`                          | If `true`, existing kin files will be replaced.                          |
| `n_processes`               | `int`                   | `1`                              | Number of parallel PRIDE processes.                                      |
| `override_products_download`| `bool`                  | `false`                          | If `true`, existing downloaded products will be replaced.                |
| `cutoff_elevation`          | `int`                   | `7`                              | Elevation cut-off angle in degrees.                                      |
| `end`                       | `str` \| `null`         | `null`                           | End time for processing.                                                 |
| `frequency`                 | `list[str]`             | `["G12","R12","E15","C26","J12"]`| Frequency bands to process.                                              |
| `high_ion`                  | `bool` \| `null`        | `null`                           | If `true`, enables high-ionospheric-activity mode.                       |
| `interval`                  | `int` \| `null`         | `null`                           | Processing interval in seconds.                                          |
| `local_pdp3_path`           | `path` \| `null`        | `null`                           | Path to a local PDP3 data directory.                                     |
| `loose_edit`                | `bool`                  | `false`                          | If `true`, enables loose editing mode.                                   |
| `sample_frequency`          | `int`                   | `1`                              | Sampling frequency in Hz.                                                |
| `start`                     | `str` \| `null`         | `null`                           | Start time for processing.                                               |
| `system`                    | `str`                   | `"GREC23J"`                      | GNSS systems to use.                                                     |
| `tides`                     | `str`                   | `"SOP"`                          | Tide model to use.                                                       |

---

## Garpos Config

### GARPOSConfig (`garposConfig`)

Top-level GARPOS job configuration.

| Field             | Type            | Default | Description                                                                  |
| ----------------- | --------------- | ------- | ---------------------------------------------------------------------------- |
| `garpos_path`     | `path` \| `null`| `null`  | Path to a local GARPOS repository. Defaults to the version installed via pip.|
| `iterations`      | `int` \| `null` | `2`     | Number of GARPOS inversion iterations.                                       |
| `run_id`          | `str` \| `int`  | `"0"`   | Label for the GARPOS run instance.                                           |
| `override`        | `bool` \| `null`| `false` | If `true`, overrides existing runs with the same `run_id`.                   |
| `inversion_params`| object \| `null`| `null`  | Fine-tuning parameters for the inversion (see [Inversion Parameters](#inversion-parameters)). |
| `filter_config`   | object \| `null`| default | Pre-filtering configuration (see [Filter Config](#filter-config)).           |

---

### Filter Config

Pre-filtering configuration applied to GARPOS shot data before inversion.

#### `pride_residuals`

| Field             | Type   | Default | Description                                          |
| ----------------- | ------ | ------- | ---------------------------------------------------- |
| `enabled`         | `bool` | `false` | If `true`, enables GNSS residual filtering.          |
| `max_residual_mm` | `int`  | `8`     | Maximum residual threshold in millimetres.           |

#### `max_distance_from_center`

| Field            | Type    | Default | Description                                                     |
| ---------------- | ------- | ------- | --------------------------------------------------------------- |
| `enabled`        | `bool`  | `false` | If `true`, enables distance-based filtering.                    |
| `max_distance_m` | `float` | `150.0` | Maximum distance threshold in metres from the array centre.     |

#### `ping_replies`

| Field         | Type   | Default | Description                                              |
| ------------- | ------ | ------- | -------------------------------------------------------- |
| `enabled`     | `bool` | `false` | If `true`, enables ping-reply filtering.                 |
| `min_replies` | `int`  | `1`     | Minimum number of required acoustic ping replies.        |

#### `acoustic_filters`

| Field     | Type   | Default | Description                                            |
| --------- | ------ | ------- | ------------------------------------------------------ |
| `enabled` | `bool` | `true`  | If `true`, applies standard acoustic data quality filters. |
| `level`   | `str`  | —       | Quality threshold level (see note below).              |

> **`level` values**:
> - `"GOOD"` — SNR ≥ 20, DBV −26 to −3, XC ≥ 60
> - `"OK"` — SNR 12–20, DBV −36 to −3, XC 45–60
> - `"DIFFICULT"` — SNR < 12, DBV < −36 or > −3, XC < 45

---

### Inversion Parameters

Fine-tuning parameters passed to the GARPOS inversion engine
(`inversion_params`).

| Field                              | Type          | Default              | Description                                                     |
| ---------------------------------- | ------------- | -------------------- | --------------------------------------------------------------- |
| `spline_degree`                    | `int`         | `3`                  | Degree of the spline used in the inversion.                     |
| `log_lambda`                       | `list[int]`   | `[-2]`               | Logarithmic lambda values for the inversion.                    |
| `log_gradlambda`                   | `int`         | `-1`                 | Logarithmic gradient lambda value.                              |
| `mu_t`                             | `list[float]` | `[0.0]`              | Temporal regularisation parameter.                              |
| `mu_mt`                            | `list[float]` | `[0.5]`              | Spatial regularisation parameter.                               |
| `knotint0`                         | `int`         | `5`                  | Knot interval for the first dimension.                          |
| `knotint1`                         | `int`         | `0`                  | Knot interval for the second dimension.                         |
| `knotint2`                         | `int`         | `0`                  | Knot interval for the third dimension.                          |
| `rejectcriteria`                   | `int`         | `2`                  | Criteria for rejecting data points.                             |
| `inversiontype`                    | `int`         | `0`                  | Type of inversion to perform.                                   |
| `positionalOffset`                 | `list[float]` | `[0.0, 0.0, 0.0]`    | Positional offset `[east, north, up]`.                          |
| `traveltimescale`                  | `float`       | `0.0001`             | Scaling factor for travel time.                                 |
| `maxloop`                          | `int`         | `100`                | Maximum number of iterations for the inversion loop.            |
| `convcriteria`                     | `float`       | `0.005`              | Convergence criteria for the inversion.                         |
| `deltap`                           | `float`       | `1e-06`              | Perturbation parameter for inversion.                           |
| `deltab`                           | `float`       | `1e-06`              | Perturbation parameter for baseline adjustment.                 |
| `delta_center_position.east`       | `int`         | `0`                  | Eastward offset for the centre position.                        |
| `delta_center_position.north`      | `int`         | `0`                  | Northward offset for the centre position.                       |
| `delta_center_position.up`         | `int`         | `0`                  | Upward offset for the centre position.                          |
| `delta_center_position.east_sigma` | `float`       | `1.0`                | Sigma for eastward offset.                                      |
| `delta_center_position.north_sigma`| `float`       | `1.0`                | Sigma for northward offset.                                     |
| `delta_center_position.up_sigma`   | `float`       | `0`                  | Sigma for upward offset.                                        |
| `delta_center_position.cov_nu`     | `int`         | `0`                  | Covariance between north and up.                                |
| `delta_center_position.cov_ue`     | `int`         | `0`                  | Covariance between up and east.                                 |
| `delta_center_position.cov_en`     | `int`         | `0`                  | Covariance between east and north.                              |
