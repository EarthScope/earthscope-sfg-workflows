# `prefiltering` — Shotdata QC filters

Pure-pandas filter functions that drop bad shotdata rows before
mid-processing or modeling. All filters return a *filtered* `DataFrame`
without mutating the input.

## Public API

```python
from earthscope_sfg_workflows.prefiltering import filter_shotdata
```

`filter_shotdata` is the top-level entry point. It composes the individual
filters below according to a `FilterConfig` (see `schemas.py`).

## Individual filters (in `utils.py`)

| Function                            | Drops rows with…                                |
| ----------------------------------- | ----------------------------------------------- |
| `filter_max_distance_from_center`   | distance > `max_distance_m` from array center   |
| `filter_min_snr`                    | SNR < `snr_min`                                 |
| `filter_dbv_range`                  | DBV outside `[dbv_min, dbv_max]`                |
| `filter_min_xc`                     | XC < `xc_min`                                   |
| `filter_acoustic_good`              | non-"good" acoustic diagnostics                 |
| `filter_acoustic_ok`                | non-"ok" acoustic diagnostics                   |
| `filter_acoustic_difficult`         | non-"difficult" acoustic diagnostics            |
| `filter_min_replies`                | fewer than `min_replies` ping replies           |
| `filter_max_wrms`                   | WRMS > `max_wrms` mm (joins kinematic position) |

## Configuration

`FilterConfig` (`schemas.py`) groups filter knobs into:

- `AcousticFilterConfig` — `FilterLevel.GOOD | OK | DIFFICULT`
- `PingRepliesFilterConfig` — `min_replies`
- `MaxDistFromCenterConfig` — `array_center_lat`, `array_center_lon`, `max_distance_m`
- `PrideResidualsConfig` — `max_wrms`

Loaded from YAML via `config.loadconfigs.get_survey_filter_config`.
