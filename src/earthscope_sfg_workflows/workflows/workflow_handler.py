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

from earthscope_sfg_workflows.data_mgmt.core import FileManager
from earthscope_sfg_workflows.data_mgmt.model import AssetKind, DirectoryTree
from earthscope_sfg_workflows.logging import ProcessLogger as logger
from earthscope_sfg_workflows.logging import change_all_logger_dirs

from ..config.file_config import (
    DEFAULT_FILE_TYPES_TO_DOWNLOAD,
    DEFAULT_INTERMEDIATE_FILE_TYPES_TO_DOWNLOAD,
    AssetType,
)
from earthscope_sfg_tools.datamodels.metadata import Site
from ..modeling.garpos_tools.schemas import InversionParams
from .modeling.garpos_handler import GarposHandler
from .pipelines.config import (
    DFOP00Config,
    NovatelConfig,
    PositionUpdateConfig,
    QCPinConfig,
    QCPipelineConfig,
    RinexConfig,
    SV3PipelineConfig,
)
from .pipelines.qc_pipeline import QC_JOBS, QCPipeline
from .pipelines.sv3_pipeline import SV3_JOBS, SV3Pipeline
from .session import StationSession
from .workspace import _build_ports

# Expose job-key lists for external introspection.
pipeline_jobs = list(SV3_JOBS)
qc_pipeline_jobs = list(QC_JOBS)


