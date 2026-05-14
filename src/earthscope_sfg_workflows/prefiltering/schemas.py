"""Pydantic schemas for shotdata pre-filtering configuration."""

from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field, field_serializer


class FilterLevel(StrEnum):
    """Acoustic-diagnostic strictness level used by ``AcousticFilterConfig``.

    Attributes
    ----------
    GOOD : str
        Strictest thresholds — highest data quality required.
    OK : str
        Moderate thresholds — default level.
    DIFFICULT : str
        Most permissive thresholds — retains data from challenging conditions.
    """

    GOOD = "GOOD"
    OK = "OK"
    DIFFICULT = "DIFFICULT"


class AcousticFilterConfig(BaseModel):
    """Configuration for the acoustic-diagnostics filter.

    Attributes
    ----------
    enabled : bool
        When ``True``, acoustic-diagnostics filtering is applied. Default is
        ``False``.
    level : FilterLevel
        Strictness level controlling SNR, DBV, and XC thresholds. Default is
        ``FilterLevel.OK``.

    Methods
    -------
    serialize_level(level)
        Serialize ``FilterLevel`` to its string value for JSON/YAML output.
    """

    enabled: bool = Field(False, description="Whether to enable acoustic diagnostics filtering")
    level: FilterLevel = Field(
        FilterLevel.OK,
        description="Level of acoustic diagnostics to filter. Options: GOOD, OK, DIFFICULT",
    )

    @field_serializer("level")
    def serialize_level(level: FilterLevel) -> str:
        """Serialize ``FilterLevel`` to its string value for JSON/YAML output.

        Parameters
        ----------
        level : FilterLevel
            The filter level enum member to serialize.

        Returns
        -------
        str
            String representation of *level*.
        """
        return level.value


class PingRepliesFilterConfig(BaseModel):
    """Configuration for the minimum-ping-replies filter.

    Attributes
    ----------
    enabled : bool
        When ``True``, shots with fewer than ``min_replies`` replies are
        removed. Default is ``False``.
    min_replies : int
        Minimum reply count required to retain a shot. Default is ``3``.
    """

    enabled: bool = Field(False, description="Whether to enable ping replies filtering")
    min_replies: int = Field(3, description="Minimum number of replies required to keep a shot")


class MaxDistFromCenterConfig(BaseModel):
    """Configuration for the max-distance-from-array-center filter.

    Attributes
    ----------
    enabled : bool
        When ``True``, shots beyond ``max_distance_m`` from the array center
        are removed. Default is ``True``.
    max_distance_m : float
        Maximum horizontal distance from the array center in metres. Default
        is ``150.0``.
    """

    enabled: bool = Field(True, description="Whether to enable max distance from center filtering")
    max_distance_m: float = Field(
        150.0,
        description="Maximum distance from the survey center in meters to keep a shot",
    )


class PrideResidualsConfig(BaseModel):
    """Configuration for the PRIDE-PPP kinematic residual filter.

    Attributes
    ----------
    enabled : bool
        When ``True``, shots coinciding with high PRIDE WRMS epochs are
        removed. Default is ``False``.
    max_residual_mm : float
        Maximum allowable PRIDE WRMS residual in millimetres. Default is
        ``8.0``.
    """

    enabled: bool = Field(False, description="Whether to enable PRIDE residuals filtering")
    max_residual_mm: float = Field(
        8.0, description="Maximum PRIDE residual in millimeters to keep a shot"
    )


class FilterConfig(BaseModel):
    """Top-level container for all shotdata pre-filter configurations.

    Attributes
    ----------
    acoustic_filters : AcousticFilterConfig
        Configuration for acoustic-diagnostics (SNR, DBV, XC) filtering.
    ping_replies : PingRepliesFilterConfig
        Configuration for minimum ping-replies filtering.
    max_distance_from_center : MaxDistFromCenterConfig
        Configuration for maximum distance from array-center filtering.
    pride_residuals : PrideResidualsConfig
        Configuration for PRIDE-PPP kinematic residual filtering.

    Methods
    -------
    update(custom_config)
        Apply nested overrides from *custom_config* in-place.
    """

    acoustic_filters: AcousticFilterConfig = Field(
        default_factory=AcousticFilterConfig,
        description="Configuration for acoustic diagnostics filtering",
    )
    ping_replies: PingRepliesFilterConfig = Field(
        default_factory=PingRepliesFilterConfig,
        description="Configuration for ping replies filtering",
    )
    max_distance_from_center: MaxDistFromCenterConfig = Field(
        default_factory=MaxDistFromCenterConfig,
        description="Configuration for max distance from center filtering",
    )
    pride_residuals: PrideResidualsConfig = Field(
        default_factory=PrideResidualsConfig,
        description="Configuration for PRIDE residuals filtering",
    )

    def update(self, custom_config: dict[str, Any]) -> None:
        """Apply nested overrides from *custom_config* in-place.

        Parameters
        ----------
        custom_config : dict
            Mapping whose keys match ``FilterConfig`` field names and whose
            values are either scalars or sub-dicts whose keys match the
            corresponding nested model's field names.
        """
        for key, value in custom_config.items():
            if hasattr(self, key):
                attr = getattr(self, key)
                if isinstance(attr, BaseModel) and isinstance(value, dict):
                    for sub_key, sub_value in value.items():
                        if hasattr(attr, sub_key):
                            setattr(attr, sub_key, sub_value)
                else:
                    setattr(self, key, value)
