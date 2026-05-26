# utils

`earthscope_sfg_workflows.prefiltering.utils`

Utility functions for pre-filtering shotdata before GARPOS inversion.

## `difficult_acoustic_diagnostics(df)`

Apply "difficult" level acoustic-diagnostics thresholds (most permissive).

Parameters
----------
df : pd.DataFrame
    Shot data to filter.

Returns
-------
pd.DataFrame
    Filtered shot data using the default (loosest) SNR, DBV, and XC
    thresholds.

## `filter_SNR(df, snr_min=12)`

Remove shots below the SNR threshold.

Quality reference levels:

- GOOD: SNR > 20
- OK: SNR 12–20
- DIFFICULT (default): SNR < 12

Parameters
----------
df : pd.DataFrame
    Shot data containing an ``snr`` column.
snr_min : int or float, optional
    Minimum SNR value (inclusive). Default is ``12``.

Returns
-------
pd.DataFrame
    Filtered shot data. Returned unchanged if ``snr`` column is absent.

## `filter_acoustic_diagnostics(df, snr_min=12, dbv_min=-36, dbv_max=-3, xc_min=45)`

Remove shots that fail any of the SNR, DBV, or XC thresholds.

Quality threshold reference:

- Good: SNR > 20, DBV (−3 to −26), XC > 60
- OK: SNR 12–20, DBV (−26 to −36), XC 45–60
- Difficult (default): SNR < 12, DBV (< −36 or > −3), XC < 45

Parameters
----------
df : pd.DataFrame
    Shot data containing ``snr``, ``dbv``, and ``xc`` columns.
snr_min : int or float, optional
    Minimum SNR threshold. Default is ``12``.
dbv_min : int or float, optional
    Minimum DBV threshold. Default is ``-36``.
dbv_max : int or float, optional
    Maximum DBV threshold. Default is ``-3``.
xc_min : int or float, optional
    Minimum XC threshold. Default is ``45``.

Returns
-------
pd.DataFrame
    Filtered shot data.

## `filter_dbv(df, dbv_min=-36, dbv_max=-3)`

Remove shots outside the DBV window.

Quality reference levels:

- GOOD: DBV −3 to −26
- OK: DBV −26 to −36
- DIFFICULT (default): DBV < −36 or > −3

Parameters
----------
df : pd.DataFrame
    Shot data containing a ``dbv`` column.
dbv_min : int or float, optional
    Minimum DBV value (inclusive). Default is ``-36``.
dbv_max : int or float, optional
    Maximum DBV value (inclusive). Default is ``-3``.

Returns
-------
pd.DataFrame
    Filtered shot data. Returned unchanged if ``dbv`` column is absent.

## `filter_ping_replies(df, min_replies=3)`

Require a minimum number of replies per ping.

Parameters
----------
df : pd.DataFrame
    Shot data with a ``pingTime`` column.
min_replies : int, optional
    Minimum reply count required to retain a ping. Default is ``3``.

Returns
-------
pd.DataFrame
    Filtered shot data. Returned unchanged if ``pingTime`` column is
    absent.

## `filter_pride_residuals(df, kinPostionTDBUri: str, start_time: datetime.datetime, end_time: datetime.datetime, max_wrms=15)`

Remove shots that coincide with high PRIDE-PPP WRMS epochs.

Parameters
----------
df : pd.DataFrame
    Shot data with a ``pingTime`` column containing Unix timestamps.
kinPostionTDBUri : str
    URI of the kinematic-position TileDB array containing PRIDE WRMS data.
start_time : datetime
    Start of the time window to read from the TileDB array.
end_time : datetime
    End of the time window to read from the TileDB array.
max_wrms : int or float, optional
    Maximum allowable WRMS value in millimetres. Shots within ±1 second
    of epochs that exceed this threshold are removed. Default is ``15``.

Returns
-------
pd.DataFrame
    Filtered shot data. Returned unchanged if the TileDB array is empty or
    lacks a ``wrms`` column.

## `filter_shotdata(survey_type: str | earthscope_sfg_tools.datamodels.metadata.earthscope.campaign.SurveyType, site: earthscope_sfg_tools.datamodels.metadata.earthscope.site.Site, shot_data: pandas.core.frame.DataFrame, kinPostionTDBUri: str, start_time: datetime.datetime, end_time: datetime.datetime, base_config: earthscope_sfg_workflows.prefiltering.schemas.FilterConfig | None = None, custom_filters: dict | None = None) -> pandas.core.frame.DataFrame`

Filter shot data using the configured acoustic, ping-reply, distance, and PRIDE filters.

Parameters
----------
survey_type : str or SurveyType
    Survey type; determines whether the distance-from-center filter is
    applied (center surveys only).
site : Site
    Site metadata providing the array center coordinates.
shot_data : pd.DataFrame
    Raw shot data to be filtered.
kinPostionTDBUri : str
    URI of the kinematic-position TileDB array used by the PRIDE residuals
    filter.
start_time : datetime
    Start of the survey window.
end_time : datetime
    End of the survey window.
base_config : FilterConfig or None, optional
    Base filter configuration. When ``None`` a default ``FilterConfig`` is
    used. Default is ``None``.
custom_filters : dict or None, optional
    Nested override mapping applied on top of *base_config*. Default is
    ``None``.

Returns
-------
pd.DataFrame
    Filtered shot data.

## `filter_wg_distance_from_center(df: pandas.core.frame.DataFrame, array_center_lat: float, array_center_lon: float, max_distance_m: float = 150) -> pandas.core.frame.DataFrame`

Remove shots where the waveglider exceeds *max_distance_m* from the array center.

Typically applied to center surveys only.

Parameters
----------
df : pd.DataFrame
    Shot data containing ``east0`` and ``north0`` ECEF coordinate columns.
array_center_lat : float
    Geodetic latitude of the array center in decimal degrees.
array_center_lon : float
    Geodetic longitude of the array center in decimal degrees.
max_distance_m : float, optional
    Maximum horizontal distance from the array center in metres. Default
    is ``150``.

Returns
-------
pd.DataFrame
    Filtered shot data with the temporary ``distance_from_center`` column
    removed.

## `filter_xc(df, xc_min=45)`

Remove shots below the XC (cross-correlation) threshold.

Quality reference levels:

- GOOD: XC > 60
- OK: XC 45–60
- DIFFICULT (default): XC < 45

Parameters
----------
df : pd.DataFrame
    Shot data containing an ``xc`` column.
xc_min : int or float, optional
    Minimum XC value (inclusive). Default is ``45``.

Returns
-------
pd.DataFrame
    Filtered shot data. Returned unchanged if ``xc`` column is absent.

## `good_acoustic_diagnostics(df)`

Apply "good" level acoustic-diagnostics thresholds.

Parameters
----------
df : pd.DataFrame
    Shot data to filter.

Returns
-------
pd.DataFrame
    Filtered shot data retaining only shots with good acoustic quality
    (SNR > 20, DBV −3 to −26, XC > 60).

## `ok_acoustic_diagnostics(df)`

Apply "ok" level acoustic-diagnostics thresholds.

Parameters
----------
df : pd.DataFrame
    Shot data to filter.

Returns
-------
pd.DataFrame
    Filtered shot data retaining shots with acceptable acoustic quality
    (SNR >= 12, DBV −36 to −3, XC >= 45).
