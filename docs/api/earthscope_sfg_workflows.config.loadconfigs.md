# loadconfigs

`earthscope_sfg_workflows.config.loadconfigs`

This module provides functions for loading survey-specific configurations.

It determines which filter settings and GARPOS parameters to use based on the
type of survey being processed (e.g., CENTER, CIRCLE).

## `get_garpos_site_config(survey_type: earthscope_sfg_tools.datamodels.metadata.earthscope.campaign.SurveyType | str) -> earthscope_sfg_workflows.config.garpos_config.GarposSiteConfig`

Get the GARPOS site configuration based on the survey type.

Parameters
----------
survey_type
    The type of the survey.

Returns
-------
GarposSiteConfig
    The GARPOS site configuration for the survey type.

## `get_survey_filter_config(survey_type: earthscope_sfg_tools.datamodels.metadata.earthscope.campaign.SurveyType | str) -> earthscope_sfg_workflows.prefiltering.schemas.FilterConfig`

Get the filter configuration based on the survey type.

Parameters
----------
survey_type
    The type of the survey.

Returns
-------
FilterConfig
    The filter configuration for the survey type.
