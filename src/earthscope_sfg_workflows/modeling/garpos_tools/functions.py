"""Low-level math and signal-processing helpers for GARPOS inversion."""

import math
import sys
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import pymap3d as pm
import seaborn as sns
from scipy.stats import hmean as harmonic_mean

sns.set_theme()


from earthscope_sfg_workflows.logging import GarposLogger as logger  # noqa: E402

from .schemas import (  # noqa: E402
    GarposInput,
    GarposObservationOutput,
    GPPositionENU,
    GPPositionLLH,
    GPTransponder,
    ObservationData,
)

colors = [
    "blue",
    "green",
    "red",
    "cyan",
    "magenta",
    "yellow",
    "black",
    "brown",
    "orange",
    "pink",
]


def xyz2enu(x, y, z, lat0, lon0, hgt0, inv=1, **kwargs):
    """
    Rotates the vector of positions XYZ and covariance to
    the local east-north-up system at latitude and longitude
    (or XYZ coordinates) specified in origin.
    if inv = -1. then enu -> xyz

    Parameters
    ----------
    x :
    y :
    z :
        Position in ECEF (if inv=-1, in ENU)
    lat0 :
    lon0 :
    Hgt0 :
        Origin for the local system in degrees.
    inv :
        Switch (1: XYZ -> ENU, -1: ENU -> XYZ)

    Returns
    -------
    e
    """

    if inv != 1 and inv != -1:
        print("error in xyz2enu : ", inv)
        sys.exit(1)

    lat = lat0 * math.pi / 180.0 * inv
    lon = lon0 * math.pi / 180.0 * inv

    sphi = math.sin(lat)
    cphi = math.cos(lat)
    slmb = math.sin(lon)
    clmb = math.cos(lon)

    T1 = [-slmb, clmb, 0]
    T2 = [-sphi * clmb, -sphi * slmb, cphi]
    T3 = [cphi * clmb, cphi * slmb, sphi]

    e = x * T1[0] + y * T1[1] + z * T1[2]
    n = x * T2[0] + y * T2[1] + z * T2[2]
    u = x * T3[0] + y * T3[1] + z * T3[2]

    return e, n, u