def _to_kinds(
    file_types: "list[AssetType] | list[str] | str | None",
) -> "list[AssetKind] | None":
    """Translate legacy ``AssetType`` / string file-type specs to :class:`AssetKind` values."""
    if file_types is None:
        return None
    if isinstance(file_types, str):
        file_types = [file_types]
    kinds: list[AssetKind] = []
    for ft in file_types:
        val = ft.value if isinstance(ft, AssetType) else str(ft).lower()
        try:
            kinds.append(AssetKind(val))
        except ValueError:
            pass
    return kinds or None

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
        s3_sync_bucket: str | None = None,
        *,
        workspace=None,  # accepted for call-site compatibility
    ) -> None:
        del workspace  # sessions are always built from directory + ports
        if directory is None:
            directory = os.environ.get("MAIN_DIRECTORY", ".")
        self._directory = Path(directory)
        self.s3_sync_bucket = s3_sync_bucket

        self._ports = _build_ports(self._directory)
        # Single FileManager shared by all sessions — it is workspace-rooted and
        # stateless except for the optional remote backend, which is workspace-wide.
        self._file_manager = FileManager(
            directory_tree=DirectoryTree(root=UPath(self._ports.root)),
            file_backend=self._ports.files,
        )
        # Registry: (network, station) -> StationSession
        self._sessions: dict[tuple[str, str], StationSession] = {}
        self._garpos_handlers: dict[tuple[str, str], GarposHandler] = {}
        self._active: StationSession | None = None

    # ------------------------------------------------------------------
    # Session registry
    # ------------------------------------------------------------------

    def _get_or_build_session(self, network: str, station: str) -> StationSession:
        """Return the cached session for *(network, station)*, building it on first use."""
        key = (network, station)
        if key not in self._sessions:
            self._sessions[key] = StationSession(
                network=network,
                station=station,
                catalog=self._ports.catalog,
                file_manager=self._file_manager,
                archive=self._ports.archive,
                remote_root=self.s3_sync_bucket,
            )
        return self._sessions[key]

    @property
    def _session(self) -> StationSession:
        """The active session; raises if none has been set yet."""
        if self._active is None:
            raise ValueError(
                "No active session. Call set_network_station_campaign() first."
            )
        return self._active

    @property
    def workspace(self) -> StationSession | None:
        """The active :class:`StationSession` (backwards-compat alias)."""
        return self._active

    # ------------------------------------------------------------------
    # Scope management
    # ------------------------------------------------------------------

    @property
    def current_network_name(self) -> str | None:
        return self._active.network_name if self._active else None

    @property
    def current_station_name(self) -> str | None:
        return self._active.station_name if self._active else None

    @property
    def current_campaign_name(self) -> str | None:
        return self._active.campaign_name if self._active else None

    @property
    def current_survey_name(self) -> str | None:
        return self._active.survey_name if self._active else None

    @property
    def current_station_metadata(self) -> "Site | None":
        return self._active.site if self._active else None

    def set_network_station_campaign(
        self,
        network_id: str,
        station_id: str | None = None,
        campaign_id: str | None = None,
    ) -> None:
        """Activate a network/station context, optionally setting a campaign.

        If the ``(network, station)`` pair has been seen before the existing
        session is reused; otherwise a new one is built.  Campaign state is
        updated only when *campaign_id* differs from the session's current
        campaign, avoiding redundant directory materialisation.
        """
        if station_id is None:
            return
        session = self._get_or_build_session(network_id, station_id)
        if campaign_id is not None and campaign_id != session.campaign_name:
            session.set_campaign(campaign_id)
            if session.campaign.layout is not None:
                change_all_logger_dirs(session.campaign.layout.logs)
                os.environ["LOG_FILE_PATH"] = str(session.campaign.layout.logs)
            logger.info(
                f"Active context: {network_id} / {station_id} / {campaign_id}"
            )
        self._active = session

    def list_campaign_directories(self) -> list[Path]:
        """List campaign subdirectories for the current station."""
        station_dir = (
            self._directory / self.current_network_name / self.current_station_name
        )
        return [
            x for x in station_dir.iterdir()
            if x.is_dir() and re.match(r"^\d{4}", x.name)
        ]

    # ------------------------------------------------------------------
    # Ingest
    # ------------------------------------------------------------------

    def ingest_add_local_data(self, directory_path: Path) -> None:
        """Scan *directory_path* and catalog discovered files for the active campaign."""
        self._session.ingest_local(source_dir=UPath(directory_path))

    def ingest_qcpin_tarballs(
        self,
        tarball_dir: Path | None = None,
        *,
        override: bool = False,
    ) -> None:
        """Extract ``.pin`` files from ``.tar.gz`` tarballs in *tarball_dir* and catalog them.

        Defaults to the active campaign's ``qc/`` directory when *tarball_dir* is not given.
        """
        self._session.ingest_qcpin_tarballs(tarball_dir=tarball_dir, override=override)

    def ingest_catalog_archive_data(self) -> None:
        """Populate the catalog with remote archive paths for the active campaign."""
        self._session.discover_remote()

    def ingest_download_archive_data(
        self,
        file_types: "list[AssetType] | list[str] | None" = DEFAULT_FILE_TYPES_TO_DOWNLOAD,
        rinex_1Hz: bool = False,
    ) -> None:
        """Download cataloged archive files for the active campaign."""
        self.download_data(file_types=file_types, rinex_1Hz=rinex_1Hz)

    def ingest_download_intermediate_archive_data(
        self,
        file_types: "list[AssetType] | list[str] | None" = DEFAULT_INTERMEDIATE_FILE_TYPES_TO_DOWNLOAD,
        rinex_1Hz: bool = False,
    ) -> None:
        """Download intermediate-processing files from the archive."""
        self.ingest_download_archive_data(file_types=file_types, rinex_1Hz=rinex_1Hz)

    def download_data(
        self,
        file_types: "list[AssetType] | list[str] | str | None" = DEFAULT_FILE_TYPES_TO_DOWNLOAD,
        override: bool = False,
        rinex_1Hz: bool = False,
    ) -> None:
        """Download cataloged remote files for the active campaign.

        *file_types* filters by asset kind; pass ``None`` to download all kinds.
        """
        self._session.download_remote(
            kinds=_to_kinds(file_types),
            override=override,
            rinex_1hz=rinex_1Hz,
        )

    def get_site_metadata(
        self,
        site_metadata: "Site | Path | str | None" = None,
    ) -> "Site | None":
        """Return site metadata for the active station, optionally from a file."""
        if isinstance(site_metadata, (str, Path)):
            return Site.from_json(site_metadata)
        if site_metadata is not None:
            return site_metadata
        site = self._session.site
        if site is None:
            msg = (
                f"No site metadata found for "
                f"{self.current_network_name} {self.current_station_name}. "
                "Some functionality may be limited."
            )
            warnings.warn(msg)
            logger.warning(msg)
        return site

    # ------------------------------------------------------------------
    # Pre-Processing — SV3 pipeline
    # ------------------------------------------------------------------

    def preprocess_get_pipeline_sv3(
        self,
        primary_config: _SV3Config = None,
        secondary_config: _SV3Config = None,
    ) -> SV3Pipeline:
        """Return a configured :class:`SV3Pipeline` for the active session."""
        return self._session.get_pipeline_sv3(
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
        self._session.run_pipeline_sv3(
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
        return self._session.get_pipeline_qc(config=primary_config)

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
        self._session.run_pipeline_qc(job=job, config=primary_config)

    def qc_get_pipeline(self, config: Optional[QCPipelineConfig] = None) -> QCPipeline:
        """Return a configured :class:`QCPipeline` (alias for :meth:`preprocess_get_pipeline_qc`)."""
        return self._session.get_pipeline_qc(config=config)

    # ------------------------------------------------------------------
    # Site metadata helper
    # ------------------------------------------------------------------

    def midprocess_get_sitemeta(self, site_metadata: "Site | str | None" = None) -> Site:
        """Return site metadata, loading from file or the active session as needed."""
        if isinstance(site_metadata, (str, Path)):
            return Site.from_json(site_metadata)
        if isinstance(site_metadata, Site):
            return site_metadata
        if self._session.site is not None:
            return self._session.site
        raise ValueError("Site metadata not loaded or provided, cannot proceed")

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
        if self.s3_sync_bucket is not None:
            self.sync_from_s3(overwrite=override)
        self._session.parse_surveys(
            survey_id=survey_id,
            override=override,
            write_intermediate=write_intermediate,
        )

    def midprocess_prep_garpos(
        self,
        site_metadata: "Site | str | None" = None,
        survey_id: str | None = None,
        custom_filters: dict | None = None,
        override: bool = False,
        override_survey_parsing: bool = False,
        write_intermediate: bool = False,
    ) -> None:
        """Parse surveys then prepare GARPOS shot-data for the active campaign."""
        if self.s3_sync_bucket is not None:
            self.sync_from_s3(overwrite=override_survey_parsing)
        self._session.parse_surveys(
            survey_id=survey_id,
            override=override_survey_parsing,
            write_intermediate=write_intermediate,
        )
        self.modeling_get_garpos_handler().prepare_shotdata_garpos(
            survey_id=survey_id,
            custom_filters=custom_filters,
            overwrite=override,
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
        if self.s3_sync_bucket is None:
            if require:
                raise RuntimeError("S3 bucket not configured; set s3_sync_bucket.")
            logger.warning("S3 synchronization skipped: s3_sync_bucket not configured")
            return False
        self._session.configure_remote(self.s3_sync_bucket)
        return True

    def midprocess_sync_station_data_s3(self, overwrite: bool = False, **_) -> None:
        """Upload station TileDB arrays to S3."""
        if self._ensure_remote():
            self._session.push_station_to_remote(overwrite=overwrite)

    def midprocess_sync_campaign_data_s3(self, overwrite: bool = False, **_) -> None:
        """Upload campaign processed files (SVP, RINEX, logs) to S3."""
        if self._ensure_remote():
            self._session.push_campaign_to_remote(overwrite=overwrite)

    def sync_from_s3(self, overwrite: bool = False) -> None:
        """Mirror data from the remote S3 prefix into the local workspace."""
        self._ensure_remote(require=True)
        self._session.pull_from_remote(overwrite=overwrite)

    # ------------------------------------------------------------------
    # Modeling — GARPOS
    # ------------------------------------------------------------------

    def modeling_get_garpos_handler(self) -> GarposHandler:
        """Return the cached :class:`GarposHandler` for the active session, building it on first use."""
        if self._session.site is None:
            raise ValueError("Site metadata not loaded; cannot get GarposHandler")
        key = (self._session.network_name, self._session.station_name)
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
        survey_id: str | None = None,
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
        self._session.run_pipeline_qc(config=pre_process_config)

        handler = self.modeling_get_garpos_handler()
        qc_pipeline = self._session.get_pipeline_qc()
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
