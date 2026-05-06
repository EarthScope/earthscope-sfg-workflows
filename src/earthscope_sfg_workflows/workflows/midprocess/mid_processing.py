"""IntermediateDataProcessor — mid-process workflow class.
Migrated to :class:`WorkflowBase` + :class:`Workspace` (RFC-A Phase 4).
"""

from __future__ import annotations

import datetime
import json
import shutil
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import numpy as np
import pandas as pd
from cloudpathlib import CloudPath

from earthscope_sfg_tools.datamodels.metadata import Campaign, Site, Survey
from earthscope_sfg_tools.rinex_tools import crinex_compress
from earthscope_sfg_tools.tiledb_integration import (
    TDBIMUPositionArray,
    TDBKinPositionArray,
    TDBShotDataArray,
)

from earthscope_sfg_workflows.logging import GarposLogger as logger
from earthscope_sfg_workflows.utils.model_update import validate_and_merge_config

from ...config.env_config import Environment
from ...config.loadconfigs import (
    GarposSiteConfig,
    get_garpos_site_config,
    get_survey_filter_config,
)
from ...data_mgmt.model import GARPOSLayout
from ...modeling.garpos_tools.data_prep import (
    GP_Transponders_from_benchmarks,
    apply_survey_config,
    get_array_dpos_center,
    prepare_garpos_input_from_survey,
    prepare_shotdata_for_garpos,
)
from ...modeling.garpos_tools.functions import CoordTransformer
from ...modeling.garpos_tools.schemas import GarposFixed, GarposInput
from ...prefiltering import filter_shotdata
from ..base import (
    WorkflowBase,
    validate_network_station,
    validate_network_station_campaign,
)
from ..preprocess_ingest.data_handler import _build_default_workspace
from ..workspace import Workspace