class CoordTransformer:
    """
    A class to transform coordinates between different systems.

    Attributes
    ----------
    lat0 : float
        Latitude of the reference point.
    lon0 : float
        Longitude of the reference point.
    hgt0 : float
        Height of the reference point.
    X0 : float
        X coordinate of the reference point in ECEF.
    Y0 : float
        Y coordinate of the reference point in ECEF.
    Z0 : float
        Z coordinate of the reference point in ECEF.

    Methods
    -------
    XYZ2ENU(X, Y, Z, **kwargs)
        Converts ECEF coordinates to ENU coordinates.
    LLH2ENU(lat, lon, hgt, **kwargs)
        Converts geodetic coordinates (latitude, longitude, height) to ENU coordinates.
    LLH2ENU_vec(lat: np.ndarray, lon: np.ndarray, hgt: np.ndarray) -> Tuple[np.ndarray, np.ndarray, np.ndarray]
        Converts arrays of geodetic coordinates to ENU coordinates.
    ECEF2ENU_vec(X: np.ndarray, Y: np.ndarray, Z: np.ndarray) -> Tuple[np.ndarray, np.ndarray, np.ndarray]
        Converts arrays of ECEF coordinates to ENU coordinates.
    """

    def __init__(self, latitude: float, longitude: float, elevation: float):
        """
        Initialize the object with a position in latitude, longitude, and height.

        Parameters
        ----------
        pos_llh : list | PositionLLH
            The position in latitude, longitude, and height.
            It can be either a list [latitude, longitude, height]
            or an instance of PositionLLH class.
        """

        self.lat0 = latitude
        self.lon0 = longitude
        self.hgt0 = elevation

        self.X0, self.Y0, self.Z0 = pm.geodetic2ecef(self.lat0, self.lon0, self.hgt0)

    def XYZ2ENU(self, X: float, Y: float, Z: float) -> tuple[float, float, float]:
        """
        Convert Cartesian coordinates (X, Y, Z) to East-North-Up (ENU) coordinates.

        Parameters
        ----------
        X : float
            X coordinate in the Cartesian system.
        Y : float
            Y coordinate in the Cartesian system.
        Z : float
            Z coordinate in the Cartesian system.

        Returns
        -------
        tuple
            A tuple containing the East (e), North (n), and Up (u) coordinates.
        """

        dX, dY, dZ = X - self.X0, Y - self.Y0, Z - self.Z0
        e, n, u = xyz2enu(
            **{
                "x": dX,
                "y": dY,
                "z": dZ,
                "lat0": self.lat0,
                "lon0": self.lon0,
                "hgt0": self.hgt0,
            }
        )

        return e, n, u

    def LLH2ENU(self, lat: float, lon: float, hgt: float) -> tuple[float, float, float]:
        """
        Convert latitude, longitude, and height (LLH) to East, North, Up (ENU) coordinates.
        This function converts geodetic coordinates (latitude, longitude, height) to local
        tangent plane coordinates (East, North, Up) relative to a reference point.

        Parameters
        ----------
        lat : float
            Latitude in degrees.
        lon : float
            Longitude in degrees.
        hgt : float
            Height in meters.

        Returns
        -------
        Tuple[float, float, float]
            A tuple containing the East, North, and Up coordinates in meters.
        """

        X, Y, Z = pm.geodetic2ecef(lat, lon, hgt)
        dX, dY, dZ = X - self.X0, Y - self.Y0, Z - self.Z0
        e, n, u = xyz2enu(
            **{
                "x": dX,
                "y": dY,
                "z": dZ,
                "lat0": self.lat0,
                "lon0": self.lon0,
                "hgt0": self.hgt0,
            }
        )

        return e, n, u

    def LLH2ENU_vec(
        self, lat: np.ndarray, lon: np.ndarray, hgt: np.ndarray
    ) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        """
        Convert latitude, longitude, and height (LLH) coordinates to East-North-Up (ENU) coordinates.

        Parameters
        ----------
        lat : np.ndarray
            Array of latitudes in degrees.
        lon : np.ndarray
            Array of longitudes in degrees.
        hgt : np.ndarray
            Array of heights in meters.

        Returns
        -------
        Tuple[np.ndarray, np.ndarray, np.ndarray]
            Tuple containing arrays of East, North, and Up coordinates in meters.
        """

        X, Y, Z = pm.geodetic2ecef(lat, lon, hgt)
        dX, dY, dZ = X - self.X0, Y - self.Y0, Z - self.Z0
        e, n, u = xyz2enu(
            **{
                "x": dX,
                "y": dY,
                "z": dZ,
                "lat0": self.lat0,
                "lon0": self.lon0,
                "hgt0": self.hgt0,
            }
        )

        return e, n, u

    def ECEF2ENU_vec(
        self, X: np.ndarray, Y: np.ndarray, Z: np.ndarray
    ) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        """
        Convert ECEF coordinates to ENU coordinates.

        Parameters
        ----------
        X : np.ndarray
            Array of X coordinates in meters.
        Y : np.ndarray
            Array of Y coordinates in meters.
        Z : np.ndarray
            Array of Z coordinates in meters.

        Returns
        -------
        Tuple[np.ndarray, np.ndarray, np.ndarray]
            Tuple containing arrays of East, North, and Up coordinates in meters.
        """
        dX, dY, dZ = X - self.X0, Y - self.Y0, Z - self.Z0
        e, n, u = xyz2enu(
            **{
                "x": dX,
                "y": dY,
                "z": dZ,
                "lat0": self.lat0,
                "lon0": self.lon0,
                "hgt0": self.hgt0,
            }
        )

        return e, n, u


