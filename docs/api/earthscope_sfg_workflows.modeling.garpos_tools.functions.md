# functions

`earthscope_sfg_workflows.modeling.garpos_tools.functions`

Low-level math and signal-processing helpers for GARPOS inversion.

## class `CoordTransformer`

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

**Methods**

### `CoordTransformer.ECEF2ENU_vec(self, X: numpy.ndarray, Y: numpy.ndarray, Z: numpy.ndarray) -> tuple[numpy.ndarray, numpy.ndarray, numpy.ndarray]`

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

### `CoordTransformer.LLH2ENU(self, lat: float, lon: float, hgt: float) -> tuple[float, float, float]`

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

### `CoordTransformer.LLH2ENU_vec(self, lat: numpy.ndarray, lon: numpy.ndarray, hgt: numpy.ndarray) -> tuple[numpy.ndarray, numpy.ndarray, numpy.ndarray]`

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

### `CoordTransformer.XYZ2ENU(self, X: float, Y: float, Z: float) -> tuple[float, float, float]`

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


## `avg_transponder_position(transponders: list[earthscope_sfg_workflows.modeling.garpos_tools.schemas.GPTransponder]) -> tuple[earthscope_sfg_workflows.modeling.garpos_tools.schemas.GPPositionENU, earthscope_sfg_workflows.modeling.garpos_tools.schemas.GPPositionLLH]`

Calculate the average position of the transponders.

Parameters
----------
transponders : List[Transponder]
    A list of transponders.

Returns
-------
Tuple[PositionENU, PositionLLH]
    A tuple containing the average position in ENU and LLH coordinates.

## `plot_enu_llh_side_by_side(garpos_input: earthscope_sfg_workflows.modeling.garpos_tools.schemas.GarposInput)`

Plot the transponder and antenna positions in ENU and LLH coordinates side by side.

Parameters
----------
garpos_input : GarposInput
    The input data containing observations and site information.

## `process_garpos_results(results: earthscope_sfg_workflows.modeling.garpos_tools.schemas.GarposInput) -> tuple[earthscope_sfg_workflows.modeling.garpos_tools.schemas.GarposInput, pandas.core.frame.DataFrame]`

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

## `rectify_shotdata(coord_transformer: earthscope_sfg_workflows.modeling.garpos_tools.functions.CoordTransformer, shot_data: pandas.core.frame.DataFrame) -> pandas.core.frame.DataFrame`

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

## `xyz2enu(x, y, z, lat0, lon0, hgt0, inv=1, **kwargs)`

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
