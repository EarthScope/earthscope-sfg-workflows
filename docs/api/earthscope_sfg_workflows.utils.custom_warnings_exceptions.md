# custom_warnings_exceptions

`earthscope_sfg_workflows.utils.custom_warnings_exceptions`

Project-specific `Warning` and `Exception` classes for CLI integrations.

## class `DYLDLibraryException`

Exception raised when the DYLD_LIBRARY_PATH environment variable is not set.

## class `LDLibraryException`

Exception raised when the LD_LIBRARY_PATH environment variable is not set.

## class `MetadataRequiredError`

Raised when a service method is called without required session metadata.

## class `PrideSampleFrequencyWarning`

Warning for when the PRIDE-PPP sample frequency should be reduced.
