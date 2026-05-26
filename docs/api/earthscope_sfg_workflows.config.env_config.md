# env_config

`earthscope_sfg_workflows.config.env_config`

This module manages environment-specific configurations.

It defines a class `Environment` that detects the current working environment
(e.g., LOCAL or GEOLAB) and loads relevant settings from environment variables.

## class `Environment`

A class to manage and provide access to environment-specific settings.

This class should not be instantiated. It provides its functionality through
class methods.

## class `WorkingEnvironment`

Enumeration for the possible working environments.