class IntermediateDataProcessor(WorkflowBase):
    """Mid-process workflow class. Parses surveys, filters shotdata, and
    writes GARPOS input files.

    Composes a :class:`Workspace` and reaches the data layer only via
    ``self.workspace.{layout,metadata,assets,ingest}``.
    """

    mid_process_workflow: bool = True

    def __init__(
        self,
        station_metadata: Site | None = None,
        directory: Path | str | None = None,
        s3_sync_bucket: str | None = None,
        *,
        workspace: Workspace | None = None,
    ) -> None:
        if workspace is None:
            import os

            if directory is None:
                directory = os.environ.get("MAIN_DIRECTORY", ".")
            workspace = _build_default_workspace(directory)

        super().__init__(workspace)
        self.s3_sync_bucket: str | None = s3_sync_bucket
        if station_metadata is not None:
            self.workspace.load_site_metadata(station_metadata)

        if self.workspace.metadata.site is not None:
            site = self.workspace.metadata.site
            self.coordTransformer = CoordTransformer(
                latitude=site.arrayCenter.latitude,
                longitude=site.arrayCenter.longitude,
                elevation=-float(site.localGeoidHeight),
            )

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
        return self.workspace.metadata.site  # type: ignore[return-value]

    @current_station_metadata.setter
    def current_station_metadata(self, value: Site | None) -> None:
        if value is not None:
            self.workspace.load_site_metadata(value)
            self.coordTransformer = CoordTransformer(
                latitude=value.arrayCenter.latitude,
                longitude=value.arrayCenter.longitude,
                elevation=-float(value.localGeoidHeight),
            )
        else:
            self.workspace._site = None

    @property
    def current_campaign_metadata(self) -> Campaign | None:
        return self.workspace.metadata.campaign  # type: ignore[return-value]

    # ------------------------------------------------------------------
    # Scope mutators
    # ------------------------------------------------------------------

    def set_network(self, network_id: str) -> None:
        self.workspace.set_network(network_id)
        self.workspace.bootstrap()
        self.workspace.ensure_network_dir(network_id)

    def set_station(self, station_id: str) -> None:
        self.workspace.set_station(station_id)
        self.workspace.ensure_station_dir(
            self.workspace.network_name,  # type: ignore[arg-type]
            station_id,
        )
        self.workspace.try_load_site_metadata_from_disk()
        if self.mid_process_workflow:
            assert self.workspace.metadata.site is not None, (
                f"Site metadata file not found for station {station_id}; "
                "cannot proceed with mid-process workflow."
            )
            site = self.workspace.metadata.site
            self.coordTransformer = CoordTransformer(
                latitude=site.arrayCenter.latitude,
                longitude=site.arrayCenter.longitude,
                elevation=-float(site.localGeoidHeight),
            )

    def set_campaign(self, campaign_id: str) -> None:
        if self.mid_process_workflow and self.workspace.metadata.site is not None:
            self.workspace.select_campaign_from_metadata(campaign_id)
        else:
            self.workspace.set_campaign(campaign_id)
        self.workspace.layout.ensure_campaign()

    def set_survey(self, survey_id: str) -> None:
        if self.mid_process_workflow and self.workspace.metadata.campaign is not None:
            self.workspace.select_survey_from_metadata(survey_id)
        else:
            self.workspace.set_survey(survey_id)
        # Materialize survey directory.
        survey_dir = self.workspace.layout.survey
        self.workspace._files.mkdir(survey_dir)

    # ------------------------------------------------------------------
    # Survey parsing
    # ------------------------------------------------------------------

    @validate_network_station_campaign
    def parse_surveys(
        self,
        survey_id: str | None = None,
        override: bool = False,
        write_intermediate: bool = False,
    ) -> None:
        """Parse surveys for the active campaign and write CSVs into survey dirs."""
        campaign_meta = self.workspace.metadata.campaign
        if campaign_meta is None:
            raise ValueError("Campaign metadata must be loaded before parse_surveys")

        tiledb = self.workspace.layout.tiledb()
        campaign = self.workspace.layout.ensure_campaign()

        shotDataTDB = TDBShotDataArray(tiledb.shotdata)

        with open(campaign.metadata_file, "w") as f:
            json.dump(campaign_meta.model_dump(mode="json"), f, indent=4)

        surveys_to_process: list[Survey] = [
            s for s in campaign_meta.surveys if survey_id is None or survey_id == s.id
        ]
        if not surveys_to_process:
            raise ValueError(f"Survey {survey_id} not found in campaign {campaign_meta.name}.")

        for survey in surveys_to_process:
            self.set_survey(survey_id=survey.id)
            survey_root = self.workspace.layout.survey

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

            with open(self.workspace.layout.survey_metadata_file, "w") as f:
                json.dump(survey.model_dump(mode="json"), f, indent=4)

    # ------------------------------------------------------------------
    # GARPOS preparation
    # ------------------------------------------------------------------

    @validate_network_station_campaign
    def prepare_shotdata_garpos(
        self,
        campaign_id: str | None = None,
        survey_id: str | None = None,
        custom_filters: dict | None = None,
        overwrite: bool = False,
    ) -> None:
        """Prepares shotdata for GARPOS processing for the active campaign."""
        if campaign_id is not None:
            self.set_campaign(campaign_id=campaign_id)

        campaign_meta = self.workspace.metadata.campaign
        if campaign_meta is None:
            raise ValueError("Campaign must be set before preparing GARPOS shotdata.")

        surveys_to_process = [
            s for s in campaign_meta.surveys if survey_id is None or s.id == survey_id
        ]
        if not surveys_to_process:
            raise ValueError(f"Survey {survey_id} not found in campaign {campaign_meta.name}.")

        for survey in surveys_to_process:
            self.set_survey(survey_id=survey.id)
            logger.info(f"Processing survey {survey.id}")
            self.prepare_single_garpos_survey(
                survey=survey,
                custom_filters=custom_filters,
                overwrite=overwrite,
            )

    @validate_network_station_campaign
    def prepare_single_garpos_survey(
        self,
        survey: Survey,
        custom_filters: dict | None = None,
        overwrite: bool = False,
    ) -> None:
        """Prepare a single survey for GARPOS processing."""
        site = self.workspace.metadata.site
        campaign_meta = self.workspace.metadata.campaign
        assert site is not None and campaign_meta is not None

        survey_root = self.workspace.layout.survey
        campaign = self.workspace.layout.ensure_campaign()
        tiledb = self.workspace.layout.tiledb()

        # Find the canonical shotdata file written by parse_surveys.
        shotdata_file_name = f"{survey.id}_{survey.type.value}_shotdata.csv".replace(" ", "")
        shotdata_path = survey_root / shotdata_file_name
        if not shotdata_path.exists():
            raise FileNotFoundError(
                f"Shotdata file {shotdata_path} does not exist. Please run parse_surveys first."
            )

        shot_data_raw = pd.read_csv(shotdata_path)
        if shot_data_raw.empty:
            logger.warning(
                f"No shot data found for survey {shotdata_path}, skipping shot data preparation."
            )
            return

        garpos_layout = self.workspace.layout.ensure_garpos_survey()

        if not garpos_layout.settings_file.exists() or overwrite:
            GarposFixed()._to_datafile(garpos_layout.settings_file)

        filtered_path = shotdata_path.parent / f"{shotdata_path.stem}_filtered.csv"
        if filtered_path.exists():
            shot_data_filtered = pd.read_csv(filtered_path)
        else:
            shot_data_filtered = pd.DataFrame()

        if shot_data_filtered.empty or overwrite:
            filter_config = get_survey_filter_config(survey_type=survey.type)
            if custom_filters is not None:
                filter_config = validate_and_merge_config(
                    base_class=filter_config,
                    override_config=custom_filters,
                )
            shot_data_filtered = filter_shotdata(
                survey_type=survey.type,
                site=site,
                shot_data=shot_data_raw,
                kinPostionTDBUri=tiledb.kin_position,
                start_time=survey.start.replace(tzinfo=datetime.UTC),
                end_time=survey.end.replace(tzinfo=datetime.UTC),
                custom_filters=custom_filters,
            )
            if shot_data_filtered.empty:
                logger.warning(
                    f"No shot data remaining after filtering for survey "
                    f"{survey.id}, skipping survey."
                )
                return
            shot_data_filtered.to_csv(filtered_path)

        gp_transponders = GP_Transponders_from_benchmarks(
            coord_transformer=self.coordTransformer,
            survey=survey,
            site=site,
        )
        array_dpos_center = get_array_dpos_center(self.coordTransformer, gp_transponders)

        rectified_path = garpos_layout.root / f"{filtered_path.stem}_rectified.csv"
        if rectified_path.exists():
            shot_data_rectified = pd.read_csv(rectified_path)
        else:
            shot_data_rectified = pd.DataFrame()

        if shot_data_rectified.empty or overwrite:
            shot_data_rectified = prepare_shotdata_for_garpos(
                coord_transformer=self.coordTransformer,
                shodata_out_path=rectified_path,
                shot_data=shot_data_filtered,
                GPtransponders=gp_transponders,
            )
            if shot_data_rectified.empty:
                logger.warning(
                    f"No shot data remaining after rectification for survey "
                    f"{survey.id}, skipping survey."
                )
                return
            shot_data_rectified.to_csv(rectified_path)

        # Copy campaign SVP into garpos dir if missing.
        if not garpos_layout.svp_file.exists():
            if campaign.svp_file.exists():
                shutil.copy(campaign.svp_file, garpos_layout.svp_file)
            else:
                logger.warning(
                    f"No sound speed profile file found for campaign "
                    f"{campaign_meta.name}, GARPOS processing may fail."
                )

        if not garpos_layout.obs_file.exists() or overwrite:
            garpos_input = prepare_garpos_input_from_survey(
                shot_data_path=rectified_path,
                survey=survey,
                site=site,
                campaign=campaign_meta,
                ss_path=garpos_layout.svp_file,
                array_dpos_center=array_dpos_center,
                num_of_shots=len(shot_data_rectified),
                GPtransponders=gp_transponders,
            )
            site_config_update: GarposSiteConfig = get_garpos_site_config(survey.type)
            garpos_input_configured: GarposInput = apply_survey_config(
                site_config_update, garpos_input
            )
            garpos_input_configured.to_datafile(garpos_layout.obs_file)

    # ------------------------------------------------------------------
    # S3 sync (uses cloudpathlib directly until the legacy
    # directorymgmt subpackage is removed).
    # ------------------------------------------------------------------

    def _s3_root(self) -> CloudPath | None:
        """Return the configured S3 root as a CloudPath, or None if unset."""
        bucket = Environment.s3_sync_bucket()
        if bucket is None:
            return None
        if not bucket.startswith("s3://"):
            bucket = f"s3://{bucket}"
        return CloudPath(bucket)

    @validate_network_station
    def midprocess_sync_station_data_s3(self, overwrite: bool = False) -> None:
        """Upload the active station's TileDB arrays to S3."""
        s3_root = self._s3_root()
        if s3_root is None:
            logger.warning("S3 synchronization skipped: s3_sync_bucket not configured")
            return

        s3_station = s3_root / self.workspace.network_name / self.workspace.station_name
        local_tdb = self.workspace.layout.tiledb()
        s3_tdb = s3_station / "TileDB"

        for tdb_attr, tdb_basename in [
            ("shotdata", "shotdata.tdb"),
            ("kin_position", "kin_position.tdb"),
            ("imu_position", "imu_position.tdb"),
            ("gnss_obs", "gnss_obs.tdb"),
        ]:
            local_array: Path = getattr(local_tdb, tdb_attr)
            s3_array = s3_tdb / tdb_basename
            print(f"Syncing {local_array} to {s3_array}")
            upload_counter = 0
            for tdb_file in local_array.rglob("*"):
                if not tdb_file.is_file():
                    continue
                relative = tdb_file.relative_to(local_array)
                s3_file = s3_array / relative
                try:
                    if not s3_file.exists() or overwrite:
                        s3_file.upload_from(tdb_file, force_overwrite_to_cloud=overwrite)
                        upload_counter += 1
                except Exception as e:
                    logger.error(f"Failed to upload {tdb_file} to S3: {e}")
            print(f"Uploaded {upload_counter} files to {s3_array}")

    @validate_network_station_campaign
    def midprocess_sync_campaign_data_s3(self, overwrite: bool = False) -> None:
        """Upload SVP and intermediate RINEX files for the active campaign to S3."""
        s3_root = self._s3_root()
        if s3_root is None:
            logger.warning("S3 synchronization skipped: s3_sync_bucket not configured")
            return

        s3_campaign = (
            s3_root
            / self.workspace.network_name
            / self.workspace.station_name
            / self.workspace.campaign_name
        )
        campaign = self.workspace.layout.ensure_campaign()

        local_svp = campaign.svp_file
        s3_svp = s3_campaign / "processed" / "svp.csv"
        try:
            if local_svp.exists() and not s3_svp.exists():
                s3_svp.upload_from(local_svp, force_overwrite_to_cloud=overwrite)
        except Exception as e:
            logger.error(f"Failed to upload {local_svp} to S3: {e}")

        local_intermediate = campaign.intermediate
        s3_rinex_dest_dir = s3_campaign / "processed" / "rinex"
        station_name = self.workspace.station_name

        def upload_rinex_file(rinex_file: Path) -> None:
            if ".crx" in rinex_file.suffix:
                return
            if not any(ext in rinex_file.suffix for ext in ["S", "d", ".gz"]):
                old_suffix = rinex_file.suffix
                new_suffix = old_suffix[:-1] + "d"
                source = rinex_file.with_suffix(new_suffix + ".gz")
                if not source.exists():
                    crinex_compress(rinex_file, source, gzip=True, logger=logger.logger)
                    assert source.exists(), f"CRINEX compression failed for {rinex_file}"
            else:
                source = rinex_file
            relative = source.relative_to(local_intermediate)
            s3_file = s3_rinex_dest_dir / relative
            try:
                if not s3_file.exists() or overwrite:
                    s3_file.upload_from(source, force_overwrite_to_cloud=overwrite)
                    print(f"Uploaded {source} to {s3_file}")
            except Exception as e:
                logger.error(f"Failed to upload {source} to S3: {e}")

        if local_intermediate.exists():
            print(
                f"Syncing intermediate Rinex files from {local_intermediate} "
                f"to {s3_rinex_dest_dir}..."
            )
            rinex_files = list(local_intermediate.rglob(f"*{station_name}*"))
            with ThreadPoolExecutor(max_workers=25) as executor:
                executor.map(upload_rinex_file, rinex_files)

    @validate_network_station_campaign
    def midprocess_sync_s3(self, overwrite: bool = False) -> None:
        """Upload station TileDB arrays + per-campaign SVP/log files to S3."""
        s3_root = self._s3_root()
        if s3_root is None:
            logger.warning("S3 synchronization skipped: s3_sync_bucket not configured")
            return

        s3_station = s3_root / self.workspace.network_name / self.workspace.station_name
        local_tdb = self.workspace.layout.tiledb()
        s3_tdb = s3_station / "TileDB"

        for tdb_attr, tdb_basename in [
            ("shotdata", "shotdata.tdb"),
            ("kin_position", "kin_position.tdb"),
            ("imu_position", "imu_position.tdb"),
            ("gnss_obs", "gnss_obs.tdb"),
        ]:
            local_array: Path = getattr(local_tdb, tdb_attr)
            s3_array = s3_tdb / tdb_basename
            for tdb_file in local_array.rglob("*"):
                if not tdb_file.is_file():
                    continue
                relative = tdb_file.relative_to(local_array)
                s3_file = s3_array / relative
                try:
                    if not s3_file.exists() or overwrite:
                        s3_file.upload_from(tdb_file, force_overwrite_to_cloud=overwrite)
                except Exception as e:
                    logger.error(f"Failed to upload {tdb_file} to S3: {e}")

        # Sync per-campaign svp + logs
        for campaign_name in self.workspace.layout.list_campaigns():
            local_campaign_dir = (
                self.workspace.root
                / self.workspace.network_name
                / self.workspace.station_name
                / campaign_name
            )
            s3_campaign = s3_station / campaign_name

            local_svp = local_campaign_dir / "processed" / "svp.csv"
            s3_svp = s3_campaign / "processed" / "svp.csv"
            try:
                if local_svp.exists() and not s3_svp.exists():
                    s3_svp.upload_from(local_svp, force_overwrite_to_cloud=overwrite)
            except Exception as e:
                logger.error(f"Failed to upload {local_svp} to S3: {e}")

            local_log_dir = local_campaign_dir / "logs"
            s3_log_dir = s3_campaign / "logs"
            if local_log_dir.exists():
                for log_file in local_log_dir.rglob("*"):
                    if log_file.is_file():
                        relative = log_file.relative_to(local_log_dir)
                        s3_log_file = s3_log_dir / relative
                        try:
                            s3_log_file.upload_from(log_file, force_overwrite_to_cloud=overwrite)
                        except Exception as e:
                            logger.error(f"Failed to upload {log_file} to S3: {e}")

    # ------------------------------------------------------------------
    # QC pseudo-survey parsing
    # ------------------------------------------------------------------

    def get_pseudo_surveys(self, shotdatatdb: TDBShotDataArray) -> list[Survey]:
        """Generate pseudo-surveys from unique shotdata dates."""
        pseudo_surveys: list[Survey] = []
        dates: list[np.datetime64] = shotdatatdb.get_unique_dates().tolist()
        if not dates:
            logger.warning("No shotdata dates found to generate pseudo-surveys.")
            return pseudo_surveys

        campaign_name = self.workspace.campaign_name
        if campaign_name is None:
            return pseudo_surveys
        current_year = int(campaign_name.split("_")[0])
        filtered_dates = [d for d in dates if d.year == current_year]
        if not filtered_dates:
            logger.warning(
                f"No shotdata dates found for campaign year {current_year} "
                "to generate pseudo-surveys."
            )
            return pseudo_surveys

        for idx, date in enumerate(sorted(filtered_dates)):
            start_time = (
                pd.Timestamp(date)
                .tz_localize("UTC")
                .to_pydatetime()
                .replace(hour=0, minute=0, second=0, microsecond=0)
            )
            end_time = datetime.datetime.combine(start_time.date(), datetime.time.max).replace(
                tzinfo=datetime.UTC
            )
            year, month, day = start_time.year, start_time.month, start_time.day
            pseudo_surveys.append(
                Survey(
                    id=f"{year}_{month}_{day}_{idx + 1}",
                    type="unknown",
                    start=start_time,
                    end=end_time,
                    benchmarkIDs=[],
                )
            )
        return pseudo_surveys

    @validate_network_station_campaign
    def parse_surveys_qc(
        self,
        shotdata_uri: str | Path,
        override: bool = False,
    ) -> list[GARPOSLayout] | None:
        """Parse QC pseudo-surveys and produce GARPOS input files."""
        site = self.workspace.metadata.site
        campaign_meta = self.workspace.metadata.campaign
        assert site is not None
        campaign = self.workspace.layout.ensure_campaign()

        garpos_layouts: list[GARPOSLayout] = []
        shotDataTDB = TDBShotDataArray(Path(shotdata_uri))
        surveys_to_process: list[Survey] = self.get_pseudo_surveys(shotDataTDB)

        for survey in surveys_to_process:
            survey_dir = campaign.qc / survey.id
            survey_dir.mkdir(parents=True, exist_ok=True)

            shotdata_file_name = f"{survey.id}_{survey.type.value}_shotdata.csv".replace(" ", "")
            shotdata_dest = survey_dir / shotdata_file_name

            if not shotdata_dest.exists() or shotdata_dest.stat().st_size == 0 or override:
                df = shotDataTDB.read_df(start=survey.start, end=survey.end)
                if df.empty:
                    logger.warning(
                        f"No shot data found for survey {survey.id} from "
                        f"{survey.start} to {survey.end}, skipping survey."
                    )
                    continue
                df.to_csv(shotdata_dest)
            else:
                df = pd.read_csv(shotdata_dest)

            garpos_layout = GARPOSLayout.for_survey(survey_dir)
            for d in garpos_layout.standard_dirs:
                d.mkdir(parents=True, exist_ok=True)

            if not garpos_layout.svp_file.exists():
                if campaign.svp_file.exists():
                    shutil.copy(campaign.svp_file, garpos_layout.svp_file)

            if not garpos_layout.settings_file.exists() or override:
                GarposFixed()._to_datafile(garpos_layout.settings_file)

            rectified_path = garpos_layout.root / f"{shotdata_dest.stem}_rectified.csv"

            if not rectified_path.exists() or override:
                gp_transponders = GP_Transponders_from_benchmarks(
                    coord_transformer=self.coordTransformer,
                    survey=survey,
                    site=site,
                    is_qc=True,
                )
                array_dpos_center = get_array_dpos_center(self.coordTransformer, gp_transponders)

                if rectified_path.exists():
                    shotdata_rectified = pd.read_csv(rectified_path)
                else:
                    shotdata_rectified = pd.DataFrame()

                if shotdata_rectified.empty or override:
                    shotdata_rectified = prepare_shotdata_for_garpos(
                        coord_transformer=self.coordTransformer,
                        shodata_out_path=rectified_path,
                        shot_data=df,
                        GPtransponders=gp_transponders,
                    )
                    if shotdata_rectified.empty:
                        logger.warning(
                            f"No shot data remaining after rectification for "
                            f"survey {survey.id}, skipping survey."
                        )
                        return None
                    shotdata_rectified.to_csv(rectified_path)

            if not garpos_layout.obs_file.exists() or override:
                garpos_input = prepare_garpos_input_from_survey(
                    shot_data_path=rectified_path,
                    survey=survey,
                    site=site,
                    campaign=campaign_meta,
                    ss_path=garpos_layout.svp_file,
                    array_dpos_center=array_dpos_center,
                    num_of_shots=len(shotdata_rectified),
                    GPtransponders=gp_transponders,
                )
                site_config_update: GarposSiteConfig = get_garpos_site_config(survey.type)
                garpos_input_configured: GarposInput = apply_survey_config(
                    site_config_update, garpos_input
                )
                garpos_input_configured.to_datafile(garpos_layout.obs_file)

            garpos_layouts.append(garpos_layout)

        return garpos_layouts
