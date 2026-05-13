root_dir = "/Volumes/DunbarSSD/Project/SeafloorGeodesy/SFGMain3"
network = 'cascadia-gorda'
station = 'NCC1'
campaign = '2025_A_1126'
raw_source = "/Volumes/DunbarSSD/Project/SeafloorGeodesy/SFGMain/cascadia-gorda/NCC1/2025_A_1126/raw"
qc_source = "/Volumes/DunbarSSD/Project/SeafloorGeodesy/SFGMain/cascadia-gorda/NCC1/2025_A_1126/qc"


"""
1. Create a workflow handler for the root directory.
2. Use the handler to create a session for the desired network/station/campaign/sources
3. ingest raw data into the session (campaign_dir/raw)
4. ingest qc data into the session (campaign_dir/qc)
5. Run the sv3 pipeline on the session, make sure to decimate pride to 1hz
6. run the qc pipeline on the session.
7. Run garpos on the fullrate data
8. run garpos on the qc data.

Stop and debug when necessary. Look to the legacy repo when the solution is not obvious:
/Users/franklyndunbar/Project/SeaFloorGeodesy/es_sfgtools/packages/earthscope-sfg-workflows

"""
from pathlib import Path
from earthscope_sfg_workflows.workflows import WorkflowHandler
from earthscope_sfg_workflows.workflows.pipelines.config import (
    SV3PipelineConfig,
    QCPipelineConfig,
    RinexConfig,
    PrideConfig,
)

if __name__ == "__main__":
    # ---------------------------------------------------------------------------
    # 1. Create handler and activate session
    # ---------------------------------------------------------------------------
    handler = WorkflowHandler(directory=root_dir)
    handler.set_network_station_campaign(network, station, campaign)
    session = handler._session

    campaign_layout = session.campaign.layout
    print(f"Campaign directory: {campaign_layout.root}")
    print(f"  raw:  {campaign_layout.raw}")
    print(f"  qc:   {campaign_layout.qc}")
    print(f"  processed: {campaign_layout.processed}")

    # ---------------------------------------------------------------------------
    # 2. Ingest raw data (catalog files already in campaign_dir/raw)
    # ---------------------------------------------------------------------------
    print("\n--- Ingesting raw data ---")
    handler.ingest_add_local_data(Path(raw_source))

    # ---------------------------------------------------------------------------
    # 3. Ingest QC pin files from .tar.gz tarballs in campaign_dir/qc
    # ---------------------------------------------------------------------------
    print("\n--- Ingesting QC pin tarballs ---")
    handler.ingest_qcpin_tarballs(Path(qc_source))  # defaults to campaign_layout.qc

    # ---------------------------------------------------------------------------
    # 4. Run SV3 pipeline — decimate PRIDE output to 1 Hz (modulo_millis=1000)
    # ---------------------------------------------------------------------------
    print("\n--- Running SV3 pipeline (PRIDE decimated to 1 Hz) ---")
    sv3_config = SV3PipelineConfig(
        rinex_config=RinexConfig(modulo_millis=1000),
    )
    handler.preprocess_run_pipeline_sv3(job="all", primary_config=sv3_config)

    # ---------------------------------------------------------------------------
    # 5. Run QC pipeline
    # ---------------------------------------------------------------------------
    print("\n--- Running QC pipeline ---")
    handler.preprocess_run_pipeline_qc(job="all")

    # ---------------------------------------------------------------------------
    # 6. Run GARPOS on fullrate (SV3) data
    # ---------------------------------------------------------------------------
    print("\n--- Preparing GARPOS (fullrate) ---")
    handler.midprocess_prep_garpos()

    print("\n--- Running GARPOS (fullrate) ---")
    handler.modeling_run_garpos(run_id="fullrate", iterations=1)

    # ---------------------------------------------------------------------------
    # 7. Run GARPOS on QC data
    # ---------------------------------------------------------------------------
    print("\n--- Running GARPOS (QC) ---")
    handler.qc_process_and_model(run_id="qc", iterations=1)

    print("\nDone.")
