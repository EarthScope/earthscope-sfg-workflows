"""GARPOS Fortran library bootstrap helpers.

Resolves `GARPOS_PATH` and locates `f90lib/lib_raytrace.so`, which the GARPOS
ray-tracing layer needs at runtime.

Public entrypoints:
    - :func:`get_drive_garpos` — resolve the ``drive_garpos`` callable from
      whichever GARPOS install is available (pip package or ``GARPOS_PATH``).
    - :func:`get_lib_paths` — resolve the Fortran ``(f90lib_dir, lib_raytrace.so)``
      paths for a ``GARPOS_PATH`` source checkout.

Callers should not import ``garpos.drive_garpos`` directly — that hides
whichever install path the user actually has.
"""

import importlib.util
import os
import sys
from collections.abc import Callable
from functools import cache
from pathlib import Path

from earthscope_sfg_workflows.logging import GarposLogger as logger


class GarposNotInstalledError(RuntimeError):
    """Raised when neither a pip-installed nor a `GARPOS_PATH` GARPOS is reachable."""


def _garpos_path_root() -> Path | None:
    """Return ``Path(GARPOS_PATH)`` if set and exists, else ``None``.

    Raises
    ------
    FileNotFoundError
        If ``GARPOS_PATH`` is set but does not exist.
    """
    raw = os.getenv("GARPOS_PATH", None)
    if raw is None or raw == "None":
        return None
    root = Path(raw)
    if not root.exists():
        raise FileNotFoundError(f"GARPOS_PATH {root} does not exist")
    return root


def _candidate_subroots(root: Path) -> list[Path]:
    """Return likely GARPOS package roots: the path itself plus immediate subdirs.

    Bounded to depth=1 to avoid traversing arbitrary user trees. Upstream
    layouts place the package under ``<root>`` directly or under a versioned
    subdir like ``<root>/garpos_v102/``.
    """
    candidates = [root]
    candidates.extend(sorted(p for p in root.iterdir() if p.is_dir()))
    return candidates


def _resolve_lib_paths() -> tuple[str, str] | None:
    """Resolve the GARPOS Fortran library paths from ``GARPOS_PATH``.

    Returns
    -------
    tuple[str, str] or None
        ``(f90lib_dir, lib_raytrace_so)`` as strings, or ``None`` if
        ``GARPOS_PATH`` is unset.

    Raises
    ------
    FileNotFoundError
        If ``GARPOS_PATH`` is set but ``f90lib`` / ``lib_raytrace.so`` cannot
        be located in the path or any of its immediate subdirectories.
    """
    root = _garpos_path_root()
    if root is None:
        return None
    logger.debug(f"Found GARPOS_PATH: {root}")

    for base in _candidate_subroots(root):
        f90lib = base / "f90lib"
        raytrace = f90lib / "lib_raytrace.so"
        if f90lib.is_dir() and raytrace.exists():
            logger.debug(f"Found f90lib directory: {f90lib}")
            logger.debug(f"Found lib_raytrace.so: {raytrace}")
            return str(f90lib), str(raytrace)

    raise FileNotFoundError(
        f"f90lib/lib_raytrace.so not found under {root} or its immediate subdirectories"
    )


def _load_drive_garpos_from_path() -> Callable:
    """Dynamically load ``drive_garpos`` from ``$GARPOS_PATH``.

    Looks for ``garpos_main.py`` under ``$GARPOS_PATH`` or any of its
    immediate subdirectories (e.g. ``garpos_v102/garpos_main.py``).

    Returns
    -------
    Callable
        The ``drive_garpos`` function from the discovered module.

    Raises
    ------
    FileNotFoundError
        If ``GARPOS_PATH`` is unset, or ``garpos_main.py`` cannot be located.
    AttributeError
        If the discovered module has no ``drive_garpos``.
    """
    root = _garpos_path_root()
    if root is None:
        raise FileNotFoundError("GARPOS_PATH environment variable is not set")
    logger.debug(f"Found GARPOS_PATH: {root}")

    garpos_main: Path | None = None
    for base in _candidate_subroots(root):
        candidate = base / "garpos_main.py"
        if candidate.exists():
            garpos_main = candidate
            break
    if garpos_main is None:
        raise FileNotFoundError(
            f"garpos_main.py not found under {root} or its immediate subdirectories"
        )
    logger.debug(f"Found garpos_main.py: {garpos_main}")

    # Setup module
    module_name = str(garpos_main.parent.stem)
    spec = importlib.util.spec_from_file_location(
        module_name, str(garpos_main.parent / "__init__.py")
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    garpos_main_module = importlib.import_module(f"{garpos_main.parent.stem}.{garpos_main.stem}")
    return garpos_main_module.drive_garpos


@cache
def get_lib_paths() -> tuple[str, str] | None:
    """Public, cached resolver for the GARPOS Fortran library paths.

    See :func:`_resolve_lib_paths` for behavior.
    """
    return _resolve_lib_paths()


@cache
def get_drive_garpos() -> Callable:
    """Resolve ``drive_garpos`` from whichever GARPOS install is available.

    Resolution order:
        1. ``from garpos import drive_garpos`` — pip-installed package.
        2. :func:`_load_drive_garpos_from_path` — dynamic load from ``GARPOS_PATH``.

    The result is cached for the process lifetime.

    Raises
    ------
    GarposNotInstalledError
        If neither install path yields a usable ``drive_garpos`` callable.
        The error message lists both attempted strategies and how to fix them.
    """
    # Strategy 1: pip-installed package.
    try:
        from garpos import drive_garpos as pkg_drive_garpos

        logger.debug("Resolved drive_garpos from installed `garpos` package")
        return pkg_drive_garpos
    except ImportError as pkg_exc:
        pkg_error = str(pkg_exc)

    # Strategy 2: GARPOS_PATH dynamic import.
    try:
        fn = _load_drive_garpos_from_path()
        logger.debug("Resolved drive_garpos from GARPOS_PATH")
        return fn
    except (FileNotFoundError, AttributeError) as src_exc:
        src_error = str(src_exc)

    raise GarposNotInstalledError(
        "Could not locate a GARPOS install.\n"
        f"  - `import garpos` failed: {pkg_error}\n"
        f"  - GARPOS_PATH fallback failed: {src_error}\n"
        "Install the `garpos` package, or set GARPOS_PATH to a checkout of "
        "https://github.com/s-watanabe-ihp/garpos."
    )
