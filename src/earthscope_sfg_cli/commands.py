"""
This module contains the core logic for executing pipeline commands.

It orchestrates the data handling and processing workflows based on the
parsed manifest file.
"""


def run_manifest(manifest_object):
    """
    Executes a series of data ingestion, download, and processing jobs
    based on the provided PipelineManifest object.

    Parameters
    ----------
    manifest_object
        An object containing details about all jobs and
        the main directory for data handling.

    Raises
    ------
    AssertionError
        If a directory listed in an ingestion job does not exist.
    """
    from earthscope_sfg_workflows.config.env_config import Environment
    from earthscope_sfg_workflows.utils.model_update import validate_and_merge_config
    from earthscope_sfg_workflows.modeling.garpos_tools.load_utils import get_lib_paths
    from earthscope_sfg_workflows.workflows.workflow_handler import WorkflowHandler

    from .manifest import GARPOSConfig
    from .utils import display_pipelinemanifest

    # Load environment early so GEOLAB directory / S3 config is available
    # before WorkflowHandler resolves paths.
    Environment.load_working_environment()

    display_pipelinemanifest(manifest_object)
    get_lib_paths()
    wfh = WorkflowHandler(directory=manifest_object.main_directory)

    for ingest_job in manifest_object.ingestion_jobs:
        wfh.set_network_station_campaign(
            network_id=ingest_job.network,
            station_id=ingest_job.station,
            campaign_id=ingest_job.campaign,
        )
        assert ingest_job.directory.exists(), "Directory listed does not exist"
        wfh.ingest_add_local_data(ingest_job.directory)

    for job in manifest_object.download_jobs:
        wfh.set_network_station_campaign(
            network_id=job.network,
            station_id=job.station,
            campaign_id=job.campaign,
        )
        report = wfh.ingest_discover_archive()
        if report.cataloged == 0:
            print(f"No Remote Assets Found For {job.model_dump()}")
        else:
            wfh.download_data()

    for job in manifest_object.process_jobs:
        wfh.set_network_station_campaign(
            network_id=job.network, station_id=job.station, campaign_id=job.campaign
        )
        wfh.preprocess_run_pipeline_sv3(
            job=job.job_type,
            primary_config=job.global_config,
            secondary_config=job.secondary_config,
        )

    for job in manifest_object.garpos_jobs:
        config: GARPOSConfig = validate_and_merge_config(
            base_class=job.global_config,
            override_config=job.secondary_config,
        )
        wfh.set_network_station_campaign(
            network_id=job.network, station_id=job.station, campaign_id=job.campaign
        )
        wfh.midprocess_prep_garpos(
            custom_filters=(config.filter_config.model_dump() if config.filter_config else None),
            override_garpos_prep=config.override,
            override_survey_parsing=False,
            survey_id=None,
            write_intermediate=False,
        )

        surveys = job.surveys if job.surveys else [None]

        for survey_id in surveys:
            wfh.modeling_run_garpos(
                iterations=config.iterations,
                run_id=config.run_id,
                override=config.override,
                survey_id=survey_id,
                custom_settings=config.inversion_params,
            )



