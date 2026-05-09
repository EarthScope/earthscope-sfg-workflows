import os
import re
import warnings
from pathlib import Path
from typing import (
    Callable,
    List,
    Literal,
)

from pride_ppp.specifications.cli import PrideCLIConfig

from earthscope_sfg_workflows.logging import ProcessLogger as logger
from earthscope_sfg_workflows.logging import change_all_logger_dirs
from earthscope_sfg_workflows.utils.model_update import validate_and_merge_config

from ..config.file_config import (
    DEFAULT_FILE_TYPES_TO_DOWNLOAD,
    DEFAULT_INTERMEDIATE_FILE_TYPES_TO_DOWNLOAD,
    REMOTE_TYPE,
    AssetType,
)
from ..data_mgmt.adapters.disk_filestore import S3FileStore
from ..data_mgmt.model import AssetEntry, AssetKind
from earthscope_sfg_tools.datamodels.metadata import Site
from ..modeling.garpos_tools.schemas import InversionParams
from .base import (
    WorkflowBase,
    validate_network_station,
    validate_network_station_campaign,
)
from .midprocess.mid_processing import IntermediateDataProcessor
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
from .pipelines.qc_pipeline import QCPipeline
from .pipelines.sv3_pipeline import SV3Pipeline
from .workspace import TileDBRegistry, Workspace, _build_default_workspace, _to_asset_kind

_SV3_JOBS: dict[str, Callable[["SV3Pipeline"], None]] = {
    "all":                lambda p: p.run_pipeline(),
    "intermediate":       lambda p: p.run_intermediate_pipeline(),
    "process_novatel":    lambda p: p.pre_process_novatel(),
    "build_rinex":        lambda p: p.get_rinex_files(),
    "run_pride":          lambda p: p.process_rinex(),
    "process_kinematic":  lambda p: p.process_kin(),
    "process_dfop00":     lambda p: p.process_dfop00(),
    "refine_shotdata":    lambda p: p.update_shotdata(),
    "process_svp":        lambda p: p.process_svp(),
}

_QC_JOBS: dict[str, Callable[["QCPipeline"], None]] = {
    "all":                lambda p: p.run_pipeline(),
    "process_qcpin":      lambda p: p.process_qcpin(),
    "build_rinex":        lambda p: p.get_rinex_files(),
    "run_pride":          lambda p: p.process_rinex(),
    "process_kinematic":  lambda p: p.process_kin(),
    "refine_shotdata":    lambda p: p.update_shotdata(),
}

# Keep legacy list names for any external callers that inspect them.
pipeline_jobs = list(_SV3_JOBS)
qc_pipeline_jobs = list(_QC_JOBS)


