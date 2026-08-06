import os
from pathlib import Path
from earthscope_sfg_workflows.workflows.workflow_handler import WorkflowHandler


def main():
    main_dir = Path.home() / "path" / "to" / "your" / "SFGMain"

    workflow = WorkflowHandler(main_dir)

    global_config = {
        "dfop00_config": {"override": True},
        "novatel_config": {"n_processes": 5, "override": False},
        "position_update_config": {"override": True, "lengthscale": 0.1, "plot": False},
        "pride_config": {
            "cli": {
                "cutoff_elevation": 7,
                "frequency": ["G12", "R12", "E15", "C26", "J12"],
                "high_ion": None,
                "interval": None,
                "loose_edit": True,
                "sample_frequency": 1,
                "system": "GREC23J",
                "tides": "SOP",
            },
            "override_products_download": False,
            "override": True,
        },
        "rinex_config": {"n_processes": 5, "time_interval": 24, "override": False},
    }

    NETWORK = "cascadia-gorda"
    CAMPAIGN = "2025_A_1126"
    STATIONS = ["NTH1"]  # , "NCC1", "NBR1", "GCC1"]

    for station in STATIONS:
        workflow.set_network_station_campaign(
            network_id=NETWORK,
            station_id=station,
            campaign_id=CAMPAIGN,
        )
        workflow.ingest_discover_archive()
        workflow.download_data()
        workflow.preprocess_run_pipeline_sv3(
            job="all",
            primary_config=global_config
        )


if __name__ == "__main__":
    main()
