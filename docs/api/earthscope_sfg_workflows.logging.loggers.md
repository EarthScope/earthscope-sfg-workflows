# loggers

`earthscope_sfg_workflows.logging.loggers`

This module contains functions to set up loggers for the package.

The base logger is set up first, writes to a file, and is used to create other loggers.
The general logger is used for most of the package and prints to the console.
The pride logger is used for the pride module and prints to the console and a file.
The rinex logger is used for the rinex module and prints to the console and a file.
The notebook logger is used for the notebook module and prints to the console with a minimal format.

## `change_all_logger_dirs(dir: pathlib.Path)`

Change the directory for all loggers.

Parameters
----------
dir
    The directory to set.

## `remove_all_loggers_from_console()`

Remove all loggers from the console.

## `route_all_loggers_to_console()`

Route all loggers to the console.

## `set_all_logger_levels(level: Literal[10, 20, 30, 40, 50])`

Set the level for all loggers.

Parameters
----------
level
    The logging level to set.
