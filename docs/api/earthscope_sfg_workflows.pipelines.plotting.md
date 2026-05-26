# plotting

`earthscope_sfg_workflows.pipelines.plotting`

Pipeline result plotting utilities for campaign diagnostics.

## `get_rinex_timelast(rinex_asset: earthscope_sfg_workflows.data_mgmt.model.AssetEntry) -> datetime.datetime`

Gets the last timestamp from a RINEX file.

Parameters
----------
rinex_asset
    The RINEX asset entry.

Returns
-------
datetime.datetime
    The last timestamp in the RINEX file.

## `plot_kin_position_data(kin_position_data: earthscope_sfg_tools.tiledb_integration.arrays.TDBKinPositionArray, rinex_entries: list[earthscope_sfg_workflows.data_mgmt.model.AssetEntry] = None) -> None`

Plots KinPosition data over time.
This function plots KinPosition data over time, with each subplot
representing a unique month of data.

The function performs the following steps:
1. Extracts unique dates from the KinPosition data.
2. Organizes the dates by year and month.
3. Creates a subplot for each unique month.
4. Reads the KinPosition data for the date range of each month.
5. Plots the KinPosition data points as scatter plots.
6. Adds vertical lines to indicate daily and hourly markers.
7. Formats the x-axis with hourly ticks and rotates the labels for better
   readability.
8. Sets the title for each subplot to indicate the date range of the data.
9. Adjusts the layout and displays the plot.

Parameters
----------
kin_position_data
    An object containing KinPosition data with methods to retrieve unique dates and read data frames.
rinex_entries
    A list of RINEX asset entries, by default [].

## `to_timestamp(time: numpy.datetime64 | datetime.datetime) -> float`

Converts a numpy.datetime64 or datetime.datetime object to a UNIX timestamp.

Parameters
----------
time
    The time to convert.

Returns
-------
float
    The UNIX timestamp.