def avg_transponder_position(
    transponders: list[GPTransponder],
) -> tuple[GPPositionENU, GPPositionLLH]:
    """
    Calculate the average position of the transponders.

    Parameters
    ----------
    transponders : List[Transponder]
        A list of transponders.

    Returns
    -------
    Tuple[PositionENU, PositionLLH]
        A tuple containing the average position in ENU and LLH coordinates.
    """
    pos_array_llh = []
    pos_array_enu = []
    for transponder in transponders:
        pos_array_llh.append(
            [
                transponder.position_llh.latitude,
                transponder.position_llh.longitude,
                transponder.position_llh.height,
            ]
        )
        pos_array_enu.append(transponder.position_enu.get_position())
    avg_pos_llh = np.mean(pos_array_llh, axis=0).tolist()
    avg_pos_enu = np.mean(pos_array_enu, axis=0).tolist()

    out_pos_llh = GPPositionLLH(
        latitude=avg_pos_llh[0], longitude=avg_pos_llh[1], height=avg_pos_llh[2]
    )
    out_pos_enu = GPPositionENU(east=avg_pos_enu[0], north=avg_pos_enu[1], up=avg_pos_enu[2])

    return out_pos_enu, out_pos_llh


def enu_to_ecef_llh(
    coord_transformer: CoordTransformer, east: float, north: float, up: float
) -> tuple[float, float, float, float, float, float]:
    """Convert a local ENU position (relative to `coord_transformer`'s origin) into
    absolute ECEF XYZ and geodetic lat/lon/height, GNATSS-style.

    `coord_transformer.hgt0` is the ellipsoidal height GARPOS assigned to the local
    origin (`-site.localGeoidHeight`, i.e. the geoid undulation at the array center
    under the assumption the array center sits at ~0 m MSL). We reuse it as the geoid
    undulation to convert the derived ellipsoidal height into an MSL height comparable
    to GNATSS's `Hgt.msl`.

    Returns
    -------
    tuple
        `(X, Y, Z, latitude, longitude, height_msl)`.
    """
    X, Y, Z = pm.enu2ecef(
        east, north, up, coord_transformer.lat0, coord_transformer.lon0, coord_transformer.hgt0
    )
    lat, lon, height_ellipsoidal = pm.ecef2geodetic(X, Y, Z)
    height_msl = height_ellipsoidal - coord_transformer.hgt0
    return X, Y, Z, lat, lon, height_msl


def _enu_rotation_matrix(lat0: float, lon0: float) -> np.ndarray:
    """Return R such that `[e, n, u] = R @ [dX, dY, dZ]` (ECEF delta -> ENU)."""
    lat = math.radians(lat0)
    lon = math.radians(lon0)
    sphi, cphi = math.sin(lat), math.cos(lat)
    slmb, clmb = math.sin(lon), math.cos(lon)
    return np.array(
        [
            [-slmb, clmb, 0.0],
            [-sphi * clmb, -sphi * slmb, cphi],
            [cphi * clmb, cphi * slmb, sphi],
        ]
    )


def enu_sigma_to_ecef_sigma(
    coord_transformer: CoordTransformer, sigma_e: float, sigma_n: float, sigma_u: float
) -> tuple[float, float, float]:
    """Propagate diagonal ENU sigmas into approximate ECEF X/Y/Z sigmas.

    Ignores ENU cross-covariance terms (diagonal-only approximation) — good
    enough for a display-only uncertainty, not for rigorous error propagation.

    Returns
    -------
    tuple
        `(sigma_x, sigma_y, sigma_z)`.
    """
    R = _enu_rotation_matrix(coord_transformer.lat0, coord_transformer.lon0)
    cov_enu = np.diag([sigma_e**2, sigma_n**2, sigma_u**2])
    cov_ecef = R.T @ cov_enu @ R
    return tuple(np.sqrt(np.diag(cov_ecef)))


