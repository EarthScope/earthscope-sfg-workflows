# shotdata_gnss_refinement

`earthscope_sfg_workflows.pipelines.shotdata_gnss_refinement`

Refinement of acoustic shotdata positions using high-precision GNSS kinematic solutions.

## `analyze_offsets(merged_positions: pandas.core.frame.DataFrame) -> None`

Analyzes the offsets between smoothed and original antenna positions.

Calculates the absolute differences for the X, Y, and Z coordinates
between the columns 'ant_x_smoothed', 'ant_y_smoothed', 'ant_z_smoothed'
and their respective original columns 'ant_x', 'ant_y', 'ant_z'.
Computes summary statistics (count, mean, std, min, 25%, 50%, 75%, max)
for each offset and prints the results in a formatted table.

Parameters
----------
merged_positions
    DataFrame containing the columns 'ant_x', 'ant_y', 'ant_z', 'ant_x_smoothed', 'ant_y_smoothed', and 'ant_z_smoothed'.

Returns
-------
None
    Prints the summary statistics to the console. If the DataFrame is empty, prints a message and returns.

## `combine_data(imu_position_data: pandera.typing.pandas.DataFrame[earthscope_sfg_tools.datamodels.observationdata.garpos.observables.IMUPositionDataFrame], ppp_position_data: pandera.typing.pandas.DataFrame[earthscope_sfg_tools.datamodels.observationdata.parsing.ppp.KinPositionDataFrame]) -> pandas.core.frame.DataFrame`

Combines IMU position and PPP position data into a single DataFrame.

This is done with a specified column order.

Parameters
----------
imu_position_data
    DataFrame containing IMU position data with columns matching the expected column order.
ppp_position_data
    DataFrame containing PPP position data with columns matching the expected column order.

Returns
-------
pd.DataFrame
    Combined DataFrame containing both position and GPS data, ordered by time and columns. Note: Rows with NaN values are retained to preserve kinematic velocity information.

## `filter_spatial_outliers(df: pandas.core.frame.DataFrame, radius: float = 5000) -> pandas.core.frame.DataFrame`

Filters out rows that are outside a specified radius from the median ECEF position.

Parameters
----------
df
    Input DataFrame containing ECEF position columns 'ant_x', 'ant_y', 'ant_z'.
radius
    Radius in meters to define the acceptable range from the median position.

Returns
-------
pd.DataFrame
    Filtered DataFrame with rows outside the specified radius removed.

## `interpolate_enu(tenu_l: numpy.ndarray, enu_l_sig: numpy.ndarray, tenu_r: numpy.ndarray, enu_r_sig: numpy.ndarray) -> numpy.ndarray`

Interpolate the enu values between the left and right enu values.

Parameters
----------
tenu_l
    The left enu time values in unix epoch.
enu_l_sig
    The standard deviation of the left enu values in ECEF coordinates.
tenu_r
    The right enu time values in unix epoch.
enu_r_sig
    The standard deviation of the right enu values in ECEF coordinates.

Returns
-------
np.ndarray
    The interpolated enu values and the standard deviation of the interpolated enu values predicted at the time values from tenu_r.

## `interpolate_enu_kernelridge(kin_position_data: numpy.ndarray, shot_data: numpy.ndarray, lengthscale: float = 0.5) -> numpy.ndarray`

Interpolate the enu values using Kernel Ridge Regression.

Parameters
----------
kin_position_data
    The kinematic position data.
shot_data
    The shot data.
lengthscale
    The length scale for the kernel, by default 0.5.

Returns
-------
np.ndarray
    The interpolated enu values at the time values from tenu_r.

## `interpolate_enu_radius_regression(kin_position_df: pandas.core.frame.DataFrame, shotdata_df: pandas.core.frame.DataFrame, lengthscale: float = 0.1) -> pandas.core.frame.DataFrame`

Interpolate the enu values using Radius Neighbors Regression.

Parameters
----------
kin_position_df
    The kinematic position data.
shotdata_df
    The shot data.
lengthscale
    The length scale for the kernel, by default 0.1.

Returns
-------
pd.DataFrame
    The updated shotdata DataFrame.

