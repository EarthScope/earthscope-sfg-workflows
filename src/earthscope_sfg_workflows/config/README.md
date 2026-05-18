# `config` — Static configuration

Static configuration values, environment lookup, and YAML loaders for SFG
workflows. No I/O state — pure data and loader functions.

## Modules

| Module                 | Purpose                                                       |
| ---------------------- | ------------------------------------------------------------- |
| `env_config.py`        | Environment variable lookup with sensible defaults.           |
| `file_config.py`       | `AssetType` / `FILE_TYPE` enums and download-type constants.  |
| `garpos_config.py`     | `GarposSiteConfig`, `DEFAULT_SITE_CONFIG`.                    |
| `loadconfigs.py`       | YAML loaders for site config and survey filter config.        |
| `shotdata_filters.py`  | Built-in filter dictionaries used by `prefiltering`.          |

## Public API

```python
from earthscope_sfg_workflows.config import (
    AssetType, FILE_TYPE,
    DEFAULT_FILE_TYPES_TO_DOWNLOAD,
    DEFAULT_INTERMEDIATE_FILE_TYPES_TO_DOWNLOAD,
    INTERMEDIATE_DOWNLOAD_TYPES,
    PREPROCESS_DOWNLOAD_TYPES,
    DEFAULT_SITE_CONFIG, GarposSiteConfig,
    get_garpos_site_config, get_survey_filter_config,
)
```
