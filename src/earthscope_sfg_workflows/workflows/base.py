"""Minimal :class:`WorkflowBase` — **deprecated**.

.. deprecated::
    Use :class:`~earthscope_sfg_workflows.workflows.session.StationSession` directly
    for session state, and :class:`~earthscope_sfg_workflows.workflows.workflow_handler.WorkflowHandler`
    for orchestration. ``WorkflowBase`` will be removed in a future release.

The :func:`validate_network_station_campaign` and :func:`validate_network_station`
decorators are still exported from this module for backwards compatibility.
"""

from __future__ import annotations

import warnings
from abc import ABC
from collections.abc import Callable
from functools import wraps
from pathlib import Path
from typing import TYPE_CHECKING, Concatenate, ParamSpec, Protocol, TypeVar

if TYPE_CHECKING:
    from .session import StationSession as Workspace

P = ParamSpec("P")
R = TypeVar("R")


class HasWorkspace(Protocol):
    """Anything that exposes a ``workspace`` attribute."""

    workspace: Workspace


class WorkflowBase(ABC):
    """Minimal base for all workflow classes.

    .. deprecated::
        Subclass from nothing and hold ``self.workspace: StationSession`` directly.
        ``WorkflowBase`` will be removed in a future release.
    """

    mid_process_workflow: bool = False

    def __init__(self, workspace: Workspace) -> None:
        """Bind to a `Workspace`. The workspace owns ports, scope, and metadata."""
        warnings.warn(
            "WorkflowBase is deprecated and will be removed in a future release. "
            "Hold self.workspace: StationSession directly instead of subclassing WorkflowBase.",
            DeprecationWarning,
            stacklevel=2,
        )
        self.workspace = workspace

    @property
    def directory(self) -> Path:
        """Workspace root directory. Backwards-compat helper."""
        return self.workspace.root

    # ------------------------------------------------------------------
    # Backwards-compat scope accessors. New code should use
    # ``self.workspace.{network,station,campaign}_name`` directly.
    # ------------------------------------------------------------------

    @property
    def current_network_name(self) -> str:
        """Active network name; alias for `self.workspace.network_name`."""
        return self.workspace.network_name

    @property
    def current_station_name(self) -> str:
        """Active station name; alias for `self.workspace.station_name`."""
        return self.workspace.station_name

    @property
    def current_campaign_name(self) -> str | None:
        """Active campaign name; alias for `self.workspace.campaign_name`."""
        return self.workspace.campaign_name

    def close(self) -> None:
        """Close the underlying workspace and release any held resources."""
        self.workspace.close()


def validate_network_station_campaign(
    func: Callable[Concatenate[HasWorkspace, P], R],
) -> Callable[Concatenate[HasWorkspace, P], R]:
    """Decorator: ensure ``self.workspace`` has network/station/campaign set.

    Network and station are fixed at :class:`~.session.CampaignSession`
    construction and are always non-None; the guards below are defensive for
    future workspace implementations.  Campaign is the meaningful runtime
    check — it must be set via ``set_campaign()`` before calling decorated
    methods.
    """

    @wraps(func)
    def wrapper(self: HasWorkspace, *args: P.args, **kwargs: P.kwargs) -> R:
        ws = self.workspace
        if ws.network_name is None:
            raise ValueError("workspace has no network set")
        if ws.station_name is None:
            raise ValueError("workspace has no station set")
        if ws.campaign_name is None:
            raise ValueError("Campaign not set; call set_campaign() first")
        return func(self, *args, **kwargs)

    return wrapper


def validate_network_station(
    func: Callable[Concatenate[HasWorkspace, P], R],
) -> Callable[Concatenate[HasWorkspace, P], R]:
    """Decorator: ensure ``self.workspace`` has network/station set.

    Network and station are fixed at :class:`~.session.CampaignSession`
    construction, so this decorator is effectively a no-op for sessions.
    It is kept for defensive checks and documentation purposes.
    """

    @wraps(func)
    def wrapper(self: HasWorkspace, *args: P.args, **kwargs: P.kwargs) -> R:
        ws = self.workspace
        if ws.network_name is None:
            raise ValueError("workspace has no network set")
        if ws.station_name is None:
            raise ValueError("workspace has no station set")
        return func(self, *args, **kwargs)

    return wrapper


__all__ = [
    "WorkflowBase",
    "HasWorkspace",
    "validate_network_station_campaign",
    "validate_network_station",
]
