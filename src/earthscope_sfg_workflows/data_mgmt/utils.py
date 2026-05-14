"""Cross-cutting helpers for the data_mgmt package.

Includes a guard decorator that requires a network/station/campaign context
to be set on a workflow handler, and a utility for computing merge signatures
from shotdata + kin_position TileDB arrays.
"""

from collections.abc import Callable
from functools import wraps
from typing import (
    Concatenate,
    ParamSpec,
    Protocol,
    TypeVar,
)

import numpy as np

from earthscope_sfg_workflows.logging import ProcessLogger as logger
from earthscope_sfg_tools.tiledb_integration import (
    TDBKinPositionArray,
    TDBShotDataArray,
)

P = ParamSpec("P")
R = TypeVar("R")


class HasNetworkStationCampaign(Protocol):
    """Structural type for objects exposing the current scope attributes."""

    current_network: str | None
    current_station: str | None
    current_campaign: str | None


def check_network_station_campaign(
    func: Callable[Concatenate[HasNetworkStationCampaign, P], R],
) -> Callable[Concatenate[HasNetworkStationCampaign, P], R]:
    """Guard decorator: raise ``ValueError`` if any of network/station/campaign is unset."""

    @wraps(func)
    def wrapper(self: HasNetworkStationCampaign, *args: P.args, **kwargs: P.kwargs) -> R:
        if self.current_network is None:
            raise ValueError("Network name not set, use change_working_station")
        if self.current_station is None:
            raise ValueError("Station name not set, use change_working_station")
        if self.current_campaign is None:
            raise ValueError("campaign name not set, use change_working_station")
        return func(self, *args, **kwargs)

    return wrapper


def get_merge_signature_shotdata(
    shotdata: TDBShotDataArray, kin_position: TDBKinPositionArray
) -> tuple[list[str], list[np.datetime64]]:
    """
    Get the merge signature for the shotdata and kin_position data

    Parameters
    ----------
    shotdata : TDBShotDataArray
        The shotdata array
    kin_position : TDBKinPositionArray
        The kinposition array

    Returns
    -------
    Tuple[List[str], List[np.datetime64]]
        The merge signature and the dates to merge
    """

    merge_signature = []
    shotdata_dates: np.ndarray = shotdata.get_unique_dates(
        "pingTime"
    )  # get the unique dates from the shotdata
    kin_position_dates: np.ndarray = kin_position.get_unique_dates(
        "time"
    )  # get the unique dates from the kin_position

    # get the intersection of the dates
    dates = np.intersect1d(shotdata_dates, kin_position_dates).tolist()
    if len(dates) == 0:
        error_message = "No common dates found between shotdata and kin_position"
        logger.error(error_message)
        raise ValueError(error_message)

    for date in dates:
        merge_signature.append(str(date))

    return merge_signature, dates
