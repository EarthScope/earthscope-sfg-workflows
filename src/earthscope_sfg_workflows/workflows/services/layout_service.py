"""LayoutService — directory layout operations for a StationSession."""
from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from earthscope_sfg_workflows.data_mgmt.model import (
        CampaignLayout,
        GARPOSLayout,
        StationLayout,
        SurveyLayout,
        TileDBLayout,
    )
    from earthscope_sfg_workflows.workflows.session import StationSession


class LayoutService:
    """Exposes directory-layout operations scoped to a :class:`StationSession`.

    All methods that require campaign or survey raise ``ValueError`` if the
    corresponding slot is not set on the session.
    """

    def __init__(self, session: "StationSession") -> None:
        self._s = session

    # ------------------------------------------------------------------
    # Station-level (always available)
    # ------------------------------------------------------------------

    def tiledb_layout(self) -> "TileDBLayout":
        """TileDB array layout for the current station."""
        return self._s.tiledb_layout()

    @property
    def station_dir(self) -> Path:
        """Root directory for the current station."""
        return self._s.station_dir

    @property
    def network_dir(self) -> Path:
        """Root directory for the current network."""
        return self._s.network_dir

    # ------------------------------------------------------------------
    # Campaign-level (raise if campaign not set)
    # ------------------------------------------------------------------

    def campaign_root(self) -> "CampaignLayout":
        """Campaign layout. Raises ``ValueError`` if no campaign is set."""
        return self._s.campaign_layout()

    def ensure_campaign(self) -> "CampaignLayout":
        """Materialise campaign directories and return the layout."""
        return self._s.ensure_campaign()

    # ------------------------------------------------------------------
    # Survey-level (raise if survey not set)
    # ------------------------------------------------------------------

    def survey_dir(self) -> "SurveyLayout":
        """Survey layout. Raises ``ValueError`` if no survey is set."""
        if not self._s.scope.survey:
            raise ValueError("survey_dir requires a survey to be set; call set_survey() first")
        return self._s._file_manager.directory_tree.survey(
            network=self._s.scope.network,
            station=self._s.scope.station,
            campaign=self._s.scope.campaign,
            survey=self._s.scope.survey,
        )

    def garpos_survey(self) -> "GARPOSLayout":
        """GARPOS survey layout. Raises ``ValueError`` if no survey is set."""
        return self._s.garpos_survey()

    def ensure_garpos_survey(self) -> "GARPOSLayout":
        """Materialise GARPOS survey directories and return the layout."""
        return self._s.ensure_garpos_survey()

    @property
    def survey_metadata_file(self) -> Path:
        """Metadata file path for the active survey."""
        return self._s.survey_metadata_file


__all__ = ["LayoutService"]