def garpos_results_to_gnatss_format(
    results: GarposInput, coord_transformer: CoordTransformer
) -> pd.DataFrame:
    """Recast a solved `GarposInput` into a GNATSS-style comparison table.

    GARPOS reports positions as local ENU offsets from the site's local tangent-plane
    origin (array center); GNATSS reports absolute ECEF XYZ plus geodetic
    lat/lon/Hgt.msl and `del_e/del_n/del_u` displacement from the a priori position.
    This builds one row per transponder (id = transponder id) plus one row for the
    array center (id = "ARRAY"), converted into that same absolute format so results
    can be compared directly.

    Per GARPOS's own internal position formula (`mp_estimation.py`:
    `sta0 = mp[transponder_block] + mp[center_block]`), each transponder's
    `position_enu` is only its own solved parameter — the shared array-wide
    correction `delta_center_position` (`dCentPos`) must be added to get that
    transponder's true final position. `array_center_enu` (`Center_ENU`), as written
    to a *results* file, is already the array's final converged centroid (verified
    empirically: `mean(transponder position_enu) + delta_center_position ==
    array_center_enu` to within floating-point precision on real data) — it does not
    need `delta_center_position` added again.

    `del_e/del_n/del_u` for each transponder are computed as final position (i.e.
    `position_enu + delta_center_position`) minus the a priori `position_llh`
    (converted into the same local ENU frame) — this assumes `position_llh` on the
    results object still holds the a priori seed position rather than being
    overwritten by the solver, matching current GARPOS output-file behavior. Treat
    these two columns as provisional.

    Parameters
    ----------
    results : GarposInput
        A solved `GarposInput`, e.g. from `GarposInput.from_datafile(...)`.
    coord_transformer : CoordTransformer
        The same origin (site `arrayCenter` lat/lon, `-localGeoidHeight`) used to
        build `results`.

    Returns
    -------
    pd.DataFrame
        Columns: `id, x, y, z, sigma_x, sigma_y, sigma_z, latitude, longitude,
        height_msl, del_e, del_n, del_u, sigma_e, sigma_n, sigma_u`. Sigmas are a
        diagonal-only (no cross-covariance) approximation — display-quality, not
        rigorous error propagation.
    """
    array_enu = results.array_center_enu
    array_dpos = results.delta_center_position
    if array_enu is None or array_dpos is None:
        raise ValueError("Array center or delta position not found in GARPOS results.")

    rows = []

    X, Y, Z, lat, lon, height_msl = enu_to_ecef_llh(
        coord_transformer, array_enu.east, array_enu.north, array_enu.up
    )
    sigma_e, sigma_n, sigma_u = array_dpos.get_std_dev()
    sigma_x, sigma_y, sigma_z = enu_sigma_to_ecef_sigma(coord_transformer, sigma_e, sigma_n, sigma_u)
    rows.append(
        {
            "id": "ARRAY",
            "x": X,
            "y": Y,
            "z": Z,
            "sigma_x": sigma_x,
            "sigma_y": sigma_y,
            "sigma_z": sigma_z,
            "latitude": lat,
            "longitude": lon,
            "height_msl": height_msl,
            "del_e": array_dpos.east,
            "del_n": array_dpos.north,
            "del_u": array_dpos.up,
            "sigma_e": sigma_e,
            "sigma_n": sigma_n,
            "sigma_u": sigma_u,
        }
    )

    for transponder in results.transponders:
        if transponder.position_enu is None:
            continue
        east, north, up = transponder.position_enu.get_position()
        east += array_dpos.east
        north += array_dpos.north
        up += array_dpos.up
        X, Y, Z, lat, lon, height_msl = enu_to_ecef_llh(coord_transformer, east, north, up)
        sigma_e, sigma_n, sigma_u = transponder.position_enu.get_std_dev()
        sigma_x, sigma_y, sigma_z = enu_sigma_to_ecef_sigma(
            coord_transformer, sigma_e, sigma_n, sigma_u
        )

        del_e = del_n = del_u = None
        if transponder.position_llh is not None:
            e0, n0, u0 = coord_transformer.LLH2ENU(
                transponder.position_llh.latitude,
                transponder.position_llh.longitude,
                transponder.position_llh.height,
            )
            del_e, del_n, del_u = east - e0, north - n0, up - u0

        rows.append(
            {
                "id": transponder.id,
                "x": X,
                "y": Y,
                "z": Z,
                "sigma_x": sigma_x,
                "sigma_y": sigma_y,
                "sigma_z": sigma_z,
                "latitude": lat,
                "longitude": lon,
                "height_msl": height_msl,
                "del_e": del_e,
                "del_n": del_n,
                "del_u": del_u,
                "sigma_e": sigma_e,
                "sigma_n": sigma_n,
                "sigma_u": sigma_u,
            }
        )

    return pd.DataFrame(rows)


