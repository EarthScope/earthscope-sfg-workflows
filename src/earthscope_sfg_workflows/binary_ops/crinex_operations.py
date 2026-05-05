"""Hatanaka CRINEX compression via the bundled ``sfg`` Go binary.

Replaces the legacy ``hatanaka`` Python library + reference ``RNX2CRX``
subprocess with a single call to ``sfg crinex compress``, which performs
RINEX → CRINEX (Hatanaka) compression and gzip in one step.
"""

from __future__ import annotations

import logging
import subprocess
from pathlib import Path

from earthscope_sfg_tools.utils.go_utils import find_binary, parse_cli_logs

logger = logging.getLogger(__name__)


def crinex_compress(
    input_path: str | Path,
    output_path: str | Path,
    gzip: bool = True,
) -> subprocess.CompletedProcess:
    """Compress a RINEX observation file to CRINEX (Hatanaka), optionally gzipped.

    Parameters
    ----------
    input_path : str or Path
        Path to the RINEX observation input.  ``.gz`` inputs are auto-detected.
    output_path : str or Path
        Destination path.  When ``gzip=True``, this should end in ``.gz``.
    gzip : bool, optional
        Gzip the CRINEX output.  Defaults to True.

    Returns
    -------
    subprocess.CompletedProcess
    """
    binary = find_binary()
    cmd = [
        str(binary),
        "crinex",
        "compress",
        "--input",
        str(input_path),
        "--output",
        str(output_path),
    ]
    if gzip:
        cmd.append("-z")

    logger.info(f"Running sfg crinex compress: {' '.join(cmd)}")
    result = subprocess.run(cmd, capture_output=True, text=True, check=False)
    parse_cli_logs(result, logger)
    if result.returncode != 0:
        raise RuntimeError(
            f"sfg crinex compress failed (exit {result.returncode}): {result.stderr}"
        )
    return result


def crinex_decompress(
    input_path: str | Path,
    output_path: str | Path,
) -> subprocess.CompletedProcess:
    """Decompress a CRINEX (Hatanaka) file back to RINEX observation text.

    Parameters
    ----------
    input_path : str or Path
        Path to the CRINEX input.  ``.gz`` inputs are auto-detected.
    output_path : str or Path
        Destination path.

    Returns
    -------
    subprocess.CompletedProcess
    """
    binary = find_binary()
    cmd = [
        str(binary),
        "crinex",
        "decompress",
        "--input",
        str(input_path),
        "--output",
        str(output_path),
    ]

    logger.info(f"Running sfg crinex decompress: {' '.join(cmd)}")
    result = subprocess.run(cmd, capture_output=True, text=True, check=False)
    parse_cli_logs(result, logger)
    if result.returncode != 0:
        raise RuntimeError(
            f"sfg crinex decompress failed (exit {result.returncode}): {result.stderr}"
        )
    return result