## `main(shotdata: pandas.core.frame.DataFrame, kin_positions: pandas.core.frame.DataFrame, positions_data: pandas.core.frame.DataFrame, gnss_pos_psd: float | numpy.ndarray = 3.125e-05, vel_psd: float | numpy.ndarray = 0.0025, cov_err: float | numpy.ndarray = 0.25, start_dt: float | pandas._libs.tslibs.timestamps.Timestamp = 0.05, filter_radius: float = 5000, prepare_position_data: bool = True) -> pandas.core.frame.DataFrame`

Refines shotdata using GNSS and IMU data through Kalman filtering and smoothing.

Parameters
----------
shotdata
    DataFrame containing shot event data to be refined.
kin_positions
    DataFrame containing kinematic GNSS positions.
positions_data
    DataFrame containing original positions data.
gnss_pos_psd
    GNSS position process noise spectral density (default: constants.GNSS_POS_PSD).
vel_psd
    Velocity process noise spectral density (default: constants.VEL_PSD).
cov_err
    Initial covariance error (default: constants.COV_ERR).
start_dt
    Start datetime for filtering (default: constants.START_DT).
filter_radius
    Radius for spatial outlier filtering in meters (default: 5000).

Returns
-------
pd.DataFrame
    Updated shotdata DataFrame with refined positions and antenna offsets.

Notes
-----
- Combines positions and kinematic GNSS data, filters spatial outliers, and applies Kalman filter smoothing.
- Merges smoothed results with original and kinematic positions for offset analysis.
- Updates shotdata with refined positions and prints summary statistics of antenna offsets.

## `merge_shotdata_kinposition(shotdata_pre: earthscope_sfg_tools.tiledb_integration.arrays.TDBShotDataArray, shotdata: earthscope_sfg_tools.tiledb_integration.arrays.TDBShotDataArray, kin_position: earthscope_sfg_tools.tiledb_integration.arrays.TDBKinPositionArray, position_data: earthscope_sfg_tools.tiledb_integration.arrays.TDBIMUPositionArray, dates: list[numpy.datetime64], filter_radius: float = 5000) -> earthscope_sfg_tools.tiledb_integration.arrays.TDBShotDataArray`

Merge the shotdata and kin_position data.

Parameters
----------
shotdata_pre
    The DFOP00 data.
shotdata
    The shotdata array to write to.
kin_position
    The TileDB KinPosition array.
position_data
    The TileDB IMU position array.
dates
    The dates to merge.
filter_radius
    Radius for spatial outlier filtering in meters, by default 5000.

Returns
-------
TDBShotDataArray
    The updated shotdata array.

## `merge_shotdata_kinposition_radius_regression(shotdata_pre: earthscope_sfg_tools.tiledb_integration.arrays.TDBShotDataArray, shotdata: earthscope_sfg_tools.tiledb_integration.arrays.TDBShotDataArray, kin_position: earthscope_sfg_tools.tiledb_integration.arrays.TDBKinPositionArray, dates: list[numpy.datetime64], lengthscale: float = 0.1, plot: bool = False) -> earthscope_sfg_tools.tiledb_integration.arrays.TDBShotDataArray`

Merge the shotdata and kin_position data.

Parameters
----------
shotdata_pre
    The DFOP00 data.
shotdata
    The shotdata array to write to.
kin_position
    The TileDB KinPosition array.
dates
    The dates to merge.
lengthscale
    The length scale for the kernel, by default 0.1.
plot
    Plot the interpolated values, by default False.

Returns
-------
TDBShotDataArray
    The updated shotdata array.

## `merge_shotdata_qc(shotdata_pre: earthscope_sfg_tools.tiledb_integration.arrays.TDBShotDataArray, shotdata: earthscope_sfg_tools.tiledb_integration.arrays.TDBShotDataArray, kin_position: earthscope_sfg_tools.tiledb_integration.arrays.TDBKinPositionArray, dates: list[numpy.datetime64]) -> None`

Merge the shotdata and kin_position data for QC purposes.

Parameters
----------
shotdata_pre
    The DFOP00 data.
shotdata
    The shotdata array to write to.
kin_position
    The TileDB KinPosition array.
dates
    The dates to merge.

Returns
-------
None
    This function writes the updated shotdata to the provided TDBShotDataArray.