def print_gnatss_format(df: pd.DataFrame, station: str) -> None:
    """Print a GNATSS-format DataFrame (from `garpos_results_to_gnatss_format`) in
    GNATSS's own text layout, e.g.::

        ---- FINAL SOLUTION ----
        NBR1-1
        x = -2723405.2081 +/- 1.802565e-03 m del_e = 0.2915 +/- 1.804684e-03 m
        y = -3873380.0443 +/- 1.801476e-03 m del_n = -0.1379 +/- 1.807375e-03 m
        z = 4256184.6431 +/- 1.801896e-03 m del_u = 0.2027 +/- 1.79385e-03 m
        Lat. = 42.14325336350843 deg, Long. = -125.11136643413191, Hgt.msl = -1804.1619223034095 m

    The `"ARRAY"` row (this repo's addition, with no GNATSS equivalent in the sample
    format) is printed under the label `"{station}"` instead of `"{station}-N"`.

    Parameters
    ----------
    df : pd.DataFrame
        Output of `garpos_results_to_gnatss_format` for a single survey/run (do not
        pass a multi-survey `to_gnatss_format`/`to_gnatss_format_qc` result directly —
        filter to one `survey_id` first).
    station : str
        Station name used to build each block's label (`"{station}-{n}"`).
    """
    print("---- FINAL SOLUTION ----")
    transponder_num = 0
    for _, row in df.iterrows():
        if row["id"] == "ARRAY":
            label = station
        else:
            transponder_num += 1
            label = f"{station}-{transponder_num}"
        print(label)
        print(
            f"x = {row['x']:.4f} +/- {row['sigma_x']:.6e} m "
            f"del_e = {row['del_e']:.4f} +/- {row['sigma_e']:.6e} m"
        )
        print(
            f"y = {row['y']:.4f} +/- {row['sigma_y']:.6e} m "
            f"del_n = {row['del_n']:.4f} +/- {row['sigma_n']:.6e} m"
        )
        print(
            f"z = {row['z']:.4f} +/- {row['sigma_z']:.6e} m "
            f"del_u = {row['del_u']:.4f} +/- {row['sigma_u']:.6e} m"
        )
        print(
            f"Lat. = {row['latitude']} deg, Long. = {row['longitude']}, "
            f"Hgt.msl = {row['height_msl']} m"
        )


def plot_enu_llh_side_by_side(garpos_input: GarposInput):
    """
    Plot the transponder and antenna positions in ENU and LLH coordinates side by side.

    Parameters
    ----------
    garpos_input : GarposInput
        The input data containing observations and site information.
    """

    # Create a figure with two subplots
    fig, axs = plt.subplots(1, 2, figsize=(20, 10))

    # Plot ENU plot on the first subplot
    ax_enu = axs[0]
    # Plot lines between antenna positions
    ax_enu.scatter(
        garpos_input.observation.shot_data["ant_e0"],
        garpos_input.observation.shot_data["ant_n0"],
        color="green",
        marker="o",
        linewidths=0.25,
    )

    # Plot transponder positions
    for transponder in garpos_input.site.transponders:
        ax_enu.scatter(
            transponder.position_enu.east,
            transponder.position_enu.north,
            label=transponder.id,
            marker="x",
            color="red",
            linewidths=5,
        )

    # Plot site center enu
    ax_enu.scatter(
        garpos_input.site.center_enu.east,
        garpos_input.site.center_enu.north,
        label="Center",
        marker="x",
        color="blue",
        linewidths=5,
    )
    ax_enu.set_xlabel("East (m)")
    ax_enu.set_ylabel("North (m)")
    ax_enu.set_title("Transponder and Antenna Positions (ENU)")
    ax_enu.grid(True)

    # Plot LLH plot on the second subplot
    ax_llh = axs[1]
    # Plot lines between antenna positions
    ax_llh.scatter(
        garpos_input.observation.shot_data["longitude"],
        garpos_input.observation.shot_data["latitude"],
        color="green",
        marker="o",
        linewidths=0.25,
    )
    # Plot transponder positions
    for transponder in garpos_input.site.transponders:
        ax_llh.scatter(
            transponder.position_llh.longitude,
            transponder.position_llh.latitude,
            label=transponder.id,
            marker="x",
            color="red",
            linewidths=5,
        )
    # Plot site center llh
    ax_llh.scatter(
        garpos_input.site.center_llh.longitude,
        garpos_input.site.center_llh.latitude,
        label="Center",
        marker="x",
        color="blue",
        linewidths=5,
    )
    ax_llh.set_xlabel("Longitude")
    ax_llh.set_ylabel("Latitude")
    ax_llh.set_title("Transponder and Antenna Positions (LLH)")
    ax_llh.grid(True)
    plt.legend()
    plt.tight_layout()
    plt.show()


