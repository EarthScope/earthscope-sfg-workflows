# command_line_utils

`earthscope_sfg_workflows.utils.command_line_utils`

Helpers for invoking external CLI binaries (Go-built and PRIDE PPP).

Provides platform-aware binary path resolution, log parsing for stdout/stderr,
and a thin `subprocess.run` wrapper that maps known error strings to typed
exceptions defined in `custom_warnings_exceptions`.

## `get_binary_path(path_map: dict[str, pathlib.Path], binary_name: str) -> pathlib.Path`

Resolve a platform-specific binary path from a ``{system_arch: path}`` map.

Parameters
----------
path_map
    Mapping of ``"system_arch"`` keys (e.g. ``"darwin_arm64"``) to binary paths.
binary_name
    Human-readable name used in error messages when the binary is missing.

Returns
-------
Path
    Resolved binary path for the current platform.

Raises
------
FileNotFoundError
    If no binary is available for the current platform.

## `get_system_architecture() -> tuple[str, str]`

Get the current system and architecture.

Returns
-------
tuple[str, str]
    A tuple containing the system and architecture.

## `parse_cli_logs(result, logger: earthscope_sfg_workflows.logging.loggers._BaseLogger | logging.Logger)`

Parse stdout/stderr from a `CompletedProcess`, logging and raising known errors.

## `parse_error(string: str) -> Warning | None`

Map a known error/warning substring to its `Warning` class, or None.

## `raise_exception(string: str) -> Exception | None`

Map a known platform-specific error string to its `Exception`, or None.

## `remove_ansi_escape(text)`

Strip ANSI escape sequences from `text`.

## `run_binary(cmd: list[str], log: '_BaseLogger | logging.Logger | None' = None, cwd: 'str | Path | None' = None, capture: bool = True) -> subprocess.CompletedProcess`

Run an external binary, parse its CLI logs, and return the result.

Parameters
----------
cmd
    Command and arguments to execute.
log
    Logger instance for output parsing.  Falls back to module-level logger.
cwd
    Working directory for the subprocess.
capture
    Whether to capture stdout/stderr.  Defaults to True.

Returns
-------
subprocess.CompletedProcess
