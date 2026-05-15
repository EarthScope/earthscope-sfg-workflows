"""PipelineService — pipeline construction and execution for a StationSession."""

from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING, Literal, Optional

from pride_ppp import PrideCLIConfig

from earthscope_sfg_workflows.logging import GarposLogger as logger, change_all_logger_dirs
from earthscope_sfg_workflows.utils.model_update import validate_and_merge_config
from earthscope_sfg_workflows.pipelines.config import (
    DFOP00Config,
    NovatelConfig,
    PositionUpdateConfig,
    QCPipelineConfig,
    RinexConfig,
    SV3PipelineConfig,
)
from earthscope_sfg_workflows.pipelines.qc_pipeline import QC_JOBS, QCPipeline
from earthscope_sfg_workflows.pipelines.sv3_pipeline import SV3_JOBS, SV3Pipeline

if TYPE_CHECKING:
    from earthscope_sfg_workflows.workflows.session import StationSession

_Config = (
    SV3PipelineConfig
    | PrideCLIConfig
    | NovatelConfig
    | RinexConfig
    | DFOP00Config
    | PositionUpdateConfig
)


class ProcessingService:
    """Pipeline construction and execution scoped to a :class:`StationSession`.

    Owns the cached pipeline instances (``SV3Pipeline``, ``QCPipeline``) and
    all config-merging logic so that :class:`StationSession` stays free of
    pipeline concerns.

    Attributes
    ----------
    config : _Config or None
        Default pipeline configuration applied when no per-call config is
        provided.

    Methods
    -------
    get_sv3(config, secondary_config)
        Return a configured ``SV3Pipeline`` for the current scope.
    run_sv3(job, config, secondary_config)
        Run an ``SV3Pipeline`` job for the current scope.
    get_qc(config, secondary_config)
        Return a configured ``QCPipeline`` for the current scope.
    run_qc(job, config)
        Run a ``QCPipeline`` job for the current scope.
    parse_surveys(survey_id, override, write_intermediate)
        Parse surveys and write CSVs into survey directories.
    """

    def __init__(self, session: "StationSession", config=None) -> None:
        """Initialize the service.

        Parameters
        ----------
        session : StationSession
            The active station session providing scope, catalog, and layout
            information.
        config : _Config or None, optional
            Default pipeline configuration. Applied as the first override when
            no per-call config is supplied. Default is ``None``.
        """
        self._s = session
        self.config = config
        self._sv3_pipeline: Optional[SV3Pipeline] = None
        self._qc_pipeline: Optional[QCPipeline] = None

    def _ensure_log_dir(self) -> None:
        """Route all loggers to the active campaign's logs directory, if set.

        Has no effect when no campaign layout is currently active.
        """
        layout = self._s.active_campaign_layout
        if layout is not None:
            change_all_logger_dirs(layout.logs)

    # ------------------------------------------------------------------
    # SV3 pipeline
    # ------------------------------------------------------------------

    def get_sv3(
        self, config: "_Config | None" = None, secondary_config: "_Config | None" = None
    ) -> SV3Pipeline:
        """Return a configured ``SV3Pipeline`` for the current scope.

        Config merging order: defaults → construction config → *config* →
        *secondary_config*.

        Parameters
        ----------
        config : _Config or None, optional
            Primary config override. Defaults to the value set at construction
            when ``None``.
        secondary_config : _Config or None, optional
            Secondary config applied on top of the primary merge. Default is
            ``None``.

        Returns
        -------
        SV3Pipeline
            Fully configured pipeline instance (cached between calls).
        """
        effective = config if config is not None else self.config
        base = SV3PipelineConfig()
        merged = base.model_copy()
        if effective is not None:
            if isinstance(effective, SV3PipelineConfig):
                effective = effective.model_dump()
            merged = validate_and_merge_config(base_class=merged, override_config=effective)
        if secondary_config is not None:
            if isinstance(secondary_config, SV3PipelineConfig):
                secondary_config = secondary_config.model_dump()
            merged = validate_and_merge_config(base_class=merged, override_config=secondary_config)

        if self._sv3_pipeline is None:
            self._sv3_pipeline = SV3Pipeline(
                catalog=self._s._catalog,
                scope=self._s.scope,
                config=merged,
                campaign_layout=self._s.campaign_layout,
                tiledb_layout=self._s.tiledb_layout,
            )
        else:
            self._sv3_pipeline.config = merged
        return self._sv3_pipeline

    def run_sv3(
        self,
        job: Literal[
            "all",
            "intermediate",
            "process_novatel",
            "build_rinex",
            "run_pride",
            "process_kinematic",
            "process_dfop00",
            "refine_shotdata",
            "process_svp",
        ] = "all",
        config: "_Config | None" = None,
        secondary_config: "_Config | None" = None,
    ) -> None:
        """Run an ``SV3Pipeline`` *job* for the current scope.

        Parameters
        ----------
        job : str, optional
            Name of the pipeline job to execute. Must be one of the keys in
            ``SV3_JOBS``. Default is ``"all"``.
        config : _Config or None, optional
            Primary config override passed to :meth:`get_sv3`. Default is
            ``None``.
        secondary_config : _Config or None, optional
            Secondary config override passed to :meth:`get_sv3`. Default is
            ``None``.

        Raises
        ------
        AssertionError
            If *job* is not a recognised ``SV3_JOBS`` key.
        Exception
            Re-raises any exception produced by the pipeline job after logging
            the error.
        """
        assert job in SV3_JOBS, f"Job must be one of {list(SV3_JOBS.keys())}"
        self._ensure_log_dir()
        pipeline = self.get_sv3(config=config, secondary_config=secondary_config)
        try:
            SV3_JOBS[job](pipeline)
        except Exception as e:
            logger.error(f"SV3 job '{job}' failed: {e}")
            raise

    # ------------------------------------------------------------------
    # QC pipeline
    # ------------------------------------------------------------------

    def get_qc(
        self, config: "QCPipelineConfig | None" = None, secondary_config: "_Config | None" = None
    ) -> QCPipeline:
        """Return a configured ``QCPipeline`` for the current scope.

        Parameters
        ----------
        config : QCPipelineConfig or None, optional
            Primary config override. Default is ``None``.
        secondary_config : _Config or None, optional
            Secondary config applied on top of the primary merge. Default is
            ``None``.

        Returns
        -------
        QCPipeline
            Fully configured pipeline instance (cached between calls).
        """
        base = QCPipelineConfig()
        merged = base.model_copy()
        if config is not None:
            if isinstance(config, QCPipelineConfig):
                config = config.model_dump()
            merged = validate_and_merge_config(base_class=merged, override_config=config)
        if secondary_config is not None:
            if isinstance(secondary_config, QCPipelineConfig):
                secondary_config = secondary_config.model_dump()
            merged = validate_and_merge_config(base_class=merged, override_config=secondary_config)

        if self._qc_pipeline is None:
            self._qc_pipeline = QCPipeline(
                catalog=self._s._catalog,
                scope=self._s.scope,
                config=merged,
                campaign_layout=self._s.campaign_layout,
                tiledb_layout=self._s.tiledb_layout,
            )
        else:
            self._qc_pipeline.config = merged
        return self._qc_pipeline

    def run_qc(
        self,
        job: Literal[
            "all",
            "process_qcpin",
            "build_rinex",
            "run_pride",
            "process_kinematic",
            "refine_shotdata",
        ] = "all",
        config: "QCPipelineConfig | None" = None,
    ) -> None:
        """Run a ``QCPipeline`` *job* for the current scope.

        Parameters
        ----------
        job : str, optional
            Name of the pipeline job to execute. Must be one of the keys in
            ``QC_JOBS``. Default is ``"all"``.
        config : QCPipelineConfig or None, optional
            Config override passed to :meth:`get_qc`. Default is ``None``.

        Raises
        ------
        AssertionError
            If *job* is not a recognised ``QC_JOBS`` key.
        Exception
            Re-raises any exception produced by the pipeline job after logging
            the error.
        """
        assert job in QC_JOBS, f"Job must be one of {list(QC_JOBS.keys())}"
        self._ensure_log_dir()
        pipeline = self.get_qc(config=config)
        try:
            QC_JOBS[job](pipeline)
        except Exception as e:
            logger.error(f"QC job '{job}' failed: {e}")
            raise

    # ------------------------------------------------------------------
    # Survey parsing (mid-processing)
    # ------------------------------------------------------------------

    def parse_surveys(
        self,
        survey_id: str | None = None,
        *,
        override: bool = False,
        write_intermediate: bool = False,
    ) -> None:
        """Parse surveys for the active campaign and write CSVs into survey dirs.

        Parameters
        ----------
        survey_id : str or None, optional
            Restrict processing to the survey with this ID. When ``None`` all
            surveys in the campaign are processed. Default is ``None``.
        override : bool, optional
            When ``True``, overwrite existing CSV files. Default is ``False``.
        write_intermediate : bool, optional
            When ``True``, also write kinematic-position and IMU-position CSVs
            alongside the shotdata CSV. Default is ``False``.

        Raises
        ------
        ValueError
            If site metadata is not loaded on the session.
        ValueError
            If campaign metadata is not loaded on the session.
        ValueError
            If *survey_id* is specified but not found in the campaign metadata.
        """
        self._ensure_log_dir()
        from earthscope_sfg_tools.tiledb_integration import (
            TDBIMUPositionArray,
            TDBKinPositionArray,
            TDBShotDataArray,
        )
        from earthscope_sfg_tools.datamodels.metadata import Survey

        if self._s.site is None:
            raise ValueError(
                "parse_surveys requires site metadata; ensure it was loaded at construction."
            )
        campaign_meta = self._s.campaign_meta
        if campaign_meta is None:
            raise ValueError("Campaign metadata must be loaded before parse_surveys")

        tiledb = self._s.tiledb_layout
        campaign = self._s.ensure_campaign()

        shotDataTDB = TDBShotDataArray(tiledb.shotdata)

        with open(campaign.metadata_file, "w") as f:
            json.dump(campaign_meta.model_dump(mode="json"), f, indent=4)

        surveys_to_process: list[Survey] = [
            s for s in campaign_meta.surveys if survey_id is None or survey_id == s.id
        ]
        if not surveys_to_process:
            raise ValueError(f"Survey {survey_id} not found in campaign {campaign_meta.name}.")

        for survey in surveys_to_process:
            self._s.set_survey(survey_id=survey.id)
            survey_root = self._s.survey_dir

            shotdata_file_name = f"{survey.id}_{survey.type.value}_shotdata.csv".replace(" ", "")
            shotdata_dest = survey_root / shotdata_file_name

            if not shotdata_dest.exists() or shotdata_dest.stat().st_size == 0 or override:
                df = shotDataTDB.read_df(start=survey.start, end=survey.end)
                if df.empty:
                    logger.warning(
                        f"No shot data found for survey {survey.id} from "
                        f"{survey.start} to {survey.end}, skipping survey."
                    )
                    continue
                df.to_csv(shotdata_dest)

            if write_intermediate:
                kin_name = f"{survey.id}_{survey.type.value}_kinpositiondata.csv".replace(" ", "")
                kin_dest = survey_root / kin_name
                if not kin_dest.exists() or kin_dest.stat().st_size == 0 or override:
                    kin_tdb = TDBKinPositionArray(tiledb.kin_position)
                    kin_df = kin_tdb.read_df(start=survey.start, end=survey.end)
                    if kin_df.empty:
                        logger.warning(f"No kinposition data found for survey {survey.id}")
                    else:
                        kin_df.to_csv(kin_dest)

                imu_name = f"{survey.id}_{survey.type.value}_imupositiondata.csv".replace(" ", "")
                imu_dest = survey_root / imu_name
                if not imu_dest.exists() or imu_dest.stat().st_size == 0 or override:
                    imu_tdb = TDBIMUPositionArray(tiledb.imu_position)
                    imu_df = imu_tdb.read_df(start=survey.start, end=survey.end)
                    if imu_df.empty:
                        logger.warning(f"No imuposition data found for survey {survey.id}")
                    else:
                        imu_df.to_csv(imu_dest)

            with open(self._s.survey_metadata_file, "w") as f:
                json.dump(survey.model_dump(mode="json"), f, indent=4)


__all__ = ["ProcessingService"]
