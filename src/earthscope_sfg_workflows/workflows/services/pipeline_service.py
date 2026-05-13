"""PipelineService — pipeline execution operations for a StationSession."""
from __future__ import annotations

from typing import TYPE_CHECKING, Literal, Optional

if TYPE_CHECKING:
    from earthscope_sfg_workflows.workflows.pipelines.sv3_pipeline import SV3Pipeline
    from earthscope_sfg_workflows.workflows.pipelines.qc_pipeline import QCPipeline
    from earthscope_sfg_workflows.workflows.session import StationSession


class PipelineService:
    """Pipeline construction and execution scoped to a :class:`StationSession`.

    *config* is stored at construction and used as the default for all pipeline
    calls.  Pass ``config=`` per-call to override for a single invocation.
    """

    def __init__(self, session: "StationSession", config=None) -> None:
        self._s = session
        self.config = config

    def get_sv3(self, config=None, secondary_config=None) -> "SV3Pipeline":
        """Return an ``SV3Pipeline`` for the current scope.

        *config* defaults to the value set at construction when not passed.
        """
        return self._s.get_pipeline_sv3(
            config=config if config is not None else self.config,
            secondary_config=secondary_config,
        )

    def run_sv3(
        self,
        job: Literal[
            "all", "intermediate", "process_novatel", "build_rinex",
            "run_pride", "process_kinematic", "process_dfop00",
            "refine_shotdata", "process_svp",
        ] = "all",
        config=None,
        secondary_config=None,
    ) -> None:
        """Run an ``SV3Pipeline`` job for the current scope.

        *config* defaults to the value set at construction when not passed.
        """
        self._s.run_pipeline_sv3(
            job=job,
            config=config if config is not None else self.config,
            secondary_config=secondary_config,
        )

    def get_qc(self, config=None, secondary_config=None) -> "QCPipeline":
        """Return a ``QCPipeline`` for the current scope."""
        return self._s.get_pipeline_qc(config=config, secondary_config=secondary_config)

    def run_qc(
        self,
        job: Literal[
            "all", "process_qcpin", "build_rinex", "run_pride",
            "process_kinematic", "refine_shotdata",
        ] = "all",
        config=None,
    ) -> None:
        """Run a ``QCPipeline`` job for the current scope."""
        self._s.run_pipeline_qc(job=job, config=config)

    def parse_surveys(
        self,
        survey_id: str | None = None,
        *,
        override: bool = False,
        write_intermediate: bool = False,
    ) -> None:
        """Parse surveys for the active campaign.

        Raises :class:`~earthscope_sfg_workflows.utils.custom_warnings_exceptions.MetadataRequiredError`
        if site or campaign metadata is not loaded.
        """
        from earthscope_sfg_workflows.utils.custom_warnings_exceptions import MetadataRequiredError

        if self._s.site is None:
            raise MetadataRequiredError(
                "parse_surveys requires site metadata; ensure set_station() fetched it."
            )
        if self._s.campaign_meta is None:
            raise MetadataRequiredError(
                "parse_surveys requires campaign metadata; call set_campaign() after loading site."
            )
        self._s.parse_surveys(
            survey_id=survey_id,
            override=override,
            write_intermediate=write_intermediate,
        )


__all__ = ["PipelineService"]

