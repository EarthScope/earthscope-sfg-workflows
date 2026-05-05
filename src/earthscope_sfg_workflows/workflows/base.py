"""Minimal :class:`WorkflowBase` — replacement for the legacy ``WorkflowABC``.

Owns nothing except the :class:`Workspace` and a ``mid_process_workflow``
flag. All scope state, ports, and data-mgmt access live on the workspace.

See ``plans/prds/2026-05-05-workflow-base-and-facades.md``.
"""

from __future__ import annotations

from abc import ABC
from collections.abc import Callable
from functools import wraps
from pathlib import Path
from typing import Concatenate, ParamSpec, Protocol, TypeVar

from .workspace import Workspace

P = ParamSpec("P")
R = TypeVar("R")


class HasWorkspace(Protocol):
    """Anything that exposes a ``workspace`` attribute."""

    workspace: Workspace


class WorkflowBase(ABC):
    """Minimal base for all workflow classes.

    Subclasses receive a single :class:`Workspace`. They reach the data
    layer only via the four façades (``layout``, ``metadata``, ``assets``,
    ``ingest``) on it. There is no ``self.asset_catalog``, no
    ``self.directory_handler``, no ``self.current_*_dir``.
    """

    mid_process_workflow: bool = False

    def __init__(self, workspace: Workspace) -> None:
        self.workspace = workspace

    @property
    def directory(self) -> Path:
        """Workspace root directory. Backwards-compat helper."""
        return self.workspace.root

    def close(self) -> None:
        self.workspace.close()


def validate_network_station_campaign(
    func: Callable[Concatenate[HasWorkspace, P], R],
) -> Callable[Concatenate[HasWorkspace, P], R]:
    """Decorator: ensure ``self.workspace`` has network/station/campaign set."""

    @wraps(func)
    def wrapper(self: HasWorkspace, *args: P.args, **kwargs: P.kwargs) -> R:
        ws = self.workspace
        if ws.network_name is None:
            raise ValueError("Network name not set; call set_network() first")
        if ws.station_name is None:
            raise ValueError("Station name not set; call set_station() first")
        if ws.campaign_name is None:
            raise ValueError("Campaign name not set; call set_campaign() first")
        return func(self, *args, **kwargs)

    return wrapper


def validate_network_station(
    func: Callable[Concatenate[HasWorkspace, P], R],
) -> Callable[Concatenate[HasWorkspace, P], R]:
    """Decorator: ensure ``self.workspace`` has network/station set."""

    @wraps(func)
    def wrapper(self: HasWorkspace, *args: P.args, **kwargs: P.kwargs) -> R:
        ws = self.workspace
        if ws.network_name is None:
            raise ValueError("Network name not set; call set_network() first")
        if ws.station_name is None:
            raise ValueError("Station name not set; call set_station() first")
        return func(self, *args, **kwargs)

    return wrapper


__all__ = [
    "WorkflowBase",
    "HasWorkspace",
    "validate_network_station_campaign",
    "validate_network_station",
]
