"""IntermediateDataProcessor — mid-process workflow class.
Migrated to :class:`WorkflowBase` + :class:`Workspace` (RFC-A Phase 4).
"""

from __future__ import annotations

import datetime
import json
import shutil
from pathlib import Path

import numpy as np
import pandas as pd

from earthscope_sfg_tools.datamodels.metadata import Campaign, Site, Survey
from earthscope_sfg_tools.tiledb_integration import (
    TDBIMUPositionArray,
    TDBKinPositionArray,
    TDBShotDataArray,
)

from earthscope_sfg_workflows.logging import GarposLogger as logger
from earthscope_sfg_workflows.utils.model_update import validate_and_merge_config

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
    validate_network_station_campaign,
)
from ..session import StationSession as Workspace, _build_default_workspace


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
        import warnings
        warnings.warn(
            "IntermediateDataProcessor is deprecated and will be removed in a future release. "
            "Use StationSession directly for survey parsing, or GarposHandler for GARPOS preparation.",
            DeprecationWarning,
            stacklevel=2,
        )

        if workspace is None:
            import os

            if directory is None:
                directory = os.environ.get("MAIN_DIRECTORY", ".")
            workspace = _build_default_workspace(directory)

        super().__init__(workspace)
        self.s3_sync_bucket: str | None = s3_sync_bucket
        if station_metadata is not None:
            self.workspace.load_site_metadata(station_metadata)

        if self.workspace.site is not None:
            site = self.workspace.site
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
        return self.workspace.site  # type: ignore[return-value]

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
            self.workspace.station = None

    @property
    def current_campaign_metadata(self) -> Campaign | None:
        return self.workspace.campaign_meta  # type: ignore[return-value]

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
            assert self.workspace.site is not None, (
                f"Site metadata file not found for station {station_id}; "
                "cannot proceed with mid-process workflow."
            )
            site = self.workspace.site
            self.coordTransformer = CoordTransformer(
                latitude=site.arrayCenter.latitude,
                longitude=site.arrayCenter.longitude,
                elevation=-float(site.localGeoidHeight),
            )

    def set_campaign(self, campaign_id: str) -> None:
        if self.mid_process_workflow and self.workspace.site is not None:
            self.workspace.select_campaign_from_metadata(campaign_id)
        else:
            self.workspace.set_campaign(campaign_id)
        self.workspace.ensure_campaign()

    def set_survey(self, survey_id: str) -> None:
        if self.mid_process_workflow and self.workspace.campaign_meta is not None:
            self.workspace.select_survey_from_metadata(survey_id)
        else:
            self.workspace.set_survey(survey_id)
        # Materialize survey directory.
        survey_dir = self.workspace.survey_dir
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
        campaign_meta = self.workspace.campaign_meta
        if campaign_meta is None:
            raise ValueError("Campaign metadata must be loaded before parse_surveys")

        tiledb = self.workspace.tiledb_layout()
        campaign = self.workspace.ensure_campaign()

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
            survey_root = self.workspace.survey_dir

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

            with open(self.workspace.survey_metadata_file, "w") as f:
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

        campaign_meta = self.workspace.campaign_meta
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
        site = self.workspace.site
        campaign_meta = self.workspace.campaign_meta
        assert site is not None and campaign_meta is not None

        survey_root = self.workspace.survey_dir
        campaign = self.workspace.ensure_campaign()
        tiledb = self.workspace.tiledb_layout()

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

        garpos_layout = self.workspace.ensure_garpos_survey()

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
        site = self.workspace.site
        campaign_meta = self.workspace.campaign_meta
        assert site is not None
        campaign = self.workspace.ensure_campaign()

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
