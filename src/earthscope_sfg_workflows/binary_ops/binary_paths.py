"""Platform-specific Go binary path resolution.

Ported from ``earthscope_sfg.novatel_tools.utils`` to keep the workflow
orchestration layer self-contained.
"""

from pathlib import Path

from earthscope_sfg_workflows.utils.command_line_utils import (
    GOLANG_BINARY_BUILD_DIR,
    get_binary_path,
)

NOVB2TILE_BIN_PATH = {
    "darwin_amd64": GOLANG_BINARY_BUILD_DIR / "novab2tile_darwin_amd64",
    "darwin_arm64": GOLANG_BINARY_BUILD_DIR / "novab2tile_darwin_arm64",
    "linux_amd64": GOLANG_BINARY_BUILD_DIR / "novab2tile_linux_amd64",
    "linux_arm64": GOLANG_BINARY_BUILD_DIR / "novab2tile_linux_arm64",
}

NOV0002TILE_BIN_PATH = {
    "darwin_amd64": GOLANG_BINARY_BUILD_DIR / "nov0002tile_darwin_amd64",
    "darwin_arm64": GOLANG_BINARY_BUILD_DIR / "nov0002tile_darwin_arm64",
    "linux_amd64": GOLANG_BINARY_BUILD_DIR / "nov0002tile_linux_amd64",
    "linux_arm64": GOLANG_BINARY_BUILD_DIR / "nov0002tile_linux_arm64",
}


def get_nov_770_tile_binary_path() -> Path:
    """Get the path to the novb2tile golang binary for the current platform."""
    return get_binary_path(NOVB2TILE_BIN_PATH, "NOVB2TILE")


def get_nov_000_tile_binary_path() -> Path:
    """Get the path to the nov0002tile golang binary for the current platform."""
    return get_binary_path(NOV0002TILE_BIN_PATH, "NOV0002TILE")
