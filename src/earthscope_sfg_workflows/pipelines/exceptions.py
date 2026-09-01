"""Custom exceptions raised by the SFG processing pipelines."""


class NoNovatelFound(Exception):
    """Custom exception raised when no Novatel files are found for processing."""


class NoRinexBuilt(Exception):
    """Custom exception raised when no RINEX files are built for processing."""


class NoRinexFound(Exception):
    """Custom exception raised when no RINEX files are found for processing."""


class NoKinFound(Exception):
    """Custom exception raised when no KIN files are found for processing."""


class NoDFOP00Found(Exception):
    """Custom exception raised when no DFOP00 files are found for processing."""


class NoSVPFound(Exception):
    """Custom exception raised when no SVP files are found for processing."""


class NoLocalData(Exception):
    """Custom exception raised when no data is ingested for processing."""


class NoQCPinFound(Exception):
    """Custom exception raised when no QC PIN files are found for processing."""


class NoNovatelPinFound(Exception):
    """Custom exception raised when no NOVATEL PIN files are found for processing."""