class WorkflowHandler(WorkflowBase):
    """Handles data operations including searching, adding, downloading, and processing.
    Owns a single :class:`Workspace`. All scope state lives on ``self.workspace``.
    """

    def __init__(
        self,
        directory: Path | str | None = None,
        s3_sync_bucket: str | None = None,
        *,
        workspace=None,
    ) -> None:
        """
        Args:
            directory: Root path of the data tree. Auto-detected from environment when omitted.
            s3_sync_bucket: S3 bucket name/URI for sync operations.
            workspace: Pre-constructed workspace. Preferred over ``directory``.
        """
        if workspace is None:
            if directory is None:
                directory = os.environ.get("MAIN_DIRECTORY", ".")
            workspace = _build_default_workspace(directory)

        super().__init__(workspace)
        self.s3_sync_bucket: str | None = s3_sync_bucket
        self.tiledb: TileDBRegistry | None = None
        self.workspace.bootstrap()

    # ------------------------------------------------------------------
    # Backwards-compat scope/metadata aliases (forward to workspace).
    # ------------------------------------------------------------------

    @property
    def current_network_name(self) -> str | None:
        return self.workspace.network_name

    @property
    def current_station_name(self) -> str | None:
        return self.workspace.station_name

    @property
    def current_campaign_name(self) -> str | None:
        return self.workspace.campaign_name

    @property
    def current_survey_name(self) -> str | None:
        return self.workspace.survey_name

    @property
    def current_station_metadata(self) -> Site | None:
        return self.workspace.site  # type: ignore[return-value]

    @current_station_metadata.setter
    def current_station_metadata(self, value: Site | None) -> None:
        if value is not None:
            self.workspace.load_site_metadata(value)
        else:
            self.workspace._site = None

    def set_network_station_campaign(
        self,
        network_id: str,
        station_id: str | None = None,
        campaign_id: str | None = None,
    ):
        """Sets the current network, and optionally station and campaign.

        Args:
            network_id: The ID of the network to set.
            station_id: The ID of the station to set. If None, only network context is set.
            campaign_id: The ID of the campaign to set. If None, campaign context is not set.
        """
        if network_id != self.workspace.network_name:
            self._set_network(network_id)
        if station_id is not None and station_id != self.workspace.station_name:
            self._set_station(station_id)
        if campaign_id is not None and campaign_id != self.workspace.campaign_name:
            self._set_campaign(campaign_id)

    # ------------------------------------------------------------------
    # Internal scope mutators (workspace + side effects)
    # ------------------------------------------------------------------

    def _set_network(self, network_id: str) -> None:
        self.workspace.set_network(network_id)
        self.workspace.bootstrap()
        self.workspace.ensure_network_dir(network_id)

    def _set_station(self, station_id: str) -> None:
        self.workspace.set_station(station_id)
        self.workspace.ensure_station_dir(
            self.workspace.network_name,  # type: ignore[arg-type]
            station_id,
        )
        self.workspace.try_load_site_metadata_from_disk()

    def _set_campaign(self, campaign_id: str) -> None:
        layout = self.workspace.activate_campaign(campaign_id)
        change_all_logger_dirs(layout.logs)
        os.environ["LOG_FILE_PATH"] = str(layout.logs)
        logger.info(
            f"Built directory structure for "
            f"{self.workspace.network_name} {self.workspace.station_name} {campaign_id}"
        )
        if isinstance(self.workspace.root, Path):
            self.tiledb = self.workspace.build_tiledb()

    @validate_network_station
    def list_campaign_directories(self) -> list[str]:
        """Lists the campaign directories available for the current station.
        Returns:
            A list of campaign directory names.

        Raises:
            ValueError: If station metadata is not loaded.

        Examples:
            >>> workflow = WorkflowHandler("/path/to/data"):
            >>> workflow.set_network_station_campaign("network", "station", "campaign"):
            >>> campaigns = workflow.list_campaign_directories():
            ['2022_A_1065','2023_A_1063','2025_A_1126']:
        """

        station_dir = (
            Path(self.workspace.root) / self.workspace.network_name / self.workspace.station_name
        )
        campaign_dirs: List[Path] = [
            x for x in station_dir.iterdir() if x.is_dir() and re.match(r"^\d{4}", x.name)
        ]
        return campaign_dirs

    @validate_network_station_campaign
    def ingest_add_local_data(self, directory_path: Path) -> None:
        """Scans a directory for data files and adds them to the catalog.
        Args:
            directory_path: The path to the directory to scan.
        """

        self.workspace.ingest_local(directory_path=directory_path)

    @validate_network_station_campaign
    def ingest_catalog_archive_data(self) -> None:
        """
        Updates the data catalog with the s3 uri's for data hosted in Earthscope's remote archive for the current network, station, and campaign.

        Notes:
            This method does not download any data files. It only updates the catalog with remote file paths. See `ingest_download_archive_data` to download files.:
        """
        self.workspace.discover_campaign()

    @validate_network_station_campaign
    def ingest_download_archive_data(
        self,
        file_types: list[AssetType] | list[str] | None = DEFAULT_FILE_TYPES_TO_DOWNLOAD,
        rinex_1Hz: bool = False,
    ) -> None:
        """
        Downloads data files from the Earthscope archive based on the current catalog entries.

        Notes:
            This method requires that the catalog has been populated with remote file paths using `ingest_catalog_archive_data`.:
        """
        self.download_data(file_types=file_types, rinex_1Hz=rinex_1Hz)

    @validate_network_station_campaign
    def ingest_download_intermediate_archive_data(
        self,
        file_types: (
            list[AssetType] | list[str] | None
        ) = DEFAULT_INTERMEDIATE_FILE_TYPES_TO_DOWNLOAD,
        rinex_1Hz: bool = False,
    ) -> None:
        """
        Downloads intermediate data files from the Earthscope archive based on the current catalog entries.

        Notes:
            This method requires that the catalog has been populated with remote file paths using `ingest_catalog_archive_data`.:
        """
        self.ingest_download_archive_data(file_types=file_types, rinex_1Hz=rinex_1Hz)

    @validate_network_station_campaign
    def preprocess_get_pipeline_sv3(
        self,
        primary_config: (
            SV3PipelineConfig
            | PrideCLIConfig
            | NovatelConfig
            | RinexConfig
            | DFOP00Config
            | PositionUpdateConfig
            | dict
            | None
        ) = None,
        secondary_config: (
            SV3PipelineConfig
            | PrideCLIConfig
            | NovatelConfig
            | RinexConfig
            | DFOP00Config
            | PositionUpdateConfig
            | dict
            | None
        ) = None,
    ) -> SV3Pipeline:
        """Creates and configures an SV3 processing pipeline.
        Args:
            primary_config: Optional primary configuration for the pipeline.
            secondary_config: Optional secondary configuration for the pipeline.

        Returns:
            Configured SV3Pipeline instance.

        Raises:
            AssertionError: If current network, station, or campaign is not set.
            ValueError: If configuration validation fails.

        See Also:
            earthscope_sfg_workflows.workflows.pipelines.config.SV3Pipeline:
        """

        base_config = SV3PipelineConfig()
        base_config_updated = base_config.model_copy()
        # Merge primary config if provided, overwriting defaults. Also check for misspelled keys
        if primary_config is not None:
            if isinstance(
                primary_config,
                (
                    SV3PipelineConfig,
                    PrideCLIConfig,
                    NovatelConfig,
                    RinexConfig,
                    DFOP00Config,
                    PositionUpdateConfig,
                ),
            ):
                primary_config = primary_config.model_dump()

            base_config_updated = validate_and_merge_config(
                base_class=base_config, override_config=primary_config
            )

        # Merge secondary config if provided, overwriting primary and defaults. Also check for misspelled keys
        if secondary_config is not None:
            if isinstance(
                secondary_config,
                (
                    SV3PipelineConfig,
                    PrideCLIConfig,
                    NovatelConfig,
                    RinexConfig,
                    DFOP00Config,
                    PositionUpdateConfig,
                ),
            ):
                secondary_config = secondary_config.model_dump()
            base_config_updated = validate_and_merge_config(
                base_class=base_config_updated, override_config=secondary_config
            )

        pipeline = SV3Pipeline(
            directory=self.directory,
            s3_sync_bucket=self.s3_sync_bucket,
            config=base_config_updated,
        )
        pipeline.set_network_station_campaign(
            network_id=self.current_network_name,
            station_id=self.current_station_name,
            campaign_id=self.current_campaign_name,
        )
        return pipeline

    @validate_network_station_campaign
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
        primary_config: (
            SV3PipelineConfig
            | PrideCLIConfig
            | NovatelConfig
            | RinexConfig
            | DFOP00Config
            | PositionUpdateConfig
            | dict
            | None
        ) = None,
        secondary_config: (
            SV3PipelineConfig
            | PrideCLIConfig
            | NovatelConfig
            | RinexConfig
            | DFOP00Config
            | PositionUpdateConfig
            | dict
            | None
        ) = None,
    ) -> None:
        """Runs the SV3 processing pipeline with optional configuration overrides.
        This method creates and configures an :class:`~earthscope_sfg_workflows.workflows.pipelines.config.SV3Pipeline`
        instance using the :attr:`workspace` to access the directory structure and catalog.

        Args:
            job: The specific job to run within the pipeline, by default "all".
            primary_config: Primary configuration to override defaults.
            secondary_config: Secondary configuration to override primary and defaults.

        Raises:
            AssertionError: If job is not in valid pipeline jobs.
            ValueError: If configuration validation fails.

        See Also:
            preprocess_get_pipeline_sv3:
            earthscope_sfg_workflows.workflows.pipelines.config.SV3Pipeline:
            earthscope_sfg_workflows.workflows.workflow_handler.WorkflowHandler:

        Examples:
            # Run the sv3 pipeline with custom Novatel processing configuration:
            >>> workflow = WorkflowHandler("/path/to/data"):
            >>> workflow.change_working_station("network", "station", "campaign"):
            >>> workflow.preprocess_run_pipeline_sv3(:
            ...     job="process_novatel",:
            ...     primary_config={"novatel_config":
            ... ):
        """
        assert job in _SV3_JOBS, f"Job must be one of {pipeline_jobs}"

        pipeline: SV3Pipeline = self.preprocess_get_pipeline_sv3(
            primary_config=primary_config, secondary_config=secondary_config
        )
        try:
            _SV3_JOBS[job](pipeline)
        except Exception as e:
            logger.error(f"SV3 job '{job}' failed: {e}")
            raise

    @validate_network_station_campaign
    def preprocess_get_pipeline_qc(
        self,
        primary_config: (
            QCPipelineConfig
            | PrideCLIConfig
            | RinexConfig
            | PositionUpdateConfig
            | QCPinConfig
            | dict
            | None
        ) = None,
        secondary_config: (
            QCPipelineConfig
            | PrideCLIConfig
            | RinexConfig
            | PositionUpdateConfig
            | QCPinConfig
            | dict
            | None
        ) = None,
    ) -> QCPipeline:
        """Creates and configures a QC processing pipeline.
        Args:
            primary_config: Optional primary configuration for the pipeline.
            secondary_config: Optional secondary configuration for the pipeline.

        Returns:
            Configured QCPipeline instance.

        Raises:
            AssertionError: If current network, station, or campaign is not set.
            ValueError: If configuration validation fails.

        See Also:
            earthscope_sfg_workflows.workflows.pipelines.qc_pipeline.QCPipeline:
        """
        base_config = QCPipelineConfig()
        base_config_updated = base_config.model_copy()

        # Merge primary config if provided
        if primary_config is not None:
            if isinstance(
                primary_config,
                (
                    QCPipelineConfig,
                    PrideCLIConfig,
                    RinexConfig,
                    PositionUpdateConfig,
                    QCPinConfig,
                ),
            ):
                primary_config = primary_config.model_dump()

            base_config_updated = validate_and_merge_config(
                base_class=base_config, override_config=primary_config
            )

        # Merge secondary config if provided
        if secondary_config is not None:
            if isinstance(
                secondary_config,
                (
                    QCPipelineConfig,
                    PrideCLIConfig,
                    RinexConfig,
                    PositionUpdateConfig,
                    QCPinConfig,
                ),
            ):
                secondary_config = secondary_config.model_dump()
            base_config_updated = validate_and_merge_config(
                base_class=base_config_updated, override_config=secondary_config
            )

        pipeline = QCPipeline(
            directory=self.directory,
            s3_sync_bucket=self.s3_sync_bucket,
            config=base_config_updated,
        )
        pipeline.set_network_station_campaign(
            network_id=self.current_network_name,
            station_id=self.current_station_name,
            campaign_id=self.current_campaign_name,
        )
        return pipeline

    @validate_network_station_campaign
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
        primary_config: (
            QCPipelineConfig
            | PrideCLIConfig
            | RinexConfig
            | PositionUpdateConfig
            | QCPinConfig
            | dict
            | None
        ) = None,
        secondary_config: (
            QCPipelineConfig
            | PrideCLIConfig
            | RinexConfig
            | PositionUpdateConfig
            | QCPinConfig
            | dict
            | None
        ) = None,
    ) -> None:
        """Runs the QC processing pipeline with optional configuration overrides.
        This method creates and configures a :class:`~earthscope_sfg_workflows.workflows.pipelines.qc_pipeline.QCPipeline`
        instance for processing QC PIN data from Sonardyne equipment.

        Args:
            job: The specific job to run within the pipeline, by default "all".
            primary_config: Primary configuration to override defaults.
            secondary_config: Secondary configuration to override primary and defaults.

        Raises:
            AssertionError: If job is not in valid QC pipeline jobs.
            ValueError: If configuration validation fails.

        See Also:
            preprocess_get_pipeline_qc:
            earthscope_sfg_workflows.workflows.pipelines.qc_pipeline.QCPipeline:

        Examples:
            # Run the QC pipeline with custom configuration:
            >>> workflow = WorkflowHandler("/path/to/data"):
            >>> workflow.set_network_station_campaign("network", "station", "campaign"):
            >>> workflow.preprocess_run_pipeline_qc(:
            ...     job="process_qcpin",:
            ...     primary_config={"qcpin_config":
            ... ):
        """
        assert job in _QC_JOBS, f"Job must be one of {qc_pipeline_jobs}"

        pipeline: QCPipeline = self.preprocess_get_pipeline_qc(
            primary_config=primary_config, secondary_config=secondary_config
        )
        try:
            _QC_JOBS[job](pipeline)
        except Exception as e:
            logger.error(f"QC job '{job}' failed: {e}")
            raise

    @validate_network_station
    def midprocess_get_sitemeta(self, site_metadata: Site | str | None = None) -> Site:
        """Loads and returns the site metadata for the current station. Sets the current_station_metadata attribute.
        1. If site_metadata is None, attempts to load from workspace metadata.
        2. If site_metadata is a string or Path, loads the site metadata from the file.
        3. If site_metadata is already a Site instance, uses it directly.

        Args:
            site_metadata: Optional site metadata or path to metadata file. If not provided, it will be loaded if available.

        Returns:
            The site metadata.

        Raises:
            ValueError: If site metadata cannot be loaded or is not provided.
        """
        if site_metadata is None:
            if self.workspace.site is not None:
                site_metadata = self.workspace.site
            else:
                site_metadata: Site | None = self.workspace.load_or_fetch_site_metadata(
                    station_id=self.workspace.station_name
                )

        elif isinstance(site_metadata, (str, Path)):
            site_metadata = Site.from_json(site_metadata)

        else:
            assert isinstance(site_metadata, Site), (
                "site_metadata must be of type Site if not a str or Path"
            )

        if site_metadata is None:
            raise ValueError("Site metadata not loaded or provided, cannot proceed")

        self.current_station_metadata = site_metadata
        return self.current_station_metadata

    @validate_network_station
    def midprocess_get_processor(
        self,
        site_metadata: Site | str | None = None,
        override_metadata_require: bool = False,
    ) -> IntermediateDataProcessor:
        """Returns an instance of the IntermediateDataProcessor for the current station.
        Args:
            site_metadata: Optional site metadata or path to metadata file. If not provided, it will be loaded if available.
            override_metadata_require: If True, bypasses the requirement for loaded site metadata, by default False.

        Returns:
            An instance of IntermediateDataProcessor.

        Raises:
            ValueError: If site metadata is not loaded and override_metadata_require is False.
        """
        if not override_metadata_require:
            # Ensure site metadata is loaded
            self.midprocess_get_sitemeta(site_metadata=site_metadata)

            if self.current_station_metadata is None:
                raise ValueError(
                    "Station metadata must be loaded before initializing IntermediateDataProcessor."
                )
        dataPostProcessor = IntermediateDataProcessor(
            station_metadata=self.current_station_metadata,
            directory=self.directory,
            s3_sync_bucket=self.s3_sync_bucket,
        )
        dataPostProcessor.mid_process_workflow = not override_metadata_require
        dataPostProcessor.set_network(network_id=self.current_network_name)
        dataPostProcessor.set_station(station_id=self.current_station_name)
        if self.current_campaign_name is not None:
            dataPostProcessor.set_campaign(campaign_id=self.current_campaign_name)

        return dataPostProcessor

    @validate_network_station_campaign
    def midprocess_parse_surveys(
        self,
        site_metadata: Site | str | None = None,
        override: bool = False,
        write_intermediate: bool = False,
        survey_id: str | None = None,
    ) -> IntermediateDataProcessor:
        """Parses survey data for the current station.
        Args:
            site_metadata: Optional site metadata or path to metadata file. If not provided, it will be loaded if available.
            override: If True, re-parses existing data, by default False.
            write_intermediate: If True, writes intermediate files to disk, by default False.
            survey_id: Optional survey identifier to process. If None, processes all surveys, by default None.

        Raises:
            ValueError: If site metadata is not loaded.
        """
        if self.s3_sync_bucket is not None:
            self.sync_from_s3(overwrite=override)

        dataPostProcessor: IntermediateDataProcessor = self.midprocess_get_processor(
            site_metadata=site_metadata
        )
        dataPostProcessor.parse_surveys(
            survey_id=survey_id,
            override=override,
            write_intermediate=write_intermediate,
        )
        return dataPostProcessor

    @validate_network_station_campaign
    def midprocess_prep_garpos(
        self,
        site_metadata: Site | str | None = None,
        survey_id: str | None = None,
        custom_filters: dict | None = None,
        override: bool = False,
        override_survey_parsing: bool = False,
        write_intermediate: bool = False,
    ) -> None:
        """Prepares data for GARPOS processing.
        Args:
            site_metadata: Optional site metadata or path to metadata file. If not provided, it will be loaded if available.
            survey_id: Optional survey identifier to process. If None, processes all surveys, by default None.
            custom_filters: Custom filter settings for shot data preparation, by default None.
            override: If True, re-prepares existing data, by default False.
            write_intermediate: If True, writes intermediate files, by default False.

        Raises:
            ValueError: If site metadata is not loaded.
        """
        dataPostProcessor: IntermediateDataProcessor = self.midprocess_parse_surveys(
            site_metadata=site_metadata,
            override=override_survey_parsing,
            write_intermediate=write_intermediate,
            survey_id=survey_id,
        )
        dataPostProcessor.prepare_shotdata_garpos(
            survey_id=survey_id,
            custom_filters=custom_filters,
            overwrite=override,
        )

    @validate_network_station
    def midprocess_sync_station_data_s3(
        self, overwrite: bool = False, override_metadata_require: bool = True
    ) -> None:
        """Uploads intermediate processed data to S3 for the current station.
        Args:
            overwrite: If True, overwrites existing data on S3, by default False.
            override_metadata_require: If True, bypasses the requirement for loaded site metadata, by default False.

        Raises:
            ValueError: If site metadata is not loaded and ``override_metadata_require`` is False.
        """
        dataPostProcessor: IntermediateDataProcessor = self.midprocess_get_processor(
            self.current_station_metadata,
            override_metadata_require=override_metadata_require,
        )
        dataPostProcessor.midprocess_sync_station_data_s3(overwrite=overwrite)

    @validate_network_station_campaign
    def midprocess_sync_campaign_data_s3(
        self, overwrite: bool = False, override_metadata_require: bool = True
    ) -> None:
        """Uploads intermediate processed data to S3 for the current campaign.
        Args:
            overwrite: If True, overwrites existing data on S3, by default False.
            override_metadata_require: If True, bypasses the requirement for loaded site metadata, by default False.

        Raises:
            ValueError: If site metadata is not loaded and ``override_metadata_require`` is False.
        """
        dataPostProcessor: IntermediateDataProcessor = self.midprocess_get_processor(
            self.current_station_metadata,
            override_metadata_require=override_metadata_require,
        )
        dataPostProcessor.midprocess_sync_campaign_data_s3(overwrite=overwrite)

    @validate_network_station_campaign
    def modeling_get_garpos_handler(self) -> GarposHandler:
        """Returns an instance of the GarposHandler for the current station.
        Returns:
            An instance of GarposHandler.

        Raises:
            ValueError: If site metadata is not loaded.
        """
        if self.current_station_metadata is None:
            raise ValueError("Site metadata not loaded, cannot get GarposHandler")

        gp_handler = GarposHandler(
            directory=self.directory,
            station_metadata=self.current_station_metadata,
            s3_sync_bucket=self.s3_sync_bucket,
        )
        gp_handler.set_network_station_campaign(
            network_id=self.current_network_name,
            station_id=self.current_station_name,
            campaign_id=self.current_campaign_name,
        )
        return gp_handler

    @validate_network_station_campaign
    def modeling_run_garpos(
        self,
        survey_id: str | None = None,
        run_id: str = "Test",
        iterations: int = 1,
        override: bool = False,
        custom_settings: dict | None = None,
    ) -> None:
        """Runs GARPOS processing for the current station.
        Args:
            survey_id: Optional survey identifier to process. If None, processes all surveys, by default None.
            run_id: Identifier for the GARPOS run.
            iterations: Number of GARPOS iterations to perform, by default 1.
            site_metadata: Optional site metadata or path to metadata file. If not provided, it will be loaded if available.
            override: If True, re-runs GARPOS even if results exist, by default False.
            custom_settings: Custom settings to override GARPOS defaults, by default None.

        Raises:
            ValueError: If site metadata is not loaded.
        """
        gp_handler = self.modeling_get_garpos_handler()
        gp_handler.run_garpos(
            survey_id=survey_id,
            run_id=run_id,
            iterations=iterations,
            override=override,
            custom_settings=custom_settings,
        )

    @validate_network_station_campaign
    def modeling_plot_shotdata_replies_per_transponder(
        self,
        survey_id: str | None = None,
        save_fig: bool = True,
        show_fig: bool = False,
    ) -> None:
        """Plots the shot data replies per transponder for a given survey.
        Args:
            survey_id: ID of the survey to plot results for, by default None.
            save_fig: If True, save the figure, by default True.
            show_fig: If True, display the figure, by default False.
        """
        gp_handler = self.modeling_get_garpos_handler()
        gp_handler.plot_shotdata_replies_per_transponder(
            savefig=save_fig,
            showfig=show_fig,
        )

    @validate_network_station_campaign
    def modeling_plot_flagged_residuals(
        self,
        survey_id: str | None = None,
        run_id: str = "Test",
        save_fig: bool = True,
        show_fig: bool = False,
    ) -> None:
        """Plots the flagged residuals for a given survey.
        Args:
            survey_id: ID of the survey to plot results for, by default None.
            run_id: The run ID of the survey results to plot, by default 0.
            save_fig: If True, save the figure, by default True.
            show_fig: If True, display the figure, by default False.
        """
        gp_handler = self.modeling_get_garpos_handler()
        gp_handler.plot_residuals_per_transponder_before_and_after(
            survey_id=survey_id,
            run_id=run_id,
            savefig=save_fig,
            showfig=show_fig,
        )

    @validate_network_station_campaign
    def modeling_plot_garpos_residuals(
        self,
        survey_id: str | None = None,
        run_id: str = "Test",
        subplots: bool = True,
        save_fig: bool = True,
        show_fig: bool = False,
    ) -> None:
        """Plots the time series results for a given survey.
        Args:
            survey_id: ID of the survey to plot results for, by default None.
            run_id: The run ID of the survey results to plot, by default 0.
            res_filter: The residual filter value to filter outrageous values (m), by default 10.
            save_fig: If True, save the figure, by default True.
            show_fig: If True, display the figure, by default False.
        """
        gp_handler = self.modeling_get_garpos_handler()
        gp_handler.plot_remaining_residuals_per_transponder(
            survey_id=survey_id,
            run_id=run_id,
            subplots=subplots,
            savefig=save_fig,
            showfig=show_fig,
        )

    @validate_network_station_campaign
    def qc_get_pipeline(self, config: QCPipelineConfig = None) -> "QCPipeline":
        """Get a configured QCPipeline instance.
        Args:
            config: A QCPipelineConfig instance with configuration options, by default None.

        Returns:
            A configured QCPipeline instance.
        """

        qc_pipeline: QCPipeline = QCPipeline(
            directory=self.directory,
            s3_sync_bucket=self.s3_sync_bucket,
            config=config,
        )
        qc_pipeline.set_network_station_campaign(
            network_id=self.current_network_name,
            station_id=self.current_station_name,
            campaign_id=self.current_campaign_name,
        )
        return qc_pipeline

    @validate_network_station_campaign
    def qc_process_and_model(
        self,
        site_metadata: Site | str | None = None,
        run_id: str | int = 0,
        iterations: int = 1,
        garpos_settings: dict | InversionParams | None = None,
        garpos_override: bool = False,
        pre_process_config: QCPipelineConfig = None,
    ) -> None:
        """Process QC files and run GARPOS modeling.
        Args:
            site_metadata: Optional site metadata or path to metadata file. If not provided, it will be loaded if available.
            run_id: Identifier for the GARPOS run, by default 0.
            iterations: Number of GARPOS iterations to perform, by default 1.
            garpos_settings: Custom settings to override GARPOS defaults, by default None.
            garpos_override: If True, re-runs GARPOS even if results exist, by default False.
            pre_process_config: A QCPipelineConfig instance with configuration options, by default None.

        Raises:
            ValueError: If site metadata is not provided and cannot be loaded.
        """
        # Get and run the QC pipeline
        qc_pipeline: QCPipeline = self.qc_get_pipeline(config=pre_process_config)
        qc_pipeline.run_pipeline()

        # Get the intermediate data processor and parse QC surveys
        try:
            qc_mid_processor = self.midprocess_get_processor(site_metadata=site_metadata)
        except ValueError as e:
            raise e  # for visibility

        gp_dir_list = qc_mid_processor.parse_surveys_qc(
            override=False, shotdata_uri=qc_pipeline.qcShotDataFinalTDB.uri
        )

        # Get the GARPOS handler and run GARPOS
        qc_garpos_handler = self.modeling_get_garpos_handler()
        qc_garpos_handler.run_garpos(
            surveys=gp_dir_list,
            run_id=run_id,
            iterations=iterations,
            override=garpos_override,
            custom_settings=garpos_settings,
        )
        for garpos_layout in gp_dir_list:
            qc_garpos_handler._plot_ts_results(
                survey_id=garpos_layout.root.parent.name,
                run_id=run_id,
                res_filter=10,
                savefig=True,
                showfig=False,
                results_dir=garpos_layout.results,
            )

    @validate_network_station_campaign
    def modeling_plot_garpos_results(
        self,
        survey_id: str | None = None,
        run_id: str = "Test",
        residuals_filter: float | None = 10,
        save_fig: bool = True,
        show_fig: bool = False,
    ) -> None:
        """Plots the time series results for a given survey.
        Args:
            survey_id: ID of the survey to plot results for, by default None.
            run_id: The run ID of the survey results to plot, by default 0.
            res_filter: The residual filter value to filter outrageous values (m), by default 10.
            save_fig: If True, save the figure, by default True.
            show_fig: If True, display the figure, by default False.
        """
        gp_handler = self.modeling_get_garpos_handler()
        gp_handler.plot_ts_results(
            survey_id=survey_id,
            run_id=run_id,
            res_filter=residuals_filter,
            savefig=save_fig,
            showfig=show_fig,
        )

    # ------------------------------------------------------------------
    # Data catalog helpers (moved from DataHandler)
    # ------------------------------------------------------------------

    @validate_network_station_campaign
    def add_data_to_catalog(self, local_filepaths: list[Path]) -> None:
        """Catalog an explicit list of local files. Symlinks them to ``raw/`` if needed."""
        campaign = self.workspace.ensure_campaign()

        added = 0
        scope = self.workspace.scope
        for file_path in local_filepaths:
            file_path = Path(file_path)
            if not file_path.exists():
                logger.error(f"File {file_path} does not exist")
                continue
            kind = self.workspace._detector.detect(file_path.name)
            if kind is None:
                continue
            if file_path.parent != campaign.raw:
                symlinked_path = campaign.raw / file_path.name
                if symlinked_path != file_path:
                    try:
                        file_path.symlink_to(symlinked_path, target_is_directory=False)
                    except FileExistsError:
                        pass
            entry = AssetEntry(kind=kind, scope=scope, local_path=file_path)
            if self.workspace.add_or_update_asset(entry) is not None:
                added += 1

        logger.info(f"Added {added} out of {len(local_filepaths)} files to the catalog")

    @validate_network_station_campaign
    def add_data_remote(
        self,
        remote_filepaths: list[str],
        remote_type: REMOTE_TYPE | str = REMOTE_TYPE.HTTP,
    ) -> None:
        """Catalog remote URLs after type detection. Skips already-downloaded entries."""
        if isinstance(remote_type, str):
            try:
                remote_type = REMOTE_TYPE(remote_type)
            except Exception as e:
                raise ValueError(
                    f"Remote type {remote_type} must be one of {REMOTE_TYPE.__members__.keys()}"
                ) from e

        scope = self.workspace.scope
        not_recognized = 0
        already_local = 0
        added = 0

        for url in remote_filepaths:
            kind = self.workspace._detector.detect(Path(url).name)
            if kind is None:
                logger.debug(f"File type not recognized for {url}")
                not_recognized += 1
                continue
            if self.workspace.remote_file_exists_locally(kind, url):
                already_local += 1
                continue
            entry = AssetEntry(
                kind=kind,
                scope=scope,
                remote_path=url,
                remote_type=remote_type.value,
            )
            if self.workspace.add_or_update_asset(entry) is not None:
                added += 1

        logger.info(f"{not_recognized} files not recognized and skipped")
        logger.info(f"{already_local} files already exist in the catalog")
        logger.info(f"Added {added} out of {len(remote_filepaths)} files to the catalog")

    def download_data(
        self,
        file_types: list[AssetType] | list[str] | str = DEFAULT_FILE_TYPES_TO_DOWNLOAD,
        override: bool = False,
        rinex_1Hz: bool = False,
    ) -> None:
        """Download cataloged remote files of the given types."""
        if not isinstance(file_types, list):
            file_types = [file_types]

        kinds: list[AssetKind] = []
        seen: set[AssetKind] = set()
        for ft in file_types:
            if isinstance(ft, str):
                try:
                    ft = AssetType(ft.lower())
                except Exception as e:
                    raise ValueError(
                        f"File type {ft!r} must be one of {AssetType.__members__.keys()}"
                    ) from e
            kind = _to_asset_kind(ft)
            if kind not in seen:
                seen.add(kind)
                kinds.append(kind)

        report = self.workspace.download_assets(kinds=kinds, override=override, rinex_1hz=rinex_1Hz)
        logger.info(f"Downloaded {report.downloaded} files (skipped {report.skipped})")
        for err in report.errors:
            logger.error(err)

    def get_site_metadata(
        self,
        site_metadata: "Site | Path | str | None" = None,
    ) -> "Site | None":
        """Load or persist site metadata for the active station."""
        site = self.workspace.load_or_fetch_site_metadata(explicit=site_metadata)
        if site is not None:
            logger.info(
                f"Site metadata loaded for "
                f"{self.workspace.network_name} {self.workspace.station_name}"
            )
        else:
            msg = (
                f"No site metadata found for "
                f"{self.workspace.network_name} {self.workspace.station_name}. "
                "Some functionality may be limited."
            )
            warnings.warn(msg)
            logger.warning(msg)
        return site

    def sync_from_s3(self, overwrite: bool = False) -> None:
        """Mirror seafloor-geodesy data from a remote S3 prefix into the local workspace."""
        if self.s3_sync_bucket is None:
            raise RuntimeError("sync_from_s3 requires s3_sync_bucket to be configured.")
        if self.workspace.network_name is None or self.workspace.station_name is None:
            raise ValueError("sync_from_s3 requires network and station to be set.")

        s3_root = self.s3_sync_bucket
        if not s3_root.startswith("s3://"):
            s3_root = f"s3://{s3_root}"

        s3_files = S3FileStore()
        local_files = self.workspace._files

        s3_station = Path(s3_root) / self.workspace.network_name / self.workspace.station_name
        if not s3_files.is_dir(s3_station):
            logger.error(f"S3 station path not found: {s3_station}")
            return

        for info in s3_files.list_files(s3_station, recursive=True):
            if not info.is_file:
                continue
            relative = Path(str(info.path)).relative_to(s3_station)
            local_dest = (
                self.workspace.root
                / self.workspace.network_name
                / self.workspace.station_name
                / relative
            )
            if local_dest.exists() and not overwrite:
                continue
            try:
                local_dest.parent.mkdir(parents=True, exist_ok=True)
                local_files.write_bytes(local_dest, s3_files.read_bytes(info.path))
            except Exception as e:
                logger.error(f"Failed to download {info.path} to {local_dest}: {e}")
