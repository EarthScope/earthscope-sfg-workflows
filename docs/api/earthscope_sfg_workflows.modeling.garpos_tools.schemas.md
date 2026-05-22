# schemas

`earthscope_sfg_workflows.modeling.garpos_tools.schemas`

Pydantic and pandera schemas for GARPOS inputs, outputs, and config.

## class `GPATDOffset`

Antenna-to-transducer body-frame offsets (forward/rightward/downward, m).

**Fields**

| Name | Type | Description |
|---|---|---|
| `forward` | `float` |  |
| `rightward` | `float` |  |
| `downward` | `float` |  |

**Methods**

### `GPATDOffset.get_offset(self) -> list[float]`

Return `[forward, rightward, downward]`.


## class `GPPositionENU`

Local East/North/Up position with sigmas and covariances (meters).

**Fields**

| Name | Type | Description |
|---|---|---|
| `east` | `float \| None` |  |
| `north` | `float \| None` |  |
| `up` | `float \| None` |  |
| `east_sigma` | `float \| None` |  |
| `north_sigma` | `float \| None` |  |
| `up_sigma` | `float \| None` |  |
| `cov_nu` | `float \| None` |  |
| `cov_ue` | `float \| None` |  |
| `cov_en` | `float \| None` |  |

**Methods**

### `GPPositionENU.get_covariance(self) -> numpy.ndarray`

Return the 3x3 ENU covariance matrix built from sigmas and covariances.

### `GPPositionENU.get_position(self) -> list[float]`

Return `[east, north, up]`.

### `GPPositionENU.get_std_dev(self) -> list[float]`

Return `[east_sigma, north_sigma, up_sigma]`.


## class `GPPositionLLH`

Geodetic position in latitude/longitude/height (degrees, meters).

**Fields**

| Name | Type | Description |
|---|---|---|
| `latitude` | `float` |  |
| `longitude` | `float` |  |
| `height` | `float \| None` |  |

## class `GPTransponder`

Transponder metadata: position, turn-around time offset, and identifiers.

**Fields**

| Name | Type | Description |
|---|---|---|
| `position_llh` | `earthscope_sfg_workflows.modeling.garpos_tools.schemas.GPPositionLLH \| None` |  |
| `position_enu` | `earthscope_sfg_workflows.modeling.garpos_tools.schemas.GPPositionENU \| None` |  |
| `tat_offset` | `float \| None` |  |
| `name` | `str \| None` |  |
| `id` | `str \| None` |  |
| `delta_center_position` | `earthscope_sfg_workflows.modeling.garpos_tools.schemas.GPPositionENU \| None` |  |

## class `GarposFixed`

GARPOS fixed configuration: library paths and inversion parameters.

**Fields**

| Name | Type | Description |
|---|---|---|
| `lib_directory` | `str` |  |
| `lib_raytrace` | `str` |  |
| `inversion_params` | `InversionParams` |  |

## class `GarposInput`

GARPOS observation-input bundle (site, transponders, shot/sound-speed data).

**Fields**

| Name | Type | Description |
|---|---|---|
| `site_name` | `str` |  |
| `campaign_id` | `str` |  |
| `survey_id` | `str` |  |
| `site_center_llh` | `GPPositionLLH` |  |
| `array_center_enu` | `GPPositionENU` |  |
| `transponders` | `list` |  |
| `sound_speed_data` | `pathlib.Path \| str \| None` |  |
| `atd_offset` | `GPATDOffset` |  |
| `start_date` | `datetime` |  |
| `end_date` | `datetime` |  |
| `shot_data` | `pathlib.Path \| str \| None` |  |
| `delta_center_position` | `GPPositionENU` |  |
| `ref_frame` | `str` |  |
| `n_shot` | `int` |  |

**Methods**

### `GarposInput.dt_to_str(self, value)`

Serialize `datetime` fields as ISO-8601 strings.

### `GarposInput.path_to_str(self, value)`

Serialize `Path` fields as plain strings for JSON output.

### `GarposInput.to_datafile(self, path: pathlib.Path) -> None`

Write a GarposInput to a datafile

Parameters
----------
garpos_input : GarposInput
    The GarposInput object
path : Path
    The path to the datafile

Returns
-------
None


## class `GarposObservationOutput`

Pandera schema for the post-inversion GARPOS observation output.

