# model_update

`earthscope_sfg_workflows.utils.model_update`

Pydantic model utilities: typo-tolerant validation and deep merging.

## `deep_merge_dicts(base: dict[str, typing.Any], override: dict[str, typing.Any]) -> dict[str, typing.Any]`

Recursively merge override dict into base dict.
For nested dictionaries, values are merged recursively rather than replaced.
For other types, override values replace base values.

Parameters
----------
base
    The base dictionary to merge into.
override
    The override dictionary with values to merge.

Returns
-------
dict
    A new dictionary with merged values.

## `validate_and_merge_config(base_class: pydantic.main.BaseModel, override_config: pydantic.main.BaseModel | dict[str, typing.Any]) -> pydantic.main.BaseModel`

Validates and merges override configuration with base config, checking for typos.
Performs a deep merge so that nested configuration objects (like pride_config)
are merged field-by-field rather than completely replaced.

Parameters
----------
base_class
    The base configuration class instance (Pydantic model).
override_config
    The override configuration dictionary to update the base config.

Returns
-------
BaseModel
    A new instance of the base_class with merged configuration.

Raises
------
ValueError
    If there are typos or invalid keys in the override_config.

## `validate_keys_recursively(config_dict: dict, model_class: pydantic.main.BaseModel, path: str = '')`

Recursively validate keys and suggest corrections for typos.

Parameters
----------
config_dict
    The dictionary to validate.
model_class
    The Pydantic model to validate against.
path
    The current path in the nested dictionary, for error reporting.

Returns
-------
list
    A list of error messages.