def process_garpos_results(results: GarposInput) -> tuple[GarposInput, pd.DataFrame]:
    """
    Process garpos results to compute delta x, y, z and relevant fields.
    This function processes the garpos results to calculate the delta x, y, z
    for each transponder and other relevant fields. It also converts the
    residual travel time (ResiTT) to meters using the harmonic mean of the
    sound speed data.

    Parameters
    ----------
    results : GarposInput
        The input data containing observations and site information.

    Returns
    -------
    Tuple[GarposResults, pd.DataFrame]
        A tuple containing the processed garpos results
        and a DataFrame with the shot data including the calculated residual ranges.
    """

    # Process garpos results to get delta x,y,z and relevant fields
    logger.info("Processing GARPOS results")

    # Get the harmonic mean of the svp data, and use that to convert ResiTT to meters
    svp_df = pd.read_csv(results.sound_speed_data)
    results_df = pd.read_csv(results.shot_data, skiprows=1)
    speed_mean = harmonic_mean(svp_df.speed.values)
    range_residuals = results_df.ResiTT.values * speed_mean / 2

    results_df["ResiRange"] = range_residuals
    results_df = GarposObservationOutput.validate(results_df, lazy=True)

    # For each transponder, get the delta x,y,and z respectively
    for transponder in results.transponders:
        id = transponder.id
        takeoff = np.deg2rad(results_df[id == results_df.MT].TakeOff.values)
        azimuth = np.deg2rad(results_df[id == results_df.MT].head1.values)
        delta_x = np.mean(np.cos(takeoff) * np.cos(azimuth))
        delta_y = np.mean(np.cos(takeoff) * np.sin(azimuth))
        delta_z = np.mean(np.sin(azimuth))

        transponder.delta_center_position = GPPositionENU(east=delta_x, north=delta_y, up=delta_z)
    # save updated df
    results_df.to_csv(results.shot_data)

    logger.info("GARPOS results processed, returning results tuple")
    return results, results_df


