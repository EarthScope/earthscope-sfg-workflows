"""WorkflowHandler — session-registry-backed workflow entry point.

Holds a registry of :class:`~earthscope_sfg_workflows.workflows.session.StationSession`
instances keyed by ``(network, station)``.  Sessions are constructed once and
reused whenever the same network/station pair is made active again; only
campaign/survey state is updated on context switches.

All workflow operations delegate to the active session rather than managing
scope state directly.
"""

import os
import re
import warnings
from pathlib import Path
from typing import Literal, Optional

from pride_ppp.specifications.cli import PrideCLIConfig
from upath import UPath

from earthscope_sfg_workflows.data_mgmt.model import AssetKind
from earthscope_sfg_workflows.logging import ProcessLogger as logger
from earthscope_sfg_workflows.logging import change_all_logger_dirs

from ..data_mgmt.model import DEFAULT_PREPROCESS_KINDS, DEFAULT_INTERMEDIATE_KINDS
from earthscope_sfg_tools.datamodels.metadata import Site
from ..modeling.garpos_tools.schemas import InversionParams
from ..modeling.garpos_tools.garpos_handler import GarposHandler
from ..pipelines.config import (
    DFOP00Config,
    NovatelConfig,
    PositionUpdateConfig,
    QCPinConfig,
    QCPipelineConfig,
    RinexConfig,
    SV3PipelineConfig,
)
from ..pipelines.qc_pipeline import QC_JOBS, QCPipeline
from ..pipelines.sv3_pipeline import SV3_JOBS, SV3Pipeline
from .session import StationSession
from .workspace import Workspace

# Expose job-key lists for external introspection.
pipeline_jobs = list(SV3_JOBS)
qc_pipeline_jobs = list(QC_JOBS)


_SV3Config = (
    SV3PipelineConfig
    | PrideCLIConfig
    | NovatelConfig
    | RinexConfig
    | DFOP00Config
    | PositionUpdateConfig
    | dict
    | None
)
_QCConfig = (
    QCPipelineConfig
    | PrideCLIConfig
    | RinexConfig
    | PositionUpdateConfig
    | QCPinConfig
    | dict
    | None
)


