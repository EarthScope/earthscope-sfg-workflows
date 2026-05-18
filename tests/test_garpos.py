"""GARPOS installation/integration tests.

These tests verify that a working GARPOS install is reachable via the
``GARPOS_PATH`` environment variable. The whole module is skipped when
``GARPOS_PATH`` is unset (typical for CI without the GARPOS submodule).
"""

from __future__ import annotations

import ctypes
import os
import subprocess
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(
    not os.getenv("GARPOS_PATH") or os.getenv("GARPOS_PATH") == "None",
    reason="GARPOS_PATH not set; skipping GARPOS integration tests",
)

# Imports below trigger filesystem lookups against ``GARPOS_PATH`` at import
# time, so they must come after the skip marker is registered.
from earthscope_sfg_workflows.modeling.garpos_tools.load_utils import (  # noqa: E402
    get_drive_garpos,
    get_lib_paths,
)


@pytest.fixture(scope="module")
def garpos_lib() -> tuple[str, str]:
    """Resolve the ``(f90lib_dir, lib_raytrace.so)`` pair once per module."""
    return get_lib_paths()


@pytest.fixture(scope="module")
def drive_garpos():
    """Resolve the ``drive_garpos`` callable once per module."""
    return get_drive_garpos()


class TestGarposInstallation:
    """Verify GARPOS is properly installed and functional."""

    def test_garpos_import(self) -> None:
        """The ``garpos`` package can be imported."""
        import garpos

        assert garpos is not None, "garpos module failed to import"

    def test_garpos_lib_directory_exists(self, garpos_lib: tuple[str, str]) -> None:
        """The GARPOS f90lib directory exists on disk."""
        lib_directory, _ = garpos_lib
        assert Path(lib_directory).exists(), f"LIB_DIRECTORY not found: {lib_directory}"

    def test_garpos_lib_raytrace_exists(self, garpos_lib: tuple[str, str]) -> None:
        """The ``lib_raytrace.so`` shared object exists inside the f90lib dir."""
        lib_directory, lib_raytrace = garpos_lib
        assert (Path(lib_directory) / Path(lib_raytrace).name).exists(), (
            f"LIB_RAYTRACE not found: {lib_raytrace}"
        )

    def test_garpos_raytrace_is_loadable(self, garpos_lib: tuple[str, str]) -> None:
        """The raytrace shared library can be ``dlopen``'d via ctypes."""
        lib_directory, lib_raytrace = garpos_lib
        try:
            lib = ctypes.CDLL(str(Path(lib_directory) / Path(lib_raytrace).name))
        except OSError as exc:
            pytest.fail(f"Failed to load raytrace library: {exc}")
        assert lib is not None, "Failed to load raytrace library"

    def test_drive_garpos(self, drive_garpos) -> None:
        """``drive_garpos`` is callable; calling with no args raises ``TypeError``."""
        with pytest.raises(TypeError):
            drive_garpos()

    def test_garpos_run(self, capsys: pytest.CaptureFixture[str]) -> None:
        """The GARPOS ``demo.sh`` sample runs to completion under bash."""
        garpos_dir = Path(os.environ["GARPOS_PATH"]).resolve()
        demo_sh = garpos_dir / "sample" / "demo.sh"
        if not demo_sh.exists():
            pytest.skip(f"demo.sh not found in GARPOS_PATH: {demo_sh}")

        try:
            result = subprocess.run(
                ["bash", str(demo_sh)],
                cwd=demo_sh.parent,
                check=True,
                capture_output=True,
                text=True,
            )
        except subprocess.CalledProcessError as exc:
            pytest.fail(f"Failed to run demo.sh: {exc}\nstderr:\n{exc.stderr}")

        with capsys.disabled():
            print("\n=== GARPOS demo.sh stdout ===")
            print(result.stdout)
            if result.stderr:
                print("=== GARPOS demo.sh stderr ===")
                print(result.stderr)
