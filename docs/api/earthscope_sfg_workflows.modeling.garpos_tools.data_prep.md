# data_prep

`earthscope_sfg_workflows.modeling.garpos_tools.data_prep`

This module contains the GarposDataPreparer class, which is responsible for preparing GARPOS input data.

## `GP_Transponders_from_benchmarks(coord_transformer: earthscope_sfg_workflows.modeling.garpos_tools.functions.CoordTransformer, survey: earthscope_sfg_tools.datamodels.metadata.earthscope.campaign.Survey, site: earthscope_sfg_tools.datamodels.metadata.earthscope.site.Site, is_qc: bool = False) -> list[earthscope_sfg_workflows.modeling.garpos_tools.schemas.GPTransponder]`

Get GP transponders from the benchmarks in the survey.
Parameters
----------
coord_transformer :
    The coordinate transformer.
survey :
    The survey object.
site :
    The site metadata.
is_qc :
    Flag indicating if the data is for quality control, by default False

Returns
-------
list[GPTransponder]
    List of GPTransponder objects for the survey.

Raises
------
NoGPTranspondersError
    If no transponders are found for the survey.

## class `NoGPTranspondersError`

Custom exception raised when no GP transponders are found for a given survey.
This exception is used to indicate that the GP transponders for a specific survey are empty or not available.

## class `NoShotDataError`

Custom exception raised when no shot data is found for a given survey.
This exception is used to indicate that the shot data for a specific survey is empty or not available.

## `apply_survey_config(config: earthscope_sfg_workflows.config.garpos_config.GarposSiteConfig, garpos_input: earthscope_sfg_workflows.modeling.garpos_tools.schemas.GarposInput) -> earthscope_sfg_workflows.modeling.garpos_tools.schemas.GarposInput`

Apply the site configuration to the GarposInput object.
Parameters
----------
config :
    The site configuration.
garpos_input :
    The GarposInput object to be modified.

Returns
-------
GarposInput
    The modified GarposInput object with the site configuration applied.

## `avg_transponder_position(transponders: list[earthscope_sfg_workflows.modeling.garpos_tools.schemas.GPTransponder]) -> tuple[earthscope_sfg_workflows.modeling.garpos_tools.schemas.GPPositionENU, earthscope_sfg_workflows.modeling.garpos_tools.schemas.GPPositionLLH]`

Calculate the average position of the transponders.

Parameters
----------
transponders :
    List of transponders.

Returns
-------
tuple
    Average position in ENU and LLH.

## `create_GPTransponder(coord_transformer: earthscope_sfg_workflows.modeling.garpos_tools.functions.CoordTransformer, benchmark: earthscope_sfg_tools.datamodels.metadata.earthscope.benchmark.Benchmark, transponder: earthscope_sfg_tools.datamodels.metadata.earthscope.benchmark.Transponder) -> earthscope_sfg_workflows.modeling.garpos_tools.schemas.GPTransponder`

Create a GPTransponder object from a benchmark and transponder.
Parameters
----------
coord_transformer :
    The coordinate transformer.
benchmark :
    The benchmark object.
transponder :
    The transponder object.

Returns
-------
GPTransponder
    The created GPTransponder object.

## `get_array_dpos_center(coord_transformer: earthscope_sfg_workflows.modeling.garpos_tools.functions.CoordTransformer, transponders: list[earthscope_sfg_workflows.modeling.garpos_tools.schemas.GPTransponder])`

Get the average transponder position in ENU coordinates.
Parameters
----------
coord_transformer :
    The coordinate transformer.
transponders :
    List of GPTransponder objects.

Returns
-------
tuple
    Average transponder position in ENU and LLH coordinates.

## `prepare_garpos_input_from_survey(shot_data_path: pathlib.Path, survey: earthscope_sfg_tools.datamodels.metadata.earthscope.campaign.Survey, site: earthscope_sfg_tools.datamodels.metadata.earthscope.site.Site, campaign: earthscope_sfg_tools.datamodels.metadata.earthscope.campaign.Campaign, ss_path: str, array_dpos_center: tuple[float, float, float], num_of_shots: int, GPtransponders: list[earthscope_sfg_workflows.modeling.garpos_tools.schemas.GPTransponder]) -> earthscope_sfg_workflows.modeling.garpos_tools.schemas.GarposInput`

Prepare the GarposInput object from the survey and shot data.
Parameters
----------
shot_data_path :
    The path to the shot data CSV file.
survey :
    The survey object.
site :
    The site metadata.
campaign :
    The campaign metadata.
ss_path :
    The relative path to the sound speed profile file.
array_dpos_center :
    The average position of the transponders in ENU coordinates.
num_of_shots :
    The number of shots in the shot data.
GPtransponders :
    List of GPTransponder objects for the survey.

Returns
-------
GarposInput
    The prepared GarposInput object.

## `prepare_shotdata_for_garpos(coord_transformer: earthscope_sfg_workflows.modeling.garpos_tools.functions.CoordTransformer, shodata_out_path: pathlib.Path, shot_data: pandas.core.frame.DataFrame, GPtransponders: list[earthscope_sfg_workflows.modeling.garpos_tools.schemas.GPTransponder])`

Prepare the shot data for GARPOS.
This is done by rectifying it and saving it to a CSV file.

Parameters
----------
coord_transformer :
    The coordinate transformer.
shodata_out_path :
    The path to save the shot data CSV file.
shot_data :
    The shot data DataFrame to be prepared.
GPtransponders :
    List of GPTransponder objects for the survey.

Returns
-------
pd.DataFrame
    The rectified shot data DataFrame.

Raises
------
ValueError
    If the shot data fails validation.
