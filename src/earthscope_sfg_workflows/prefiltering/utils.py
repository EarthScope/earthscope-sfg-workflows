"""Utility functions for pre-filtering shotdata before GARPOS inversion."""

from datetime import UTC, datetime

import numpy as np
import pandas as pd
import pymap3d as pm

from earthscope_sfg_workflows.logging import GarposLogger as logger
from earthscope_sfg_workflows.utils.model_update import validate_and_merge_config

from earthscope_sfg_tools.datamodels.metadata import Site, SurveyType
from .schemas import FilterConfig, FilterLevel


def filter_shotdata(
    survey_type: str | SurveyType,
    site: Site,
    shot_data: pd.DataFrame,
    kinPostionTDBUri: str,
    start_time: datetime,
    end_time: datetime,
    base_config: FilterConfig | None = None,
    custom_filters: dict | None = None,
) -> pd.DataFrame:
    """Filter shot data using the configured acoustic, ping-reply, distance, and PRIDE filters.

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
    """

    if base_config is None:
        filter_config = FilterConfig()

    initial_count = len(shot_data)
    new_shot_data_df = shot_data.copy()

    if custom_filters:
        filter_config = validate_and_merge_config(
            base_class=filter_config, override_config=custom_filters
        )
        logger.info(f"Using custom filter configuration: {filter_config}")

    """
    Apply acoustic diagnostics filtering. This is based on the SNR, DBV, and XC thresholds.
    """
    acoustic_config = filter_config.acoustic_filters
    if acoustic_config.enabled:
        level = acoustic_config.level
        match level:
            case FilterLevel.GOOD:
                new_shot_data_df = good_acoustic_diagnostics(new_shot_data_df)
            case FilterLevel.OK:
                new_shot_data_df = ok_acoustic_diagnostics(new_shot_data_df)
            case FilterLevel.DIFFICULT:
                new_shot_data_df = difficult_acoustic_diagnostics(new_shot_data_df)
            case _:
                logger.info("No acoustic filtering applied, using original shot data")

    """
    Apply ping replies filtering. This is based on the minimum number of replies.
    """
    ping_replies_config = filter_config.ping_replies
    if ping_replies_config.enabled:
        min_replies = ping_replies_config.min_replies
        new_shot_data_df = filter_ping_replies(new_shot_data_df, min_replies=min_replies)

    """
    Apply max distance from center filtering. This is typically used for center surveys.
    """
    if survey_type == SurveyType.CENTER:
        max_distance = filter_config.max_distance_from_center
        if max_distance.enabled:
            new_shot_data_df = filter_wg_distance_from_center(
                df=new_shot_data_df,
                array_center_lat=site.arrayCenter.latitude,
                array_center_lon=site.arrayCenter.longitude,
                max_distance_m=max_distance.max_distance_m,
            )
    """
    Apply PRIDE residuals filtering. This removes shots with high PRIDE residuals.
    """
    if filter_config.pride_residuals.enabled:
        new_shot_data_df = filter_pride_residuals(
            df=new_shot_data_df,
            kinPostionTDBUri=kinPostionTDBUri,
            start_time=start_time.replace(tzinfo=UTC),
            end_time=end_time.replace(tzinfo=UTC),
            max_wrms=filter_config.pride_residuals.max_residual_mm,
        )

    filtered_count = len(new_shot_data_df)
    logger.info(
        f"Filtered {initial_count - filtered_count} records from shot data based on filtering criteria: {filter_config}"
    )
    logger.info(f"Remaining shot data records: {filtered_count}")
    return new_shot_data_df


def filter_wg_distance_from_center(
    df: pd.DataFrame,
    array_center_lat: float,
    array_center_lon: float,
    max_distance_m: float = 150,
) -> pd.DataFrame:
    """Remove shots where the waveglider exceeds *max_distance_m* from the array center.

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
    """
    # Convert array center lat/lon to ECEF coordinates (assuming sea level)
    center_x, center_y, center_z = pm.geodetic2ecef(
        lat=array_center_lat, lon=array_center_lon, alt=0
    )

    def calc_horizontal_distance(row):
        # Calculate horizontal distance only (ignore Z/up component)
        dx = row["east0"] - center_x
        dy = row["north0"] - center_y

        horizontal_distance = np.sqrt(dx**2 + dy**2)
        return horizontal_distance

    # Calculate horizontal distance from center for each row
    df = df.copy()
    df["distance_from_center"] = df.apply(calc_horizontal_distance, axis=1)

    # Filter data
    filtered_df = df[df["distance_from_center"] <= max_distance_m].copy()

    logger.info(
        f"Removed {len(df) - len(filtered_df)} records > {max_distance_m}m horizontal distance from array center"
    )

    # Drop the temporary column if you don't want to keep it
    filtered_df = filtered_df.drop("distance_from_center", axis=1)
    return filtered_df


def filter_SNR(df, snr_min=12):
    """Remove shots below the SNR threshold.

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
    """
    if "snr" not in df.columns:
        logger.error("SNR column not found, skipping filter")
        return df

    initial_count = len(df)
    # Filter based on SNR theshold greater than or equal to snr_min
    df = df[df["snr"] >= snr_min].copy()

    logger.info(f"Removed {initial_count - len(df)} records with SNR < {snr_min}")
    return df


def filter_dbv(df, dbv_min=-36, dbv_max=-3):
    """Remove shots outside the DBV window.

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
    """
    if "dbv" not in df.columns:
        logger.error("DBV column not found, skipping filter")
        return df

    initial_count = len(df)
    df = df[(df["dbv"] >= dbv_min) & (df["dbv"] <= dbv_max)].copy()

    logger.info(f"Removed {initial_count - len(df)} records with DBV < {dbv_min} or > {dbv_max}")
    return df