def drop_implausible_antenna_heights(shot_data_path: Path, filtered_path: Path) -> tuple[Path, int]:
    """Drop shots whose antenna height is a gross outlier for this survey.

    A stretch of degraded GNSS tracking (too few satellites for a
    well-determined fix) can leave a subset of shots with ``ant_u0``/``ant_u1``
    off by hundreds to thousands of metres, while genuine antenna-height
    variation (tide, heave) within a survey is a few metres at most. GARPOS's
    own ray tracer rejects such shots with an unrecoverable ``sys.exit()``
    deep inside a multiprocessing worker — ``SystemExit`` escapes ``Pool``'s
    ``except Exception`` handling, so the worker dies without ever reporting
    back and the pool hangs forever instead of raising. Filter these out
    before they reach GARPOS, using a robust (median / MAD) threshold per
    antenna-height column so the check self-calibrates to each survey's own
    baseline rather than assuming a fixed "normal" height.

    Parameters
    ----------
    shot_data_path : Path
        Path to the GARPOS-format shot data CSV (as referenced by
        ``GarposInput.shot_data``).
    filtered_path : Path
        Where to write the filtered CSV. Only used if any rows are dropped.

    Returns
    -------
    tuple[Path, int]
        The path to use going forward (``shot_data_path`` unchanged if
        nothing was dropped, else ``filtered_path``), and the number of
        rows dropped.
    """
    # GARPOS's own regenerated "*-obs.csv" (used as the shot-data source from the
    # second iteration onward of a multi-iteration run) prepends a "# cfgfile = ..."
    # comment line before the real header; comment="#" skips it so the header is
    # parsed correctly regardless of which shot-data variant is passed in.
    df = pd.read_csv(shot_data_path, index_col=0, comment="#")
    bad = pd.Series(False, index=df.index)
    for col in ("ant_u0", "ant_u1"):
        if col not in df.columns:
            continue
        median = df[col].median()
        mad = (df[col] - median).abs().median()
        if mad == 0:
            continue
        bad |= (df[col] - median).abs() > 8 * mad

    n_bad = int(bad.sum())
    if n_bad == 0:
        return shot_data_path, 0

    logger.warning(
        f"Dropping {n_bad} of {len(df)} shots with implausible antenna height "
        f"(likely a period of degraded GNSS tracking) before running GARPOS: {shot_data_path}"
    )
    df[~bad].to_csv(filtered_path)
    return filtered_path, n_bad


def rectify_shotdata(coord_transformer: CoordTransformer, shot_data: pd.DataFrame) -> pd.DataFrame:
    """
    Rectifies the shot data to the site local coordinate system by transforming coordinates and renaming columns.
    This method performs the following operations on the input shot data:
    1. Transforms the ECEF coordinates to ENU coordinates for two sets of points.
    2. Adds the transformed coordinates to the DataFrame.
    3. Sets default values for the "SET" and "LN" columns.
    4. Renames specific columns according to a predefined mapping.
    5. Selects and reorders the columns in the DataFrame.
    6. Validates and sorts the DataFrame by "triggerTime".

    Parameters
    ----------
    shot_data : pd.DataFrame
        The input DataFrame containing shot data with columns
        "east0", "north0", "up0", "east1", "north1", "up1",
        "trigger_time", "hae0", "pingTime", "returnTime",
        "tt", "transponderID", "head0", "pitch0", "roll0",
        "head1", "pitch1", and "roll1".

    Returns
    -------
    pd.DataFrame
        The rectified and validated DataFrame sorted by "triggerTime".
    """

    e0, n0, u0 = coord_transformer.ECEF2ENU_vec(
        shot_data.east0.to_numpy(),
        shot_data.north0.to_numpy(),
        shot_data.up0.to_numpy(),
    )
    e1, n1, u1 = coord_transformer.ECEF2ENU_vec(
        shot_data.east1.to_numpy(),
        shot_data.north1.to_numpy(),
        shot_data.up1.to_numpy(),
    )
    shot_data["ant_e0"] = e0
    shot_data["ant_n0"] = n0
    shot_data["ant_u0"] = u0
    shot_data["ant_e1"] = e1
    shot_data["ant_n1"] = n1
    shot_data["ant_u1"] = u1
    shot_data["SET"] = "S01"
    shot_data["LN"] = "L01"
    rename_dict = {
        "pingTime": "ST",
        "hae0": "height",
        "returnTime": "RT",
        "tt": "TT",
        "transponderID": "MT",
    }
    shot_data = shot_data.rename(columns=rename_dict).loc[
        :,
        [
            "MT",
            "ST",
            "RT",
            "TT",
            "ant_e0",
            "ant_n0",
            "ant_u0",
            "head0",
            "pitch0",
            "roll0",
            "ant_e1",
            "ant_n1",
            "ant_u1",
            "head1",
            "pitch1",
            "roll1",
            "isUpdated",
        ],
    ]

    return ObservationData.validate(shot_data, lazy=True).sort_values("ST")