**Fields**

| Name | Type | Description |
|---|---|---|
| `MT` | `<pandera.common.AnnotationInfo object at 0x30ed65b80>` |  |
| `TT` | `<pandera.common.AnnotationInfo object at 0x30eb35eb0>` |  |
| `ST` | `<pandera.common.AnnotationInfo object at 0x30ec9ac60>` |  |
| `RT` | `<pandera.common.AnnotationInfo object at 0x30e5a7aa0>` |  |
| `flag` | `<pandera.common.AnnotationInfo object at 0x30a5e5040>` |  |
| `gamma` | `<pandera.common.AnnotationInfo object at 0x30e495a00>` |  |
| `ResiTT` | `<pandera.common.AnnotationInfo object at 0x3102711f0>` |  |
| `TakeOff` | `<pandera.common.AnnotationInfo object at 0x310271250>` |  |
| `head1` | `<pandera.common.AnnotationInfo object at 0x30fed3770>` |  |
| `ResiRange` | `<pandera.common.AnnotationInfo object at 0x30fed3710>` |  |
| `dVO` | `<pandera.common.AnnotationInfo object at 0x30fed3740>` |  |
| `gradV1e` | `<pandera.common.AnnotationInfo object at 0x30fed3800>` |  |
| `gradV1n` | `<pandera.common.AnnotationInfo object at 0x30f4b8980>` |  |
| `gradV2e` | `<pandera.common.AnnotationInfo object at 0x30f4b8740>` |  |
| `gradV2n` | `<pandera.common.AnnotationInfo object at 0x30e6a0050>` |  |
| `dV` | `<pandera.common.AnnotationInfo object at 0x30e6a0d40>` |  |
| `LogResidual` | `<pandera.common.AnnotationInfo object at 0x30e6a11c0>` |  |

## class `InversionLoop`

Per-iteration diagnostics from a GARPOS inversion run.

**Fields**

| Name | Type | Description |
|---|---|---|
| `iteration` | `int` |  |
| `rms_tt` | `float` |  |
| `used_shot_percentage` | `float` |  |
| `reject` | `int` |  |
| `max_dx` | `float` |  |
| `hgt` | `float` |  |
| `inv_type` | `InversionType` |  |

## class `InversionParams`

Hyperparameters and inversion settings for a GARPOS run.

**Fields**

| Name | Type | Description |
|---|---|---|
| `spline_degree` | `int` |  |
| `log_lambda` | `list` | Smoothness parameter for backgroun perturbation |
| `log_gradlambda` | `float` | Smoothness paramter for spatial gradient |
| `mu_t` | `list` | Correlation length of data for transmit time [minute] |
| `mu_mt` | `list` | Data correlation coefficient b/w the different transponders |
| `knotint0` | `int` | Typical Knot interval (in min.) for gamma's component (a0, a1, a2) |
| `knotint1` | `int` | Typical Knot interval (in min.) for gamma's component (a0, a1, a2) |
| `knotint2` | `int` | Typical Knot interval (in min.) for gamma's component (a0, a1, a2) |
| `rejectcriteria` | `float` | Criteria for the rejection of data (+/- rsig * Sigma) |
| `inversiontype` | `InversionType` | Inversion type |
| `positionalOffset` | `list[float] \| None` | Positional offset for the inversion |
| `traveltimescale` | `float` | Typical measurement error for travel time (= 1.e-4 sec is recommended in 10 kHz carrier) |
| `maxloop` | `int` | Maximum loop for iteration |
| `convcriteria` | `float` | Convergence criteria for model parameters |
| `deltap` | `float` | Infinitesimal values to make Jacobian matrix |
| `deltab` | `float` | Infinitesimal values to make Jacobian matrix |
| `delta_center_position` | `GPPositionENU` | Delta center position |

**Methods**

### `InversionParams.show_params(self) -> None`

Log inversion parameters at INFO level.


## class `InversionResults`

Parsed GARPOS inversion results (ABIC, misfit, hyperparameters, loops).

**Fields**

| Name | Type | Description |
|---|---|---|
| `ABIC` | `float` |  |
| `misfit` | `float` |  |
| `inv_type` | `InversionType` |  |
| `lambda_0_squared` | `float` |  |
| `grad_lambda_squared` | `float` |  |
| `mu_t` | `float` |  |
| `mu_mt` | `float` |  |
| `delta_center_position` | `list` |  |
| `loop_data` | `list` |  |

