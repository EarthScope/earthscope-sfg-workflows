"""Workflow-local wrappers around platform-specific Go binaries.

These wrappers are kept inside ``earthscope_sfg_workflows`` because the
orchestration layer needs CLI flags that are not yet exposed by the modern
``earthscope_sfg_tools`` Go-binary wrappers (e.g. ``-tdbpos``, ``-procs``).
"""

from . import novatel_binary_operations

__all__ = ["novatel_binary_operations"]