def filter_xc(df, xc_min=45):
    """Remove shots below the XC (cross-correlation) threshold.

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
    """
    if "xc" not in df.columns:
        logger.error("XC column not found, skipping filter")
        return df

    initial_count = len(df)
    df = df[df["xc"] >= xc_min].copy()

    logger.info(f"Removed {initial_count - len(df)} records with XC < {xc_min}")
    return df


def filter_acoustic_diagnostics(df, snr_min=12, dbv_min=-36, dbv_max=-3, xc_min=45):
    """Remove shots that fail any of the SNR, DBV, or XC thresholds.

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
    """

    initial_count = len(df)
    df = filter_SNR(df=df, snr_min=snr_min)
    df = filter_dbv(df=df, dbv_min=dbv_min, dbv_max=dbv_max)
    df = filter_xc(df=df, xc_min=xc_min)

    logger.info(f"Total acoustic diagnostic filtering removed {initial_count - len(df)} records")
    return df


def good_acoustic_diagnostics(df):
    """Apply "good" level acoustic-diagnostics thresholds.

    Parameters
    ----------
    df : pd.DataFrame
        Shot data to filter.

    Returns
    -------
    pd.DataFrame
        Filtered shot data retaining only shots with good acoustic quality
        (SNR > 20, DBV −3 to −26, XC > 60).
    """
    return filter_acoustic_diagnostics(df, snr_min=20, dbv_min=-26, dbv_max=-3, xc_min=60)


def ok_acoustic_diagnostics(df):
    """Apply "ok" level acoustic-diagnostics thresholds.

    Parameters
    ----------
    df : pd.DataFrame
        Shot data to filter.

    Returns
    -------
    pd.DataFrame
        Filtered shot data retaining shots with acceptable acoustic quality
        (SNR >= 12, DBV −36 to −3, XC >= 45).
    """
    return filter_acoustic_diagnostics(df, snr_min=12, dbv_min=-36, dbv_max=-3, xc_min=45)


def difficult_acoustic_diagnostics(df):
    """Apply "difficult" level acoustic-diagnostics thresholds (most permissive).

    Parameters
    ----------
    df : pd.DataFrame
        Shot data to filter.

    Returns
    -------
    pd.DataFrame
        Filtered shot data using the default (loosest) SNR, DBV, and XC
        thresholds.
    """
    return filter_acoustic_diagnostics(df)


def filter_ping_replies(df, min_replies=3):
    """Require a minimum number of replies per ping.

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
    """
    if "pingTime" not in df.columns:
        logger.error("pingTime column not found, skipping filter")
        return df

    # Count replies per ping time
    ping_counts = df["pingTime"].value_counts()

    # Get ping times that have at least min_replies
    valid_ping_times = ping_counts[ping_counts >= min_replies].index

    # Filter dataframe to only include pings with enough replies
    filtered_df = df[df["pingTime"].isin(valid_ping_times)].copy()

    removed_pings = len(ping_counts) - len(valid_ping_times)
    removed_records = len(df) - len(filtered_df)

    logger.info(
        f"Removed {removed_pings} ping times with < {min_replies} replies ({removed_records} total records)"
    )

    return filtered_df


def filter_pride_residuals(
    df, kinPostionTDBUri: str, start_time: datetime, end_time: datetime, max_wrms=15
):
    """Remove shots that coincide with high PRIDE-PPP WRMS epochs.

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
    """

    # Convert tileDB array to dataframe
    from earthscope_sfg_tools.tiledb_integration import TDBKinPositionArray

    pride_data = TDBKinPositionArray(kinPostionTDBUri)
    ppp_df = pride_data.read_df(start=start_time, end=end_time)
    if ppp_df.empty:
        logger.error("No Pride PPP data found, skipping residual filter")
        return df

    # Check if wrms column exists
    if "wrms" not in ppp_df.columns:
        logger.error("WRMS column not found in Pride data, skipping residual filter")
        return df

    # Filter Pride data for high WRMS values
    high_wrms_times = ppp_df[ppp_df["wrms"] > max_wrms]["time"].tolist()

    if not high_wrms_times:
        logger.info(f"No Pride PPP data exceeds WRMS threshold of {max_wrms}mm")
        return df

    # Convert Pride PPP datetime to Unix timestamp to match pingTime format
    high_wrms_unix_times = []
    for bad_time in high_wrms_times:
        if pd.isna(bad_time):
            continue
        # Convert datetime to Unix timestamp
        unix_timestamp = bad_time.timestamp()
        high_wrms_unix_times.append(unix_timestamp)

    # Create exclusion time ranges using Unix timestamps
    exclusion_ranges = []
    time_buffer_seconds = 1  # 1 second buffer before/after

    for bad_unix_time in high_wrms_unix_times:
        exclusion_ranges.append(
            {
                "start": bad_unix_time - time_buffer_seconds,
                "end": bad_unix_time + time_buffer_seconds,
            }
        )

    # Filter shot data - remove shots that fall within any exclusion range
    initial_count = len(df)
    mask = pd.Series(True, index=df.index)  # Start with all True

    for time_range in exclusion_ranges:
        # Mark shots within this exclusion range as False (pingTime is Unix timestamp)
        in_range = (df["pingTime"] >= time_range["start"]) & (df["pingTime"] <= time_range["end"])
        mask = mask & ~in_range  # Remove shots in this range

    filtered_df = df[mask].copy()

    removed_count = initial_count - len(filtered_df)
    logger.info(
        f"Removed {removed_count} shot records due to high WRMS (>{max_wrms}mm) in Pride PPP data"
    )
    logger.info(f"Used {len(exclusion_ranges)} time exclusion ranges with ±1s buffer")

    return filtered_df  # Return filtered_df instead of original df
