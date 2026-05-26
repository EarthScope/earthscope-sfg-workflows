# schemas

`earthscope_sfg_workflows.prefiltering.schemas`

Pydantic schemas for shotdata pre-filtering configuration.

## class `AcousticFilterConfig`

Configuration for the acoustic-diagnostics filter.

Attributes
----------
enabled : bool
    When ``True``, acoustic-diagnostics filtering is applied. Default is
    ``False``.
level : FilterLevel
    Strictness level controlling SNR, DBV, and XC thresholds. Default is
    ``FilterLevel.OK``.

Methods
-------
serialize_level(level)
    Serialize ``FilterLevel`` to its string value for JSON/YAML output.

**Fields**

| Name | Type | Description |
|---|---|---|
| `enabled` | `bool` | Whether to enable acoustic diagnostics filtering |
| `level` | `FilterLevel` | Level of acoustic diagnostics to filter. Options: GOOD, OK, DIFFICULT |

**Methods**

### `AcousticFilterConfig.serialize_level(level: earthscope_sfg_workflows.prefiltering.schemas.FilterLevel) -> str`

Serialize ``FilterLevel`` to its string value for JSON/YAML output.

Parameters
----------
level : FilterLevel
    The filter level enum member to serialize.

Returns
-------
str
    String representation of *level*.


## class `FilterConfig`

Top-level container for all shotdata pre-filter configurations.

Attributes
----------
acoustic_filters : AcousticFilterConfig
    Configuration for acoustic-diagnostics (SNR, DBV, XC) filtering.
ping_replies : PingRepliesFilterConfig
    Configuration for minimum ping-replies filtering.
max_distance_from_center : MaxDistFromCenterConfig
    Configuration for maximum distance from array-center filtering.
pride_residuals : PrideResidualsConfig
    Configuration for PRIDE-PPP kinematic residual filtering.

Methods
-------
update(custom_config)
    Apply nested overrides from *custom_config* in-place.

**Fields**

| Name | Type | Description |
|---|---|---|
| `acoustic_filters` | `AcousticFilterConfig` | Configuration for acoustic diagnostics filtering |
| `ping_replies` | `PingRepliesFilterConfig` | Configuration for ping replies filtering |
| `max_distance_from_center` | `MaxDistFromCenterConfig` | Configuration for max distance from center filtering |
| `pride_residuals` | `PrideResidualsConfig` | Configuration for PRIDE residuals filtering |

**Methods**

### `FilterConfig.update(self, custom_config: dict[str, typing.Any]) -> None`

Apply nested overrides from *custom_config* in-place.

Parameters
----------
custom_config : dict
    Mapping whose keys match ``FilterConfig`` field names and whose
    values are either scalars or sub-dicts whose keys match the
    corresponding nested model's field names.


## class `FilterLevel`

Acoustic-diagnostic strictness level used by ``AcousticFilterConfig``.

Attributes
----------
GOOD : str
    Strictest thresholds — highest data quality required.
OK : str
    Moderate thresholds — default level.
DIFFICULT : str
    Most permissive thresholds — retains data from challenging conditions.

## class `MaxDistFromCenterConfig`

Configuration for the max-distance-from-array-center filter.

Attributes
----------
enabled : bool
    When ``True``, shots beyond ``max_distance_m`` from the array center
    are removed. Default is ``True``.
max_distance_m : float
    Maximum horizontal distance from the array center in metres. Default
    is ``150.0``.

**Fields**

| Name | Type | Description |
|---|---|---|
| `enabled` | `bool` | Whether to enable max distance from center filtering |
| `max_distance_m` | `float` | Maximum distance from the survey center in meters to keep a shot |

## class `PingRepliesFilterConfig`

Configuration for the minimum-ping-replies filter.

Attributes
----------
enabled : bool
    When ``True``, shots with fewer than ``min_replies`` replies are
    removed. Default is ``False``.
min_replies : int
    Minimum reply count required to retain a shot. Default is ``3``.

**Fields**

| Name | Type | Description |
|---|---|---|
| `enabled` | `bool` | Whether to enable ping replies filtering |
| `min_replies` | `int` | Minimum number of replies required to keep a shot |

## class `PrideResidualsConfig`

Configuration for the PRIDE-PPP kinematic residual filter.

Attributes
----------
enabled : bool
    When ``True``, shots coinciding with high PRIDE WRMS epochs are
    removed. Default is ``False``.
max_residual_mm : float
    Maximum allowable PRIDE WRMS residual in millimetres. Default is
    ``8.0``.

**Fields**

| Name | Type | Description |
|---|---|---|
| `enabled` | `bool` | Whether to enable PRIDE residuals filtering |
| `max_residual_mm` | `float` | Maximum PRIDE residual in millimeters to keep a shot |
