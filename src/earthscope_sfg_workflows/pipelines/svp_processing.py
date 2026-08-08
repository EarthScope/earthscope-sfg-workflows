"""Shared CTD/Seabird -> sound-velocity-profile (SVP) conversion.

Used by both :class:`~earthscope_sfg_workflows.pipelines.sv3_pipeline.SV3Pipeline`
and :class:`~earthscope_sfg_workflows.pipelines.qc_pipeline.QCPipeline`, since the
resulting SVP CSV is consumed from the same place regardless of which pipeline
produced it: :attr:`~earthscope_sfg_workflows.data_mgmt.model.CampaignLayout.svp_file`.
"""

import dataclasses
from pathlib import Path

from earthscope_sfg_tools.seafloor_site_tools.soundspeed_operations import (
    CTD_to_svp_v1,
    CTD_to_svp_v2,
    seabird_to_soundvelocity,
)

from ..data_mgmt.model import AssetKind, SFGScope
from ..data_mgmt.ports import AssetCatalogPort
from ..logging import ProcessLogger
from .exceptions import NoSVPFound


def process_svp_for_scope(
    catalog: AssetCatalogPort,
    scope: SFGScope,
    destination: Path,
    override: bool = False,
) -> None:
    """Convert cataloged CTD/Seabird files into an SVP CSV at ``destination``.

    Processing order:

    1. Tries each CTD file with ``CTD_to_svp_v2``, then ``CTD_to_svp_v1``.
    2. If no CTD file yields a valid SVP, tries each Seabird file with
       ``seabird_to_soundvelocity``.

    The first successful SVP is written to ``destination`` and processing stops.

    Parameters
    ----------
    catalog : AssetCatalogPort
        Asset catalog to query for CTD/Seabird files and mark as processed.
    scope : SFGScope
        Network/station/campaign scope to query the catalog with.
    destination : Path
        Where to write the resulting SVP CSV.
    override : bool, optional
        If ``True``, forces reprocessing even if ``destination`` already
        exists.  Default is ``False``.

    Raises
    ------
    NoSVPFound
        If no CTD or Seabird files are cataloged for the scope.
    """
    if destination.exists() and not override:
        return

    ctd_entries = catalog.assets_for(
        network=scope.network, station=scope.station, campaign=scope.campaign, kind=AssetKind.CTD
    )
    seabird_entries = catalog.assets_for(
        network=scope.network,
        station=scope.station,
        campaign=scope.campaign,
        kind=AssetKind.SEABIRD,
    )

    if not ctd_entries and not seabird_entries:
        response = (
            f"No CTD or SEABIRD Files Found to Process for {scope.network} "
            f"{scope.station} {scope.campaign}"
        )
        ProcessLogger.error(response)
        raise NoSVPFound(response)

    ctd_processing_functions = [CTD_to_svp_v2, CTD_to_svp_v1]

    # Try processing CTD files first
    for ctd_entry in ctd_entries:
        for function in ctd_processing_functions:
            try:
                svp_df = function(ctd_entry.local_path)
                if not svp_df.empty:
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    svp_df.to_csv(destination, index=False)
                    ctd_entry = dataclasses.replace(ctd_entry, is_processed=True)
                    catalog.update(ctd_entry)  # mark as processed
                    ProcessLogger.info(
                        f"Processed SVP data from CTD file {ctd_entry.local_path} "
                        f"to dataframe with {function.__name__}"
                    )
                    ProcessLogger.info(f"Saved SVP dataframe to {str(destination)}")
                    return
            except Exception as e:
                ProcessLogger.error(
                    f"Error processing CTD file {ctd_entry.local_path} "
                    f"with {function.__name__}: {e}"
                )
                continue

    # If no CTD files produced SVP, try Seabird files
    for seabird_entry in seabird_entries:
        try:
            svp_df = seabird_to_soundvelocity(seabird_entry.local_path, ProcessLogger.logger)
            if not svp_df.empty:
                destination.parent.mkdir(parents=True, exist_ok=True)
                svp_df.to_csv(destination, index=False)
                seabird_entry = dataclasses.replace(seabird_entry, is_processed=True)
                catalog.update(seabird_entry)  # mark as processed
                ProcessLogger.info(
                    f"Processed SVP data from Seabird file {seabird_entry.local_path} "
                    f"and saved to {str(destination)}"
                )
                return
        except Exception as e:
            ProcessLogger.error(f"Error processing Seabird file {seabird_entry.local_path}: {e}")
            continue
