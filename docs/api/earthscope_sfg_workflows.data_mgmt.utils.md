# utils

`earthscope_sfg_workflows.data_mgmt.utils`

Cross-cutting helpers for the data_mgmt package.

Includes a guard decorator that requires a network/station/campaign context
to be set on a workflow handler, and a utility for computing merge signatures
from shotdata + kin_position TileDB arrays.

## class `HasNetworkStationCampaign`

Structural type for objects exposing the current scope attributes.

**Fields**

| Name | Type | Description |
|---|---|---|
| `current_network` | `str \| None` |  |
| `current_station` | `str \| None` |  |
| `current_campaign` | `str \| None` |  |

## `check_network_station_campaign(func: collections.abc.Callable[typing.Concatenate[earthscope_sfg_workflows.data_mgmt.utils.HasNetworkStationCampaign, ~P], ~R]) -> collections.abc.Callable[typing.Concatenate[earthscope_sfg_workflows.data_mgmt.utils.HasNetworkStationCampaign, ~P], ~R]`

Guard decorator: raise ``ValueError`` if any of network/station/campaign is unset.

## `get_merge_signature_shotdata(shotdata: earthscope_sfg_tools.tiledb_integration.arrays.TDBShotDataArray, kin_position: earthscope_sfg_tools.tiledb_integration.arrays.TDBKinPositionArray) -> tuple[list[str], list[numpy.datetime64]]`

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
