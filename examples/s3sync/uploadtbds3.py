"""
Seafloor Geodesy Data Processing Demo - Upload TileDB Arrays to S3

This demo shows the steps to sync TileDB arrays with preprocessed data to an S3 bucket.

"""
# =============================================================================
# CONFIGURATION
# NOTE: Ensure you have AWS credentials set via environment variables:
# "AWS_ACCESS_KEY_ID","AWS_SECRET_ACCESS_KEY","AWS_SESSION_TOKEN"
# or via 'aws sso login'
# =============================================================================

import os
from pathlib import Path
from typing import List
from earthscope_sfg_workflows.config.env_config import Environment
from earthscope_sfg_workflows.workflows.workflow_handler import WorkflowHandler

HOME_DIR = "/Volumes/DunbarSSD/Project/SeafloorGeodesy/SFGMain2"

DEFAULT_CONFIG = {
    "S3_SYNC_BUCKET": "seafloor-public-bucket-bucket83908e77-gprctmuztrim",
}

NETWORK = "cascadia-gorda"
STATIONS = ["NCL1", "NDP1"]

if __name__ == "__main__":
    for key, value in DEFAULT_CONFIG.items():
        os.environ[key] = value

    Environment.load_working_environment()

    workflow = WorkflowHandler(HOME_DIR)

    for station in STATIONS:
        workflow.set_network_station_campaign(
            network_id=NETWORK,
            station_id=station,
            campaign_id=None)
        print(f"Syncing station {station} data to S3")
        workflow.midprocess_sync_station_data_s3(overwrite=False)

        campaigns: List[Path] = workflow.list_campaign_directories()
        for campaign in campaigns:
            print(f"Syncing campaign {campaign.name} data to S3...")
            workflow.set_network_station_campaign(
                network_id=NETWORK,
                station_id=station,
                campaign_id=campaign.name)
            workflow.midprocess_sync_campaign_data_s3(overwrite=False)
        print(f"Finished syncing station {station} data to S3\n")
