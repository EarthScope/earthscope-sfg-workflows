"""NovAtel binary → TileDB ingestion helpers.

Ported from ``earthscope_sfg.novatel_tools.novatel_binary_operations`` to
keep the workflow orchestration layer self-contained.  The two wrappers
preserve legacy CLI flags (``-tdbpos``, ``-procs``) that the modern
``earthscope_sfg_tools.tiledb_integration.novatel_tools.go_binaries``
wrappers do not yet expose.
"""

import os
import subprocess
from pathlib import Path

from earthscope_sfg_workflows.binary_ops.binary_paths import (
    get_nov_000_tile_binary_path,
    get_nov_770_tile_binary_path,
)
from earthscope_sfg_workflows.logging import ProcessLogger as logger
from earthscope_sfg_workflows.utils.command_line_utils import parse_cli_logs

os.environ["DYLD_LIBRARY_PATH"] = os.environ.get("CONDA_PREFIX", "") + "/lib"


def novatel_770_2tile(files: list[str], gnss_obs_tdb: Path, n_procs: int = 10) -> None:
    """Process NOV770 binary files into a single TileDB GNSS observation array.

    Parameters
    ----------
    files : list[str]
        Paths to NOV770 binary inputs.
    gnss_obs_tdb : Path
        Destination TileDB array path.
    n_procs : int, optional
        Worker process count.  Defaults to 10.
    """

    binary_path = get_nov_770_tile_binary_path()
    cmd = [str(binary_path), "-tdb", str(gnss_obs_tdb), "-procs", str(n_procs)]
    logger.logdebug(f" Running {cmd}")
    for file in files:
        cmd.append(str(file))
    logger.loginfo(f"Running NOVB2TILE with {' '.join(cmd)}")

    result = subprocess.run(cmd, capture_output=True)
    parse_cli_logs(result, logger)


def novatel_000_2tile(
    files: list[str], gnss_obs_tdb: Path, position_tdb: Path, n_procs: int = 10
) -> None:
    """Process NOV000 binary files into TileDB GNSS observation + IMU position arrays.

    Parameters
    ----------
    files : list[str]
        Paths to NOV000 binary inputs.
    gnss_obs_tdb : Path
        Destination GNSS observation TileDB array path.
    position_tdb : Path
        Destination IMU position TileDB array path.
    n_procs : int, optional
        Worker process count.  Defaults to 10.
    """

    binary_path = get_nov_000_tile_binary_path()
    cmd = [
        str(binary_path),
        "-tdb",
        str(gnss_obs_tdb),
        "-tdbpos",
        str(position_tdb),
        "-procs",
        str(n_procs),
    ]
    logger.logdebug(f" Running {cmd}")
    for file in files:
        cmd.append(str(file))
    logger.loginfo(f"Running NOV0002TILE with {' '.join(cmd)}")

    result = subprocess.run(cmd)
    parse_cli_logs(result, logger)
