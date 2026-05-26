# load_utils

`earthscope_sfg_workflows.modeling.garpos_tools.load_utils`

GARPOS Fortran library bootstrap helpers.

Resolves `GARPOS_PATH` and locates `f90lib/lib_raytrace.so`, which the GARPOS
ray-tracing layer needs at runtime.

Public entrypoints:
    - :func:`get_drive_garpos` — resolve the ``drive_garpos`` callable from
      whichever GARPOS install is available (pip package or ``GARPOS_PATH``).
    - :func:`get_lib_paths` — resolve the Fortran ``(f90lib_dir, lib_raytrace.so)``
      paths for a ``GARPOS_PATH`` source checkout.

Callers should not import ``garpos.drive_garpos`` directly — that hides
whichever install path the user actually has.

## class `GarposNotInstalledError`

Raised when neither a pip-installed nor a `GARPOS_PATH` GARPOS is reachable.
