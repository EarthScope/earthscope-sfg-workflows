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


def _normalize_asset_kinds(
    kinds: "list[AssetKind | str] | frozenset[AssetKind] | str | None",
) -> "list[AssetKind] | None":
    """Normalize varied asset-kind inputs to a plain list of ``AssetKind``.

    Accepts a frozenset/set, a list of mixed ``AssetKind``/str values, a bare
    string, or ``None`` (meaning "all kinds").  Unrecognised string values are
    silently dropped.  Returns ``None`` when the input is ``None`` or resolves
    to an empty list.
    """
    if kinds is None:
        return None
    if isinstance(kinds, (frozenset, set)):
        return list(kinds) or None
    if isinstance(kinds, str):
        kinds = [kinds]
    result: list[AssetKind] = []
    for k in kinds:
        if isinstance(k, AssetKind):
            result.append(k)
        else:
            try:
                result.append(AssetKind(str(k).lower()))
            except ValueError:
                pass
    return result or None


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
    """Recommended entry point for seafloor-geodesy workflows.

    Wraps a :class:`~earthscope_sfg_workflows.workflows.workspace.Workspace` to
    provide a flat, user-friendly API for notebooks and processing scripts.
    All workflow operations (ingest, pipeline, modeling, sync) delegate to the
    internally managed session registry.

    **Quick start**::

        handler = WorkflowHandler("/data/sfg")
        handler.set_network_station_campaign("ONC", "CASAMA", "2023_JUN")
        handler.ingest_discover_archive()
        handler.download_data()
        handler.preprocess_run_pipeline_sv3()

    Sessions are created once per ``(network, station)`` pair and reused on
    context switches — switching campaigns never rebuilds TileDB arrays.

    Attributes
    ----------
    directory : Path
        Root directory of the workspace.
    s3_sync_bucket : str or None
        S3 bucket for remote sync, read from the ``S3_SYNC_BUCKET`` environment
        variable.  ``None`` when the variable is not set.

    Methods
    -------
    set_network_station_campaign(network_id, station_id, campaign_id)
        Activate a network/station context, optionally setting a campaign.
    list_campaign_directories()
        List campaign subdirectories for the current station.
    ingest_discover_archive()
        Discover and catalog EarthScope archive URLs for the active campaign.
    ingest_add_local_data(directory_path)
        Scan a local directory and catalog discovered files.
    ingest_qcpin_tarballs(tarball_dir, override)
        Extract ``.pin`` files from ``.tar.gz`` tarballs and catalog them.
    download_data(kinds, override, rinex_1hz)
        Download cataloged remote files for the active campaign.
    preprocess_get_pipeline_sv3(primary_config, secondary_config)
        Return a configured ``SV3Pipeline`` for the active session.
    preprocess_run_pipeline_sv3(job, primary_config, secondary_config)
        Run a named SV3 pipeline job for the active session.
    preprocess_get_pipeline_qc(primary_config, secondary_config)
        Return a configured ``QCPipeline`` for the active session.
    preprocess_run_pipeline_qc(job, primary_config, secondary_config)
        Run a named QC pipeline job for the active session.
    qc_get_pipeline(config)
        Return a configured ``QCPipeline`` (alias for ``preprocess_get_pipeline_qc``).
    midprocess_parse_surveys(site_metadata, override, write_intermediate, survey_id)
        Parse surveys for the active campaign and write shot-data CSVs.
    midprocess_prep_garpos(survey_id, custom_filters, override_garpos_prep, override_survey_parsing, write_intermediate)
        Parse surveys then prepare GARPOS shot-data for the active campaign.
    midprocess_sync_station_data_s3(overwrite)
        Upload station TileDB arrays to S3.
    midprocess_sync_campaign_data_s3(overwrite)
        Upload campaign processed files to S3.
    sync_from_s3(overwrite)
        Mirror data from the remote S3 prefix into the local workspace.
    modeling_get_garpos_handler()
        Return the cached ``GarposHandler`` for the active session.
    modeling_run_garpos(survey_id, run_id, iterations, override, custom_settings)
        Run GARPOS for the active campaign.
    modeling_plot_shotdata_replies_per_transponder(save_fig, show_fig)
        Plot shot-data reply counts per transponder.
    modeling_plot_flagged_residuals(survey_id, run_id, save_fig, show_fig)
        Plot before/after flagged residuals per transponder.
    modeling_plot_garpos_residuals(survey_id, run_id, subplots, save_fig, show_fig)
        Plot remaining residuals per transponder after GARPOS inversion.
    modeling_plot_garpos_results(survey_id, run_id, residuals_filter, save_fig, show_fig)
        Plot time-series GARPOS inversion results.
    qc_process_and_model(site_metadata, run_id, iterations, garpos_settings, garpos_override, pre_process_config)
        Run the full QC pipeline then GARPOS modeling end-to-end.
    """

    def __init__(
        self,
        directory: Path | str | None = None,
        *,
        workspace: Workspace | None = None,
    ) -> None:
        """Initialize the handler and its internal workspace.

        Parameters
        ----------
        directory : Path or str or None, optional
            Root directory for the workspace.  When ``None``, the value of the
            ``MAIN_DIRECTORY`` environment variable is used; if that is also
            unset, ``Workspace`` falls back to the GEOLAB default path.
        workspace : Workspace or None, optional
            Pre-constructed ``Workspace`` instance.  When provided, *directory*
            is ignored.  Intended for testing and advanced use cases.
        """
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
        """Return the active session; raises if none has been set yet."""
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
        """S3 bucket for remote sync, read from the ``S3_SYNC_BUCKET`` environment variable."""
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
        """Activate a network/station context, optionally setting a campaign.

        Parameters
        ----------
        network_id : str
            Network identifier (e.g. ``"ONC"``).
        station_id : str or None, optional
            Station identifier (e.g. ``"CASAMA"``).  When ``None`` the call is
            a no-op.
        campaign_id : str or None, optional
            Campaign identifier (e.g. ``"2023_JUN"``).  When provided, logger
            directories and the ``LOG_FILE_PATH`` environment variable are
            updated to the campaign log directory.
        """
        if station_id is None:
            return
        session = self._workspace.set_active(network_id, station_id, campaign_id)
        if campaign_id is not None and session._campaign_layout is not None:
            change_all_logger_dirs(session._campaign_layout.logs)
            os.environ["LOG_FILE_PATH"] = str(session._campaign_layout.logs)
            logger.info(f"Active context: {network_id} / {station_id} / {campaign_id}")

    def list_campaign_directories(self) -> list[Path]:
        """List campaign subdirectories for the current station.

        Returns
        -------
        list of Path
            Subdirectories of the current station directory whose names begin
            with a four-digit year (matched by ``r"^\\d{4}"``).
        """
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

        Queries the EarthScope archive for assets belonging to the active
        network/station/campaign and populates the session catalog.  Call
        :meth:`download_data` afterwards to fetch the cataloged files.

        Returns
        -------
        IngestReport
            Summary of discovered and newly cataloged assets.
        """
        return self._session.ingest.discover_remote()

    def ingest_add_local_data(self, directory_path: Path) -> None:
        """Scan a local directory and catalog discovered files for the active campaign.

        Parameters
        ----------
        directory_path : Path
            Local filesystem path to scan for ingestable files.
        """
        self._session.ingest.local(source_dir=UPath(directory_path))

    def ingest_qcpin_tarballs(
        self,
        tarball_dir: Path | None = None,
        *,
        override: bool = False,
    ) -> None:
        """Extract ``.pin`` files from ``.tar.gz`` tarballs and catalog them.

        Parameters
        ----------
        tarball_dir : Path or None, optional
            Directory containing ``.tar.gz`` archives.  Defaults to the active
            campaign's ``qc/`` directory when ``None``.
        override : bool, optional
            When ``True``, re-extract and re-catalog files that are already
            present.  Default is ``False``.
        """
        self._session.ingest.qcpin_tarballs(tarball_dir=tarball_dir, override=override)

    def download_data(
        self,
        kinds: "list[AssetKind | str] | frozenset[AssetKind] | None" = DEFAULT_PREPROCESS_KINDS,
        override: bool = False,
        rinex_1hz: bool = False,
    ) -> None:
        """Download cataloged remote files for the active campaign.

        Parameters
        ----------
        kinds : list of AssetKind or str, frozenset of AssetKind, or None, optional
            Asset kinds to download.  Accepts
            :class:`~earthscope_sfg_workflows.data_mgmt.model.AssetKind` values,
            plain strings (e.g. ``"novatel"``), or ``None`` to download all
            available kinds.  Defaults to
            :data:`~earthscope_sfg_workflows.data_mgmt.model.DEFAULT_PREPROCESS_KINDS`.
        override : bool, optional
            When ``True``, re-download files that already exist locally.
            Default is ``False``.
        rinex_1hz : bool, optional
            When ``True``, prefer 1 Hz RINEX files over the default rate.
            Default is ``False``.
        """
        self._session.ingest.download_remote(
            kinds=_normalize_asset_kinds(kinds),
            override=override,
            rinex_1hz=rinex_1hz,
        )


    # ------------------------------------------------------------------
    # Pre-Processing — SV3 pipeline
    # ------------------------------------------------------------------

    def preprocess_get_pipeline_sv3(
        self,
        primary_config: _SV3Config = None,
        secondary_config: _SV3Config = None,
    ) -> SV3Pipeline:
        """Return a configured ``SV3Pipeline`` for the active session.

        Parameters
        ----------
        primary_config : SV3PipelineConfig or PrideCLIConfig or NovatelConfig or RinexConfig or DFOP00Config or PositionUpdateConfig or dict or None, optional
            Primary configuration object or mapping applied to the pipeline.
            ``None`` uses the session default.
        secondary_config : SV3PipelineConfig or PrideCLIConfig or NovatelConfig or RinexConfig or DFOP00Config or PositionUpdateConfig or dict or None, optional
            Secondary (override) configuration merged on top of *primary_config*.
            ``None`` applies no secondary overrides.

        Returns
        -------
        SV3Pipeline
            Fully configured pipeline instance bound to the active session.
        """
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
        """Run a named SV3 pipeline job for the active session.

        Parameters
        ----------
        job : {"all", "intermediate", "process_novatel", "build_rinex", "run_pride", "process_kinematic", "process_dfop00", "refine_shotdata", "process_svp"}, optional
            Name of the pipeline step to execute.  ``"all"`` runs every step in
            sequence.  Default is ``"all"``.
        primary_config : SV3PipelineConfig or PrideCLIConfig or NovatelConfig or RinexConfig or DFOP00Config or PositionUpdateConfig or dict or None, optional
            Primary configuration for the pipeline run.
        secondary_config : SV3PipelineConfig or PrideCLIConfig or NovatelConfig or RinexConfig or DFOP00Config or PositionUpdateConfig or dict or None, optional
            Secondary (override) configuration merged on top of *primary_config*.

        Raises
        ------
        AssertionError
            When *job* is not a recognized ``SV3_JOBS`` key.
        """
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
        """Return a configured ``QCPipeline`` for the active session.

        Parameters
        ----------
        primary_config : QCPipelineConfig or PrideCLIConfig or RinexConfig or PositionUpdateConfig or QCPinConfig or dict or None, optional
            Primary configuration object or mapping applied to the pipeline.
            ``None`` uses the session default.
        secondary_config : QCPipelineConfig or PrideCLIConfig or RinexConfig or PositionUpdateConfig or QCPinConfig or dict or None, optional
            Reserved for future use; currently ignored.

        Returns
        -------
        QCPipeline
            Fully configured pipeline instance bound to the active session.
        """
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
        """Run a named QC pipeline job for the active session.

        Parameters
        ----------
        job : {"all", "process_qcpin", "build_rinex", "run_pride", "process_kinematic", "refine_shotdata"}, optional
            Name of the pipeline step to execute.  ``"all"`` runs every step in
            sequence.  Default is ``"all"``.
        primary_config : QCPipelineConfig or PrideCLIConfig or RinexConfig or PositionUpdateConfig or QCPinConfig or dict or None, optional
            Primary configuration for the pipeline run.
        secondary_config : QCPipelineConfig or PrideCLIConfig or RinexConfig or PositionUpdateConfig or QCPinConfig or dict or None, optional
            Reserved for future use; currently ignored.

        Raises
        ------
        AssertionError
            When *job* is not a recognized ``QC_JOBS`` key.
        """
        assert job in QC_JOBS, f"Job must be one of {list(QC_JOBS)}"
        self._session.pipeline.run_qc(job=job, config=primary_config)

    def qc_get_pipeline(self, config: Optional[QCPipelineConfig] = None) -> QCPipeline:
        """Return a configured ``QCPipeline`` (alias for :meth:`preprocess_get_pipeline_qc`).

        Parameters
        ----------
        config : QCPipelineConfig or None, optional
            Configuration object applied to the pipeline.  ``None`` uses the
            session default.

        Returns
        -------
        QCPipeline
            Fully configured pipeline instance bound to the active session.
        """
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
        """Parse surveys for the active campaign and write shot-data CSVs.

        When an S3 sync bucket is configured, a pull from S3 is performed
        before parsing.

        Parameters
        ----------
        site_metadata : Site or str or None, optional
            Unused; the session owns site metadata.  Retained for API
            compatibility.
        override : bool, optional
            When ``True``, re-parse surveys that have already been processed.
            Default is ``False``.
        write_intermediate : bool, optional
            When ``True``, write intermediate CSV files alongside the final
            output.  Default is ``False``.
        survey_id : str or None, optional
            Restrict parsing to a single survey.  ``None`` parses all surveys
            in the active campaign.
        """
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
        """Parse surveys then prepare GARPOS shot-data for the active campaign.

        Parameters
        ----------
        survey_id : str or None, optional
            Restrict processing to a single survey.  ``None`` processes all
            surveys in the active campaign.
        custom_filters : dict or None, optional
            Additional filter expressions applied to shot-data during GARPOS
            preparation.  ``None`` uses the handler defaults.
        override_garpos_prep : bool, optional
            When ``True``, overwrite existing GARPOS preparation outputs.
            Default is ``False``.
        override_survey_parsing : bool, optional
            When ``True``, re-parse surveys even if parsed output already
            exists.  Default is ``False``.
        write_intermediate : bool, optional
            When ``True``, write intermediate CSV files during survey parsing.
            Default is ``False``.
        """
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
        """Configure the remote backend and return True if a bucket is set."""
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
        """Upload station TileDB arrays to S3.

        Parameters
        ----------
        overwrite : bool, optional
            When ``True``, overwrite objects that already exist in S3.
            Default is ``False``.
        """
        if self._ensure_remote():
            self._session.sync.push_station(overwrite=overwrite)

    def midprocess_sync_campaign_data_s3(self, overwrite: bool = False, **_) -> None:
        """Upload campaign processed files (SVP, RINEX, logs) to S3.

        Parameters
        ----------
        overwrite : bool, optional
            When ``True``, overwrite objects that already exist in S3.
            Default is ``False``.
        """
        if self._ensure_remote():
            self._session.sync.push_campaign(overwrite=overwrite)

    def sync_from_s3(self, overwrite: bool = False) -> None:
        """Mirror data from the remote S3 prefix into the local workspace.

        Parameters
        ----------
        overwrite : bool, optional
            When ``True``, overwrite local files that already exist.
            Default is ``False``.

        Raises
        ------
        RuntimeError
            When ``S3_SYNC_BUCKET`` is not configured.
        """
        self._ensure_remote(require=True)
        self._session.sync.pull(overwrite=overwrite)

    # ------------------------------------------------------------------
    # Modeling — GARPOS
    # ------------------------------------------------------------------

    def modeling_get_garpos_handler(self) -> GarposHandler:
        """Return the cached ``GarposHandler`` for the active session, building it on first use.

        Returns
        -------
        GarposHandler
            Handler instance bound to the active session and keyed by
            ``(network, station)``.

        Raises
        ------
        ValueError
            When site metadata has not been loaded on the active session.
        """
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
        """Run GARPOS for the active campaign.

        Delegates to :meth:`GarposHandler.run_garpos` using the cached handler
        returned by :meth:`modeling_get_garpos_handler`.

        Parameters
        ----------
        survey_id : str or None, optional
            Restrict the inversion to a single survey.  ``None`` runs all
            surveys in the active campaign.
        run_id : str, optional
            Label applied to this inversion run, used for output directory
            naming.  Default is ``"Test"``.
        iterations : int, optional
            Number of GARPOS inversion iterations to perform.  Default is ``1``.
        override : bool, optional
            When ``True``, overwrite existing inversion outputs.
            Default is ``False``.
        custom_settings : dict or None, optional
            Key-value pairs merged into the GARPOS inversion configuration.
            ``None`` uses handler defaults.
        """
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
        """Plot shot-data reply counts per transponder.

        Parameters
        ----------
        save_fig : bool, optional
            When ``True``, save the figure to the campaign output directory.
            Default is ``True``.
        show_fig : bool, optional
            When ``True``, display the figure interactively.  Default is
            ``False``.
        """
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
        """Plot before/after flagged residuals per transponder.

        Parameters
        ----------
        survey_id : str or None, optional
            Survey to plot.  ``None`` uses the most recent survey.
        run_id : str, optional
            Inversion run label whose outputs are plotted.  Default is
            ``"Test"``.
        save_fig : bool, optional
            When ``True``, save the figure to the campaign output directory.
            Default is ``True``.
        show_fig : bool, optional
            When ``True``, display the figure interactively.  Default is
            ``False``.
        """
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
        """Plot remaining residuals per transponder after GARPOS inversion.

        Parameters
        ----------
        survey_id : str or None, optional
            Survey to plot.  ``None`` uses the most recent survey.
        run_id : str, optional
            Inversion run label whose outputs are plotted.  Default is
            ``"Test"``.
        subplots : bool, optional
            When ``True``, draw each transponder in its own subplot.  Default
            is ``True``.
        save_fig : bool, optional
            When ``True``, save the figure to the campaign output directory.
            Default is ``True``.
        show_fig : bool, optional
            When ``True``, display the figure interactively.  Default is
            ``False``.
        """
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
        """Plot time-series GARPOS inversion results.

        Parameters
        ----------
        survey_id : str or None, optional
            Survey to plot.  ``None`` uses the most recent survey.
        run_id : str, optional
            Inversion run label whose outputs are plotted.  Default is
            ``"Test"``.
        residuals_filter : float or None, optional
            Residual threshold (in milliseconds) used to exclude outliers from
            the plot.  ``None`` disables filtering.  Default is ``10``.
        save_fig : bool, optional
            When ``True``, save the figure to the campaign output directory.
            Default is ``True``.
        show_fig : bool, optional
            When ``True``, display the figure interactively.  Default is
            ``False``.
        """
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
        """Run the full QC pipeline then GARPOS modeling end-to-end.

        Parameters
        ----------
        site_metadata : Site or str or None, optional
            Unused; the session owns site metadata.  Retained for API
            compatibility.
        run_id : str or int, optional
            Label applied to the GARPOS inversion run.  Default is ``0``.
        iterations : int, optional
            Number of GARPOS inversion iterations to perform.  Default is ``1``.
        garpos_settings : dict or InversionParams or None, optional
            Custom GARPOS inversion parameters merged into the handler defaults.
            ``None`` uses defaults.
        garpos_override : bool, optional
            When ``True``, overwrite existing GARPOS inversion outputs.
            Default is ``False``.
        pre_process_config : QCPipelineConfig or None, optional
            Configuration applied to the QC pipeline run.  ``None`` uses the
            session default.
        """
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
