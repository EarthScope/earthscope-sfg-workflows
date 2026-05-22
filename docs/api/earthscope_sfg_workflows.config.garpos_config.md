# garpos_config

`earthscope_sfg_workflows.config.garpos_config`

This module contains default configuration settings for the GARPOS model,
including variance parameters for transponder and DPOS positions.

## class `GarposSiteConfig`

A Pydantic model for GARPOS site-specific configuration.

**Fields**

| Name | Type | Description |
|---|---|---|
| `transponder_position_variance` | `GPPositionENU` | Variance to add to the transponder positions (in meters). |
| `inversion_params` | `InversionParams` | Inversion parameters for GARPOS. |
