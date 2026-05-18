"""Internal utilities (CLI helpers, custom warnings, model_update)."""

from .command_line_utils import (
    get_binary_path,
    get_system_architecture,
    parse_cli_logs,
    run_binary,
)
from .custom_warnings_exceptions import (
    DYLDLibraryException,
    LDLibraryException,
    MetadataRequiredError,
    PrideSampleFrequencyWarning,
)
from .model_update import deep_merge_dicts, validate_and_merge_config, validate_keys_recursively

__all__ = [
    # command line helpers
    "get_binary_path",
    "get_system_architecture",
    "parse_cli_logs",
    "run_binary",
    # exceptions and warnings
    "DYLDLibraryException",
    "LDLibraryException",
    "MetadataRequiredError",
    "PrideSampleFrequencyWarning",
    # config helpers
    "deep_merge_dicts",
    "validate_and_merge_config",
    "validate_keys_recursively",
]