## `prepare_kinematic_data(kin_positions: pandera.typing.pandas.DataFrame[earthscope_sfg_tools.datamodels.observationdata.parsing.ppp.KinPositionDataFrame]) -> pandas.core.frame.DataFrame`

Prepares kinematic GPS data for Kalman filtering.

This is done by computing velocities and filtering outliers.

This function takes a DataFrame containing kinematic GPS positions and
processes it as follows:
- Copies the input DataFrame to avoid modifying the original.
- Renames position columns ('east', 'north', 'up') to antenna
  coordinates ('ant_x', 'ant_y', 'ant_z').
- Initializes velocity columns ('east', 'north', 'up') with NaN values.
- Adds uncertainty and correlation columns with default values.
- Calculates velocity components by differentiating position over time.
- Filters out rows with velocity spikes using a z-score threshold.
- Prints the reduction in data size after filtering.

Parameters
----------
kin_positions
    DataFrame containing kinematic GPS positions with columns 'east', 'north', 'up', and 'time'.

Returns
-------
pd.DataFrame
    Processed DataFrame with velocity columns and outlier rows removed.

## `prepare_positions_data(positions_data: pandera.typing.pandas.DataFrame[earthscope_sfg_tools.datamodels.observationdata.garpos.observables.IMUPositionDataFrame]) -> pandas.core.frame.DataFrame`

Prepares IMU positions data for Kalman filtering.

This is done by converting geodetic coordinates to ECEF, computing median
positions, and adding velocity and uncertainty columns.

Parameters
----------
positions_data
    DataFrame containing IMU position and velocity data with columns: 'latitude', 'longitude', 'height', 'eastVelocity', 'northVelocity', 'upVelocity', and their respective standard deviations.

Returns
-------
pd.DataFrame
    A copy of the input DataFrame with additional columns: - 'ant_x', 'ant_y', 'ant_z': ECEF coordinates - 'east', 'north', 'up': velocity components - 'ant_sigx', 'ant_sigy', 'ant_sigz': uncertainties in position - 'rho_xy', 'rho_xz', 'rho_yz': correlation coefficients (set to 0) - 'east_sig', 'north_sig', 'up_sig': uncertainties in velocity - 'v_sden', 'v_sdeu', 'v_sdnu': additional velocity uncertainty columns (set to 0)

Notes
-----
Also sets global variables MEDIAN_EAST_POSITION, MEDIAN_NORTH_POSITION, and MEDIAN_UP_POSITION
to the median ECEF coordinates.

## `run_kalman_filter_and_smooth(df_all: pandas.core.frame.DataFrame, start_dt: float, gnss_pos_psd: float, vel_psd: float, cov_err: float) -> pandas.core.frame.DataFrame`

Runs a Kalman filter simulation on GNSS shot data and processes the results.

Parameters
----------
df_all
    Input DataFrame containing GNSS shot data. Rows with NaN values are dropped before processing.
start_dt
    Initial time delta for the Kalman filter simulation.
gnss_pos_psd
    Position process spectral density for GNSS measurements.
vel_psd
    Velocity process spectral density for the filter.
cov_err
    Initial covariance error for the filter.

Returns
-------
pd.DataFrame
    DataFrame containing smoothed GNSS positions and associated covariance statistics. If the input DataFrame is empty after dropping NaNs, returns an empty DataFrame.

## `shotdata_to_imu_position_df(shotdata_df: pandas.core.frame.DataFrame) -> pandas.core.frame.DataFrame`

Converts a ShotDataFrame DataFrame into an IMUPositionDataFrame DataFrame by splitting *0 (pingTime) and *1 (returnTime) fields,
renaming to IMUPositionDataFrame schema, concatenating, sorting by time, and dropping acoustic fields.

Parameters
----------
shotdata_df
    DataFrame with ShotDataFrame schema.

Returns
-------
pd.DataFrame
    DataFrame with IMUPositionDataFrame schema.

## `update_shotdata_with_smoothed_positions(shotdata: pandas.core.frame.DataFrame, smoothed_results: pandas.core.frame.DataFrame) -> pandas.core.frame.DataFrame`

Interpolates smoothed positions onto shotdata ping and return times.

Parameters
----------
shotdata
    The shotdata DataFrame.
smoothed_results
    The smoothed results DataFrame.

Returns
-------
pd.DataFrame
    The updated shotdata DataFrame.