## class `InversionType`

GARPOS inversion mode.

## class `ObservationData`

Observation data file schema
Example data:

,SET,LN,MT,TT,ResiTT,TakeOff,gamma,flag,ST,ant_e0,ant_n0,ant_u0,head0,pitch0,roll0,RT,ant_e1,ant_n1,ant_u1,head1,pitch1,roll1
0,S01,L01,M11,2.289306,0.0,0.0,0.0,False,30072.395125,-27.85291,1473.14423,14.73469,176.47,0.59,-1.39,30075.74594,-26.70998,1462.01803,14.32703,177.07,-0.5,-1.1
1,S01,L01,M13,3.12669,0.0,0.0,0.0,False,30092.395725,-22.08296,1412.88729,14.59827,188.24,0.41,-2.13,30096.58392,-22.3514,1401.77938,14.65401,190.61,-0.1,-2.14
2,S01,L01,M14,2.702555,0.0,0.0,0.0,False,30093.48579,-22.25377,1409.87685,14.67772,188.93,0.15,-1.7,30097.24985,-22.38458,1399.96509,14.55534,190.82,-0.39,-2.21
3,S01,L01,M14,2.68107,0.0,0.0,0.0,False,30102.396135,-23.25514,1387.38992,14.75355,192.39,0.1,-1.79,30106.13871,-23.96613,1378.4627,14.58135,192.92,0.21,-1.7
4,S01,L01,M11,2.218846,0.0,0.0,0.0,False,30103.4862,-23.57701,1384.73242,14.65861,192.62,-0.14,-1.5,30106.766555,-24.0478,1377.09283,14.68464,193.04,0.59,-1.81

**Fields**

| Name | Type | Description |
|---|---|---|
| `SET` | `<pandera.common.AnnotationInfo object at 0x310892f00>` |  |
| `LN` | `<pandera.common.AnnotationInfo object at 0x30fed37d0>` |  |
| `MT` | `<pandera.common.AnnotationInfo object at 0x30fed2600>` |  |
| `TT` | `<pandera.common.AnnotationInfo object at 0x30e672c90>` |  |
| `ST` | `<pandera.common.AnnotationInfo object at 0x30e673770>` |  |
| `RT` | `<pandera.common.AnnotationInfo object at 0x3108ee840>` |  |
| `ant_e0` | `<pandera.common.AnnotationInfo object at 0x3108ee510>` |  |
| `ant_n0` | `<pandera.common.AnnotationInfo object at 0x3108ee540>` |  |
| `ant_u0` | `<pandera.common.AnnotationInfo object at 0x3108ee5d0>` |  |
| `head0` | `<pandera.common.AnnotationInfo object at 0x3108ee960>` |  |
| `pitch0` | `<pandera.common.AnnotationInfo object at 0x3108ee9f0>` |  |
| `roll0` | `<pandera.common.AnnotationInfo object at 0x3108ee8d0>` |  |
| `ant_e1` | `<pandera.common.AnnotationInfo object at 0x3108ee900>` |  |
| `ant_n1` | `<pandera.common.AnnotationInfo object at 0x3108ee990>` |  |
| `ant_u1` | `<pandera.common.AnnotationInfo object at 0x3108eea20>` |  |
| `head1` | `<pandera.common.AnnotationInfo object at 0x3108eea50>` |  |
| `pitch1` | `<pandera.common.AnnotationInfo object at 0x3108eea80>` |  |
| `roll1` | `<pandera.common.AnnotationInfo object at 0x3108eeab0>` |  |
| `flag` | `<pandera.common.AnnotationInfo object at 0x3108eeae0>` |  |
| `lat` | `<pandera.common.AnnotationInfo object at 0x3108eeb10>` |  |
| `lon` | `<pandera.common.AnnotationInfo object at 0x3108eeb40>` |  |
| `gamma` | `<pandera.common.AnnotationInfo object at 0x3108eeb70>` |  |
| `ResiTT` | `<pandera.common.AnnotationInfo object at 0x3108eeba0>` |  |
| `TakeOff` | `<pandera.common.AnnotationInfo object at 0x3108eebd0>` |  |