class WorkflowHandler:
    """Workflow entry point backed by a registry of :class:`StationSession` instances.

    Sessions are created once per ``(network, station)`` pair and cached.
    Switching context with :meth:`set_network_station_campaign` reuses the
    cached session; only the campaign/survey slot is updated.
    """

    def __init__(
        self,
        directory: Path | str | None = None,
        *,
        workspace: Workspace | None = None,
    ) -> None:
        if directory is None:
            # Leave as None when MAIN_DIRECTORY is unset so Workspace can fall
            # back to Environment.main_directory_GEOLAB() for GEOLAB deployments.
            directory = os.environ.get("MAIN_DIRECTORY")
        self._workspace: Workspace = workspace or Workspace(root_dir=directory)
        self._garpos_handlers: dict[tuple[str, str], GarposHandler] = {}

    # ------------------------------------------------------------------
    # Session registry
    # ------------------------------------------------------------------

    @property
    def _session(self) -> StationSession:
        """The active session; raises if none has been set yet."""
        if self._workspace._active is None:
            raise ValueError(
                "No active session. Call set_network_station_campaign() first."
            )
        return self._workspace._active

    @property
    def directory(self) -> Path:
        """Root directory of the workspace."""
        return self._workspace.root

    @property
    def s3_sync_bucket(self) -> str | None:
        return self._workspace.s3_sync_bucket

    # ------------------------------------------------------------------
    # Scope management
    # ------------------------------------------------------------------

    def set_network_station_campaign(
        self,
        network_id: str,
        station_id: str | None = None,
        campaign_id: str | None = None,
    ) -> None:
        """Activate a network/station context, optionally setting a campaign."""
        if station_id is None:
            return
        session = self._workspace.set_active(network_id, station_id, campaign_id)
        if campaign_id is not None and session._campaign_layout is not None:
            change_all_logger_dirs(session._campaign_layout.logs)
            os.environ["LOG_FILE_PATH"] = str(session._campaign_layout.logs)
            logger.info(f"Active context: {network_id} / {station_id} / {campaign_id}")

    def list_campaign_directories(self) -> list[Path]:
        """List campaign subdirectories for the current station."""
        station_dir = (
            self._workspace.root / self._session.scope.network / self._session.scope.station
        )
        return [
            x for x in station_dir.iterdir()
            if x.is_dir() and re.match(r"^\d{4}", x.name)
        ]

    # ------------------------------------------------------------------
    # Ingest
    # ------------------------------------------------------------------

    def ingest_discover_archive(self):
        """Discover and catalog EarthScope archive URLs for the active campaign.

        Returns the :class:`~earthscope_sfg_workflows.data_mgmt.model.IngestReport`
        from the discovery operation. Call :meth:`download_data` afterwards to
        fetch the cataloged files.
        """
        return self._session.ingest.discover_remote()

    def ingest_add_local_data(self, directory_path: Path) -> None:
        """Scan *directory_path* and catalog discovered files for the active campaign."""
        self._session.ingest.local(source_dir=UPath(directory_path))

    def ingest_qcpin_tarballs(
        self,
        tarball_dir: Path | None = None,
        *,
        override: bool = False,
    ) -> None:
        """Extract ``.pin`` files from ``.tar.gz`` tarballs in *tarball_dir* and catalog them.

        Defaults to the active campaign's ``qc/`` directory when *tarball_dir* is not given.
        """
        self._session.ingest.qcpin_tarballs(tarball_dir=tarball_dir, override=override)

    def download_data(
        self,
        kinds: "list[AssetKind | str] | frozenset[AssetKind] | None" = DEFAULT_PREPROCESS_KINDS,
        override: bool = False,
        rinex_1Hz: bool = False,
    ) -> None:
        """Download cataloged remote files for the active campaign.

        *kinds* filters by asset kind; pass ``None`` to download all kinds.
        Accepts :class:`~earthscope_sfg_workflows.data_mgmt.model.AssetKind` values,
        plain strings (e.g. ``"novatel"``), or ``None`` for all.
        Defaults to :data:`~earthscope_sfg_workflows.data_mgmt.model.DEFAULT_PREPROCESS_KINDS`.
        """
        if isinstance(kinds, (frozenset, set)):
            kinds_list: list[AssetKind] | None = list(kinds)
        elif kinds is None:
            kinds_list = None
        else:
            if isinstance(kinds, str):
                kinds = [kinds]
            kinds_list = []
            for k in kinds:
                if isinstance(k, AssetKind):
                    kinds_list.append(k)
                else:
                    try:
                        kinds_list.append(AssetKind(str(k).lower()))
                    except ValueError:
                        pass
            kinds_list = kinds_list or None
        self._session.ingest.download_remote(
            kinds=kinds_list,
            override=override,
            rinex_1hz=rinex_1Hz,
        )


    # ------------------------------------------------------------------
    # Pre-Processing — SV3 pipeline
    # ------------------------------------------------------------------

    def preprocess_get_pipeline_sv3(
        self,
        primary_config: _SV3Config = None,
        secondary_config: _SV3Config = None,
    ) -> SV3Pipeline:
        """Return a configured :class:`SV3Pipeline` for the active session."""
        return self._session.pipeline.get_sv3(
            config=primary_config, secondary_config=secondary_config
        )

    def preprocess_run_pipeline_sv3(
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
        primary_config: _SV3Config = None,
        secondary_config: _SV3Config = None,
    ) -> None:
        """Run the SV3 pipeline *job* for the active session."""
        assert job in SV3_JOBS, f"Job must be one of {list(SV3_JOBS)}"
        self._session.pipeline.run_sv3(
            job=job,
            config=primary_config,
            secondary_config=secondary_config,
        )

    # ------------------------------------------------------------------
    # Pre-Processing — QC pipeline
    # ------------------------------------------------------------------

    def preprocess_get_pipeline_qc(
        self,
        primary_config: _QCConfig = None,
        secondary_config: _QCConfig = None,
    ) -> QCPipeline:
        """Return a configured :class:`QCPipeline` for the active session."""
        return self._session.pipeline.get_qc(config=primary_config)

    def preprocess_run_pipeline_qc(
        self,
        job: Literal[
            "all",
            "process_qcpin",
            "build_rinex",
            "run_pride",
            "process_kinematic",
            "refine_shotdata",
        ] = "all",
        primary_config: _QCConfig = None,
        secondary_config: _QCConfig = None,  # noqa: ARG002 — reserved for future use
    ) -> None:
        """Run the QC pipeline *job* for the active session."""
        assert job in QC_JOBS, f"Job must be one of {list(QC_JOBS)}"
        self._session.pipeline.run_qc(job=job, config=primary_config)

    def qc_get_pipeline(self, config: Optional[QCPipelineConfig] = None) -> QCPipeline:
        """Return a configured :class:`QCPipeline` (alias for :meth:`preprocess_get_pipeline_qc`)."""
        return self._session.pipeline.get_qc(config=config)

    # ------------------------------------------------------------------
    # Mid-processing
    # ------------------------------------------------------------------

    def midprocess_parse_surveys(
        self,
        site_metadata: "Site | str | None" = None,  # noqa: ARG002 — session owns metadata
        override: bool = False,
        write_intermediate: bool = False,
        survey_id: str | None = None,
    ) -> None:
        """Parse surveys for the active campaign and write shot-data CSVs."""
        if self._workspace.s3_sync_bucket is not None:
            self.sync_from_s3(overwrite=override)
        self._session.pipeline.parse_surveys(
            survey_id=survey_id,
            override=override,
            write_intermediate=write_intermediate,
        )

    def midprocess_prep_garpos(
        self,
        survey_id: str | None = None,
        custom_filters: dict | None = None,
        override_garpos_prep: bool = False,
        override_survey_parsing: bool = False,
        write_intermediate: bool = False,
    ) -> None:
        """Parse surveys then prepare GARPOS shot-data for the active campaign."""
  
        self._session.pipeline.parse_surveys(
            survey_id=survey_id,
            override=override_survey_parsing,
            write_intermediate=write_intermediate,
        )
        self.modeling_get_garpos_handler().prepare_shotdata_garpos(
            survey_id=survey_id,
            custom_filters=custom_filters,
            overwrite=override_garpos_prep,
        )

    # ------------------------------------------------------------------
    # S3 sync
    # ------------------------------------------------------------------

    def _ensure_remote(self, *, require: bool = False) -> bool:
        """Configure the remote backend on the shared FileManager and return True.

        Returns ``False`` (with a warning) when no bucket is configured and
        *require* is ``False``; raises :class:`RuntimeError` when *require* is
        ``True``.
        """
        if self._workspace.s3_sync_bucket is None:
            if require:
                raise RuntimeError(
                    "S3 bucket not configured; set the S3_SYNC_BUCKET environment variable."
                )
            logger.warning("S3 synchronization skipped: S3_SYNC_BUCKET not configured")
            return False
        self._session.configure_remote(self._workspace.s3_sync_bucket)
        return True

    def midprocess_sync_station_data_s3(self, overwrite: bool = False, **_) -> None:
        """Upload station TileDB arrays to S3."""
        if self._ensure_remote():
            self._session.sync.push_station(overwrite=overwrite)

    def midprocess_sync_campaign_data_s3(self, overwrite: bool = False, **_) -> None:
        """Upload campaign processed files (SVP, RINEX, logs) to S3."""
        if self._ensure_remote():
            self._session.sync.push_campaign(overwrite=overwrite)

    def sync_from_s3(self, overwrite: bool = False) -> None:
        """Mirror data from the remote S3 prefix into the local workspace."""
        self._ensure_remote(require=True)
        self._session.sync.pull(overwrite=overwrite)

    # ------------------------------------------------------------------
    # Modeling — GARPOS
    # ------------------------------------------------------------------

    def modeling_get_garpos_handler(self) -> GarposHandler:
        """Return the cached :class:`GarposHandler` for the active session, building it on first use."""
        if self._session.site is None:
            raise ValueError("Site metadata not loaded; cannot get GarposHandler")
        key = (self._session.scope.network, self._session.scope.station)
        if key not in self._garpos_handlers:
            self._garpos_handlers[key] = GarposHandler(station_session=self._session)
        return self._garpos_handlers[key]

    def modeling_run_garpos(
        self,
        survey_id: str | None = None,
        run_id: str = "Test",
        iterations: int = 1,
        override: bool = False,
        custom_settings: dict | None = None,
    ) -> None:
        """Run GARPOS for the active campaign."""
        self.modeling_get_garpos_handler().run_garpos(
            survey_id=survey_id,
            run_id=run_id,
            iterations=iterations,
            override=override,
            custom_settings=custom_settings,
        )

    def modeling_plot_shotdata_replies_per_transponder(
        self,
        save_fig: bool = True,
        show_fig: bool = False,
    ) -> None:
        """Plot shot-data reply counts per transponder."""
        self.modeling_get_garpos_handler().plot_shotdata_replies_per_transponder(
            savefig=save_fig, showfig=show_fig
        )

    def modeling_plot_flagged_residuals(
        self,
        survey_id: str | None = None,
        run_id: str = "Test",
        save_fig: bool = True,
        show_fig: bool = False,
    ) -> None:
        """Plot before/after flagged residuals per transponder."""
        self.modeling_get_garpos_handler().plot_residuals_per_transponder_before_and_after(
            survey_id=survey_id, run_id=run_id, savefig=save_fig, showfig=show_fig
        )

    def modeling_plot_garpos_residuals(
        self,
        survey_id: str | None = None,
        run_id: str = "Test",
        subplots: bool = True,
        save_fig: bool = True,
        show_fig: bool = False,
    ) -> None:
        """Plot remaining residuals per transponder after GARPOS inversion."""
        self.modeling_get_garpos_handler().plot_remaining_residuals_per_transponder(
            survey_id=survey_id,
            run_id=run_id,
            subplots=subplots,
            savefig=save_fig,
            showfig=show_fig,
        )

    def modeling_plot_garpos_results(
        self,
        survey_id: str | None = None,
        run_id: str = "Test",
        residuals_filter: float | None = 10,
        save_fig: bool = True,
        show_fig: bool = False,
    ) -> None:
        """Plot time-series GARPOS inversion results."""
        self.modeling_get_garpos_handler().plot_ts_results(
            survey_id=survey_id,
            run_id=run_id,
            res_filter=residuals_filter,
            savefig=save_fig,
            showfig=show_fig,
        )

    def qc_process_and_model(
        self,
        site_metadata: "Site | str | None" = None,
        run_id: str | int = 0,
        iterations: int = 1,
        garpos_settings: "dict | InversionParams | None" = None,
        garpos_override: bool = False,
        pre_process_config: Optional[QCPipelineConfig] = None,
    ) -> None:
        """Run the full QC pipeline then GARPOS modeling end-to-end."""
        self._session.pipeline.run_qc(config=pre_process_config)

        handler = self.modeling_get_garpos_handler()
        qc_pipeline = self._session.pipeline.get_qc()
        gp_dir_list = handler.parse_surveys_qc(
            override=False, shotdata_uri=qc_pipeline.qcShotDataFinalTDB.uri
        )

        handler = self.modeling_get_garpos_handler()
        handler.run_garpos(
            surveys=gp_dir_list,
            run_id=run_id,
            iterations=iterations,
            override=garpos_override,
            custom_settings=garpos_settings,
        )
        for garpos_layout in gp_dir_list:
            handler._plot_ts_results(
                survey_id=garpos_layout.root.parent.name,
                run_id=run_id,
                res_filter=10,
                savefig=True,
                showfig=False,
                results_dir=garpos_layout.results,
            )
