"""
GarposHandler class for processing and preparing shot data for the GARPOS model.
"""

import shutil
from datetime import UTC, datetime, time
from pathlib import Path

# Plotting imports
import matplotlib.dates as mdates
import matplotlib.gridspec as gridspec
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import seaborn as sns
from matplotlib.colors import Normalize

sns.set_theme(style="whitegrid")

from earthscope_sfg_tools.datamodels.metadata import Campaign, Site, Survey  # noqa: E402
from earthscope_sfg_tools.tiledb_integration import (  # noqa: E402
    TDBIMUPositionArray,
    TDBKinPositionArray,
    TDBShotDataArray,
)

from ...config.loadconfigs import (  # noqa: E402
    GarposSiteConfig,
    get_garpos_site_config,
    get_survey_filter_config,
)
from ...data_mgmt.model import GARPOSLayout  # noqa: E402
from .data_prep import (  # noqa: E402
    GP_Transponders_from_benchmarks,
    apply_survey_config,
    get_array_dpos_center,
    prepare_garpos_input_from_survey,
    prepare_shotdata_for_garpos,
)
from .functions import CoordTransformer, process_garpos_results  # noqa: E402
from .load_utils import get_drive_garpos, get_lib_paths  # noqa: E402
from .schemas import (  # noqa: E402
    GarposFixed,
    GarposInput,
    InversionParams,
    ObservationData,
)
from ...prefiltering import filter_shotdata  # noqa: E402
from earthscope_sfg_workflows.logging import GarposLogger as logger  # noqa: E402
from earthscope_sfg_workflows.utils.model_update import validate_and_merge_config  # noqa: E402
from earthscope_sfg_workflows.workflows.session import StationSession  # noqa: E402

colors = [
    "blue",
    "green",
    "red",
    "cyan",
    "magenta",
    "yellow",
    "black",
    "brown",
    "orange",
    "pink",
]


class GarposHandler:
    """High-level interface for running the GARPOS acoustic positioning model.

    Wraps a :class:`StationSession` to provide data preparation, model
    execution, and result visualisation for GARPOS inversions.

    Attributes:
        station_session: Active session providing scope, paths, and metadata.
        garpos_fixed: Fixed/inversion parameters passed to the GARPOS solver.
        _coord_transformer: ENU coordinate transformer derived from the site array centre.
        current_garpos_survey_dir: Layout for the most recently activated survey.
    """


    def __init__(self, station_session: StationSession) -> None:
        """Initializes the GarposHandler.

        Args:
            station_session: The active :class:`StationSession` providing scope, paths,
                and metadata.
        """
        self.station_session = station_session
        self.garpos_fixed = GarposFixed()
        self.current_garpos_survey_dir: GARPOSLayout | None = None
        site = station_session.site
        if site is not None:
            self._coord_transformer: CoordTransformer | None = CoordTransformer(
                latitude=site.arrayCenter.latitude,
                longitude=site.arrayCenter.longitude,
                elevation=-float(site.localGeoidHeight),
            )
        else:
            self._coord_transformer = None

    def ensure_garpos_survey(self) -> GARPOSLayout:
        """Materialise GARPOS survey directories and return the layout."""
        return self.station_session.ensure_garpos_survey()

    def _load_results_file(self, run_dir: Path) -> Path:
        """Return the last *-res.dat file in run_dir, sorted by iteration number."""
        data_files = sorted(
            run_dir.glob("*-res.dat"),
            key=lambda x: int(x.stem.split("_")[-1].split("-")[0]),
        )
        if not data_files:
            raise FileNotFoundError(f"No *-res.dat files found in {run_dir}")
        logger.info(f"Using data file {data_files[-1]} for plotting.")
        return data_files[-1]

    def set_campaign(
        self, campaign_id: str
    ) -> None:
        """Backward-compat scope setter.

        Network and station are already fixed on the underlying
        :class:`StationSession`; only the campaign is applied here.
        """
        self.station_session.set_campaign(campaign_id)

    def set_survey(self, survey_id: str) -> None:
        """Sets the current survey.
        Args:
            survey_id: The ID of the survey to set.

        Raises:
            ValueError: If the survey is not found in the current campaign.
        """

        self.station_session.set_survey(survey_id)

        # Resolve the shotdata file produced by parse_surveys.
        campaign_meta = self.station_session.campaign_meta
        survey_type = None
        if campaign_meta is not None:
            for s in campaign_meta.surveys:
                if s.id == survey_id:
                    survey_type = s.type.value
                    break
        if survey_type is None:
            raise ValueError(
                f"Survey {survey_id} not found in campaign metadata; cannot locate shotdata."
            )

        survey_root = self.station_session.survey_dir
        shotdata_file = survey_root / f"{survey_id}_{survey_type}_shotdata.csv".replace(" ", "")
        if not shotdata_file.exists():
            raise ValueError(
                f"Shotdata for survey {survey_id} not found at {shotdata_file}. "
                "Please run parse_surveys() first."
            )

        garpos_layout = self.ensure_garpos_survey()
        rectified_files = list(garpos_layout.root.glob("*_rectified.csv"))
        rectified = rectified_files[0] if rectified_files else None
        if rectified is None or not rectified.exists():
            raise ValueError(
                f"Rectified shotdata for survey {survey_id} not found in "
                f"{garpos_layout.root}. Please run prepare_shotdata_garpos() first."
            )
        self.current_garpos_survey_dir = garpos_layout
        logger.set_dir(garpos_layout.logs)

    def set_inversion_params(self, parameters: dict | InversionParams):
        """Set inversion parameters for the model.
        This method updates the inversion parameters of the model using the
        key-value pairs provided in the `args` dictionary. Each key in the
        dictionary corresponds to an attribute of the `inversion_params`
        object, and the associated value is assigned to that attribute.

        Args:
            parameters: A dictionary containing key-value pairs to update the inversion parameters or an InversionParams object.
        """

        self.garpos_fixed.inversion_params = validate_and_merge_config(
            base_class=self.garpos_fixed.inversion_params, override_config=parameters
        )

    # ------------------------------------------------------------------
    # Survey parsing and GARPOS data preparation
    # ------------------------------------------------------------------

    def prepare_shotdata_garpos(
        self,
        campaign_id: str | None = None,
        survey_id: str | None = None,
        custom_filters: dict | None = None,
        overwrite: bool = False,
    ) -> None:
        """Prepares shotdata for GARPOS processing for the active campaign."""
        if campaign_id is not None:
            self.station_session.set_campaign(campaign_id=campaign_id)

        campaign_meta = self.station_session.campaign_meta
        if campaign_meta is None:
            raise ValueError("Campaign must be set before preparing GARPOS shotdata.")

        surveys_to_process = [
            s for s in campaign_meta.surveys if survey_id is None or s.id == survey_id
        ]
        if not surveys_to_process:
            raise ValueError(f"Survey {survey_id} not found in campaign {campaign_meta.name}.")

        for survey in surveys_to_process:
            self.station_session.set_survey(survey_id=survey.id)
            logger.info(f"Processing survey {survey.id}")
            self.prepare_single_garpos_survey(
                survey=survey,
                custom_filters=custom_filters,
                overwrite=overwrite,
            )

    def prepare_single_garpos_survey(
        self,
        survey: Survey,
        custom_filters: dict | None = None,
        overwrite: bool = False,
    ) -> None:
        """Prepare a single survey for GARPOS processing."""

        site = self.station_session.site
        campaign_meta = self.station_session.campaign_meta
        assert site is not None and campaign_meta is not None

        survey_root = self.station_session.survey_dir
        campaign = self.station_session.ensure_campaign()
        tiledb = self.station_session.tiledb_layout()

        shotdata_file_name = f"{survey.id}_{survey.type.value}_shotdata.csv".replace(" ", "")
        shotdata_path = survey_root / shotdata_file_name
        if not shotdata_path.exists():
            logger.warning(
                f"Shotdata file {shotdata_path} does not exist — skipping survey. "
                "Run parse_surveys first to generate shotdata CSVs."
            )
            return

        shot_data_raw = pd.read_csv(shotdata_path)
        if shot_data_raw.empty:
            logger.warning(
                f"No shot data found for survey {shotdata_path}, skipping shot data preparation."
            )
            return

        garpos_layout = self.ensure_garpos_survey()

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
                start_time=survey.start.replace(tzinfo=UTC),
                end_time=survey.end.replace(tzinfo=UTC),
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
            coord_transformer=self._coord_transformer,
            survey=survey,
            site=site,
        )
        array_dpos_center = get_array_dpos_center(self._coord_transformer, gp_transponders)

        rectified_path = garpos_layout.root / f"{filtered_path.stem}_rectified.csv"
        if rectified_path.exists():
            shot_data_rectified = pd.read_csv(rectified_path)
        else:
            shot_data_rectified = pd.DataFrame()

        if shot_data_rectified.empty or overwrite:
            shot_data_rectified = prepare_shotdata_for_garpos(
                coord_transformer=self._coord_transformer,
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

    def get_pseudo_surveys(self, shotdatatdb: TDBShotDataArray) -> list[Survey]:
        """Generate pseudo-surveys from unique shotdata dates."""
        pseudo_surveys: list[Survey] = []
        dates: list[np.datetime64] = shotdatatdb.get_unique_dates().tolist()
        if not dates:
            logger.warning("No shotdata dates found to generate pseudo-surveys.")
            return pseudo_surveys

        campaign_name = self.station_session.scope.campaign
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
            end_time = datetime.combine(start_time.date(), time.max).replace(tzinfo=UTC)
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

    def parse_surveys_qc(
        self,
        shotdata_uri: str | Path,
        override: bool = False,
    ) -> list[GARPOSLayout] | None:
        """Parse QC pseudo-surveys and produce GARPOS input files."""

        site = self.station_session.site
        campaign_meta = self.station_session.campaign_meta
        assert site is not None
        campaign = self.station_session.ensure_campaign()

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
                    coord_transformer=self._coord_transformer,
                    survey=survey,
                    site=site,
                    is_qc=True,
                )
                array_dpos_center = get_array_dpos_center(self._coord_transformer, gp_transponders)

                if rectified_path.exists():
                    shotdata_rectified = pd.read_csv(rectified_path)
                else:
                    shotdata_rectified = pd.DataFrame()

                if shotdata_rectified.empty or override:
                    shotdata_rectified = prepare_shotdata_for_garpos(
                        coord_transformer=self._coord_transformer,
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

    def _run_garpos(
        self,
        obsfile_path: Path,
        results_dir: Path,
        custom_settings: dict | InversionParams | None = None,
        run_id: int | str = 0,
        override: bool = False,
    ) -> Path:
        """Runs the GARPOS model.
        Args:
            obsfile_path: The path to the observation file.
            results_dir: The path to the results directory.
            custom_settings: Custom GARPOS settings to apply, by default None.
            run_id: The run ID, by default 0.
            override: If True, override existing results, by default False.

        Returns:
            The path to the results file.
        """

        garpos_fixed_params = self.garpos_fixed.model_copy()
        if custom_settings is not None:
            garpos_fixed_params.inversion_params = validate_and_merge_config(
                base_class=garpos_fixed_params.inversion_params,
                override_config=custom_settings,
            )

        # If a GARPOS_PATH source install is in use, fill in the Fortran lib
        # paths the inversion needs. The pip-installed `garpos` package
        # provides these internally, so missing values are not necessarily
        # an error here — surface them only if the inversion actually fails.
        if not garpos_fixed_params.lib_directory or not garpos_fixed_params.lib_raytrace:
            lib_paths = get_lib_paths()
            if lib_paths is not None:
                garpos_fixed_params.lib_directory, garpos_fixed_params.lib_raytrace = lib_paths

        garpos_input = GarposInput.from_datafile(obsfile_path)
        results_suffix = f"{garpos_input.survey_id}_{run_id}"
        results_path = results_dir / f"{results_suffix}-res.dat"

        if results_path.exists() and not override:
            print(f"Results already exist for {str(results_path)}")
            return None
        logger.info(
            f"Running GARPOS model for {garpos_input.site_name}, {garpos_input.survey_id}. Run ID: {run_id}"
        )
        input_path = results_dir / f"_{run_id}_observation.ini"
        fixed_path = results_dir / f"_{run_id}_settings.ini"
        garpos_fixed_params._to_datafile(fixed_path)
        garpos_input.to_datafile(input_path)

        rf = get_drive_garpos()(
            str(input_path),
            str(fixed_path),
            str(results_dir) + "/",
            f"{garpos_input.survey_id}_{run_id}",
            13,
        )
        return rf

    def _run_garpos_survey_dir(
        self,
        garpos_survey_dir: GARPOSLayout,
        custom_settings: dict | InversionParams | None = None,
        run_id: int | str = 0,
        iterations: int = 1,
        override: bool = False,
    ) -> None:
        """Run the GARPOS model for a specific GARPOSSurveyDir.
        Args:
            garpos_survey_dir: The GARPOS survey directory to run.
            custom_settings: Custom GARPOS settings to apply, by default None.
            run_id: The run identifier, by default 0.
            iterations: The number of iterations to run, by default 1.
            override: If True, override existing results, by default False.

        Raises:
            ValueError: If the observation file does not exist.
        """
        logger.info(
            f"Running GARPOS model for survey {garpos_survey_dir.root.parent.name}. Run ID: {run_id}"
        )

        results_dir_main = garpos_survey_dir.results
        results_dir = results_dir_main / f"run_{run_id}"
        if results_dir.exists() and override:
            # Remove existing results directory if override is True
            try:
                shutil.rmtree(results_dir)
            except Exception as e:
                logger.error(f"Failed to remove existing results directory {results_dir}: {e}")

        elif results_dir.exists() and not override:
            logger.info(
                f"Results directory {results_dir} already exists. Use override=True to overwrite existing results."
            )
            return
        results_dir.mkdir(parents=True, exist_ok=True)

        obsfile_path = garpos_survey_dir.obs_file

        if not obsfile_path.exists():
            raise ValueError(f"Observation file not found at {obsfile_path}")

        initialInput = GarposInput.from_datafile(obsfile_path)

        for i in range(iterations):
            logger.info(
                f"Iteration {i + 1} of {iterations} for survey {garpos_survey_dir.root.parent.name}"
            )

            obsfile_path = self._run_garpos(
                custom_settings=custom_settings,
                obsfile_path=obsfile_path,
                results_dir=results_dir,
                run_id=f"{i}",
                override=override,
            )
            if iterations > 1 and i < iterations - 1:
                iterationInput = GarposInput.from_datafile(obsfile_path)
                delta_position = iterationInput.delta_center_position.get_position()
                iterationInput.array_center_enu.east += delta_position[0]
                iterationInput.array_center_enu.north += delta_position[1]
                iterationInput.array_center_enu.up += delta_position[2]
                # zero out delta position for next iteration
                iterationInput.delta_center_position = initialInput.delta_center_position
                iterationInput.to_datafile(obsfile_path)
            # Add array delta center position to the array center enu

        results = GarposInput.from_datafile(obsfile_path)
        process_garpos_results(results)

    def _run_garpos_survey(
        self,
        survey_id: str,
        custom_settings: dict | InversionParams | None = None,
        run_id: int | str = 0,
        iterations: int = 1,
        override: bool = False,
    ) -> None:
        """Run the GARPOS model for a specific survey."""
        logger.info(f"Running GARPOS model for survey {survey_id}. Run ID: {run_id}")
        try:
            self.set_survey(survey_id=survey_id)
        except ValueError as e:
            logger.warning(f"Skipping survey {survey_id}: {e}")
            return
        self._run_garpos_survey_dir(
            garpos_survey_dir=self.current_garpos_survey_dir,
            custom_settings=custom_settings,
            run_id=run_id,
            iterations=iterations,
            override=override,
        )

    def run_garpos(
        self,
        survey_id: str | None = None,
        run_id: int | str = 0,
        iterations: int = 1,
        override: bool = False,
        custom_settings: dict | InversionParams | None = None,
        surveys: list[GARPOSLayout] | None = None,
    ) -> None:
        """Run the GARPOS model for a specific date or for all dates.
        Args:
            survey_id: The ID of the survey to run, by default None.
            run_id: The run identifier, by default 0.
            iterations: The number of iterations to run, by default 1.
            override: If True, override existing results, by default False.
            custom_settings: Custom GARPOS settings to apply, by default None.
        """

        logger.info(f"Running GARPOS model. Run ID: {run_id}")
        if surveys is None:
            surveys_to_process = (
                [s.id for s in self.station_session.campaign_meta.surveys]
                if survey_id is None
                else [survey_id]
            )
        elif all(isinstance(s, GARPOSLayout) for s in surveys):
            for garpos_survey_dir in surveys:
                self._run_garpos_survey_dir(
                    garpos_survey_dir=garpos_survey_dir,
                    custom_settings=custom_settings,
                    run_id=run_id,
                    iterations=iterations,
                    override=override,
                )
            return

        for survey_id in surveys_to_process:
            logger.info(f"Running GARPOS model for survey {survey_id}. Run ID: {run_id}")
            self._run_garpos_survey(
                survey_id=survey_id,
                run_id=run_id,
                override=override,
                iterations=iterations,
                custom_settings=dict(custom_settings).get("inversion_params")
                if custom_settings
                else None,
            )

    def plot_shotdata_replies_per_transponder(
        self,
        savefig: bool = False,
        showfig: bool = True,
    ) -> None:
        """Plots shotdata reply percentages per transponder for the active campaign."""
        metadata_surveys = []
        site = self.station_session.site
        if site is not None:
            for campaign in site.campaigns:
                if campaign.name == self.station_session.scope.campaign:
                    metadata_surveys = campaign.surveys
                    break

        metadata_time_windows = {}
        shotdata_time_windows = {}
        shotdata_dfs = {}
        shotdata_filtered_dfs = {}
        survey_id_to_type = {s.id: s.type.value for s in metadata_surveys}
        campaign_meta = self.station_session.campaign_meta
        survey_names = sorted(s.id for s in campaign_meta.surveys) if campaign_meta else []
        for survey_name in survey_names:
            if survey_name in survey_id_to_type:
                for survey in metadata_surveys:
                    if survey.id == survey_name:
                        metadata_start = survey.start.replace(tzinfo=UTC)
                        metadata_end = survey.end.replace(tzinfo=UTC)
                        metadata_time_windows[survey_name] = (
                            metadata_start,
                            metadata_end,
                        )
                    continue
                try:
                    survey_root = self.station_session.ensure_campaign().root / survey_name
                    survey_type = survey_id_to_type[survey_name]
                    shotdata_filepath = (
                        survey_root / f"{survey_name}_{survey_type}_shotdata.csv".replace(" ", "")
                    )
                    shotdata_df = pd.read_csv(shotdata_filepath, sep=",", header=0, index_col=0)
                    shotdata_dfs[survey_name] = shotdata_df
                    # use utc
                    start = datetime.fromtimestamp(shotdata_df["pingTime"].iloc[0], tz=UTC)
                    end = datetime.fromtimestamp(shotdata_df["pingTime"].iloc[-1], tz=UTC)  # noqa: F841
                    shotdata_time_windows[survey_name] = (start, end)

                    shotdata_filtered_filepath = (
                        survey_root / f"{shotdata_filepath.stem}_filtered.csv"
                    )
                    shotdata_filtered_df = pd.read_csv(
                        shotdata_filtered_filepath, sep=",", header=0, index_col=0
                    )
                    shotdata_filtered_dfs[survey_name] = shotdata_filtered_df

                except Exception as e:
                    print(e)

        fig, axs = plt.subplots(3, 1, figsize=(20, 15), sharex=False)
        for i, (survey_name, shotdata_df) in enumerate(shotdata_dfs.items()):
            try:
                unique_ids = shotdata_df["transponderID"].unique()
                for j, transponder_id in enumerate(unique_ids):
                    df = shotdata_df[shotdata_df["transponderID"] == transponder_id]
                    filtered_df = shotdata_filtered_dfs[survey_name][
                        shotdata_filtered_dfs[survey_name]["transponderID"] == transponder_id
                    ]
                    # # Resample the data to 10 minute intervals and count replies
                    df = df.set_index(pd.to_datetime(df["pingTime"], unit="s"))
                    filtered_df = filtered_df.set_index(
                        pd.to_datetime(filtered_df["pingTime"], unit="s")
                    )
                    replies_per_bin = df["pingTime"].resample("10min").count()
                    filtered_replies_per_bin = filtered_df["pingTime"].resample("10min").count()
                    axs[j].scatter(
                        replies_per_bin.index,
                        replies_per_bin.values / 40 * 100,
                        label=f"{survey_name} - {transponder_id}",
                        s=10,
                        color="black",
                    )
                    axs[j].scatter(
                        filtered_replies_per_bin.index,
                        filtered_replies_per_bin.values / 40 * 100,
                        label=f"{survey_name} - {transponder_id} (Filtered)",
                        s=10,
                        color=colors[(i) % len(colors)],
                    )
                    total_pings = shotdata_df["pingTime"].nunique()
                    survey_midpoint = (
                        metadata_time_windows[survey_name][0]
                        + (
                            metadata_time_windows[survey_name][1]
                            - metadata_time_windows[survey_name][0]
                        )
                        / 2
                    )
                    axs[j].text(
                        survey_midpoint,
                        110,
                        f"{survey_name}\n{next((survey.type.value for survey in metadata_surveys if survey.id == survey_name), 'Unknown')}\ntotal pings: {total_pings}\ntotal replies: {replies_per_bin.sum()}\nfiltered replies: {filtered_replies_per_bin.sum()}\nfiltered reply %: {filtered_replies_per_bin.sum() / total_pings * 100:.2f}%",
                        fontsize=12,
                        ha="center",
                    )
                    axs[j].set_xlabel("Time")
                    axs[j].set_ylabel("% Expected replies per 10 min bin")
                    axs[j].set_ylim(0, 150)
                    axs[j].axvspan(
                        xmin=metadata_time_windows[survey_name][0],
                        xmax=metadata_time_windows[survey_name][1],
                        color=colors[(i) % len(colors)],
                        linestyle="--",
                        linewidth=1,
                        alpha=0.1,
                    )
            except Exception:
                logger.warning(f"Error processing {survey_name}")
        fig.suptitle(
            f"Shotdata Reply Percentages for {self.station_session.scope.station} {self.station_session.scope.campaign}"
        )
        axs[0].set_title(f"{self.station_session.scope.station} Transponder 5209")
        axs[1].set_title(f"{self.station_session.scope.station} Transponder 5210")
        axs[2].set_title(f"{self.station_session.scope.station} Transponder 5211")
        fig.tight_layout()
        if showfig:
            plt.show()

        fig_path = f"{self.station_session.ensure_campaign().root}/{self.station_session.scope.station}_{self.station_session.scope.campaign}_shotdata_replies.png"
        if savefig:
            logger.info(f"Saving figure to {fig_path}")
            plt.savefig(
                fig_path,
                dpi=300,
                bbox_inches="tight",
                pad_inches=0.1,
            )

    def plot_residuals_per_transponder_before_and_after(
        self,
        survey_id: str,
        run_id: int | str = 0,
        savefig: bool = False,
        showfig: bool = True,
    ):
        surveys_to_process = []
        for survey in self.station_session.campaign_meta.surveys:
            if survey.id == survey_id or survey_id is None:
                surveys_to_process.append((survey.id, survey.type.value))

        for sid, _ in surveys_to_process:
            try:
                self.set_survey(sid)
                self._plot_residuals_per_transponder_before_and_after(
                    survey_id=sid,
                    run_id=run_id,
                    savefig=savefig,
                    showfig=showfig,
                )
            except Exception as e:
                logger.warning(f"Skipping plotting for survey {sid}: {e}")
                continue

    def _plot_residuals_per_transponder_before_and_after(
        self,
        survey_id: str,
        run_id: int | str = 0,
        savefig: bool = False,
        showfig: bool = True,
    ):
        """Plots the residuals on 3 subplots for a given survey.
        Args:
            survey_id (str): The ID of the survey to plot results for.
            run_id (int | str, optional): The run ID of the survey results to plot. Defaults to 0.
            savefig (bool, optional): If True, save the figure, by default False.
            showfig (bool, optional): If True, display the figure, by default True.
        """
        results_dir: Path = self.current_garpos_survey_dir.results
        run_dir = results_dir / f"run_{run_id}"
        if not run_dir.exists():
            raise FileNotFoundError(f"Run directory {run_dir} does not exist.")

        garpos_results = GarposInput.from_datafile(self._load_results_file(run_dir))

        array_enu = garpos_results.array_center_enu
        array_dpos = garpos_results.delta_center_position
        if array_enu is None or array_dpos is None:
            raise ValueError("Array center or delta position not found in GARPOS results.")

        array_final_position = array_dpos.model_copy()
        array_final_position.east += array_enu.east
        array_final_position.north += array_enu.north
        array_final_position.up += array_enu.up

        results_df_raw = pd.read_csv(garpos_results.shot_data)
        results_df_raw = ObservationData.validate(results_df_raw, lazy=True)
        results_df_raw["time"] = results_df_raw.ST.apply(lambda x: datetime.fromtimestamp(x, tz=UTC))
        df_filter_2 = ~results_df_raw["flag"]
        results_df = results_df_raw[df_filter_2]
        unique_ids = results_df_raw["MT"].unique()
        # make a plot with 3 subplots showing ResiRange vs time for each unique_id
        fig, axs = plt.subplots(3, 1, figsize=(20, 8), sharex=True)
        fig.suptitle(f"Residuals for {self.station_session.station_name} {survey_id} (Run {run_id})")
        for i, unique_id in enumerate(unique_ids):
            transponder_df_raw = results_df_raw[results_df_raw["MT"] == unique_id].sort_values(
                "time"
            )
            transponder_df = results_df[results_df["MT"] == unique_id].sort_values("time")
            axs[i].scatter(
                transponder_df_raw["time"],
                transponder_df_raw["ResiRange"],
                s=1,
                label=f"{unique_id}_raw {transponder_df_raw['time'].count()}",
                color="blue",
            )
            percent_remaining = round(
                transponder_df["time"].count() / transponder_df_raw["time"].count() * 100,
                1,
            )
            axs[i].scatter(
                transponder_df["time"],
                transponder_df["ResiRange"],
                s=1,
                label=f"{unique_id}_unflagged {transponder_df['time'].count()} ({percent_remaining} %)",
                color="orange",
            )
            axs[i].set_ylabel("Residual (m)")
            axs[i].legend(loc="upper right")
            axs[i].grid()
        axs[-1].set_xlabel("Time")
        plt.xticks(rotation=45)
        # add gridlines
        for ax in axs:
            ax.grid()
        plt.tight_layout()
        fig_path = f"{self.current_garpos_survey_dir.results}/{self.station_session.station_name}_{survey_id}_flagged_residuals.png"
        if savefig:
            logger.info(f"Saving figure to {fig_path}")
            plt.savefig(
                fig_path,
                dpi=300,
                bbox_inches="tight",
                pad_inches=0.1,
            )
        if showfig:
            plt.show()

    def plot_remaining_residuals_per_transponder(
        self,
        survey_id: str,
        run_id: int | str = 0,
        subplots: bool = True,
        savefig: bool = False,
        showfig: bool = True,
    ) -> None:
        """Plots the remaining residuals for each transponder.
        Args:
            survey_id (str): The ID of the survey to plot results for.
            run_id (int | str, optional): The run ID of the survey results to plot. Defaults to 0.
            savefig (bool, optional): If True, save the figure. Defaults to False.
            showfig (bool, optional): If True, display the figure. Defaults to True.
        """
        surveys_to_process = []
        for survey in self.station_session.campaign_meta.surveys:
            if survey.id == survey_id or survey_id is None:
                surveys_to_process.append((survey.id, survey.type.value))

        for sid, _ in surveys_to_process:
            try:
                self.set_survey(sid)
                self._plot_remaining_residuals_per_transponder(
                    survey_id=sid,
                    run_id=run_id,
                    subplots=subplots,
                    savefig=savefig,
                    showfig=showfig,
                )
            except Exception as e:
                logger.warning(f"Skipping plotting for survey {sid}: {e}")
                continue

    def _plot_remaining_residuals_per_transponder(
        self,
        survey_id: str,
        run_id: int | str = 0,
        subplots: bool = True,
        savefig: bool = False,
        showfig: bool = True,
    ):
        """Plots the residuals on 3 subplots for a given survey.
        Args:
            survey_id (str): The ID of the survey to plot results for.
            run_id (int | str, optional): The run ID of the survey results to plot. Defaults to 0.
            subplots (bool, optional): If True, use multiple subplots for the residuals. Defaults to True.
            savefig (bool, optional): If True, save the figure, by default False.
            showfig (bool, optional): If True, display the figure, by default True.
        """
        results_dir: Path = self.current_garpos_survey_dir.results
        run_dir = results_dir / f"run_{run_id}"
        if not run_dir.exists():
            raise FileNotFoundError(f"Run directory {run_dir} does not exist.")

        garpos_results = GarposInput.from_datafile(self._load_results_file(run_dir))

        array_enu = garpos_results.array_center_enu
        array_dpos = garpos_results.delta_center_position
        if array_enu is None or array_dpos is None:
            raise ValueError("Array center or delta position not found in GARPOS results.")

        array_final_position = array_dpos.model_copy()
        array_final_position.east += array_enu.east
        array_final_position.north += array_enu.north
        array_final_position.up += array_enu.up

        results_df_raw = pd.read_csv(garpos_results.shot_data)
        results_df_raw = ObservationData.validate(results_df_raw, lazy=True)
        results_df_raw["time"] = results_df_raw.ST.apply(lambda x: datetime.fromtimestamp(x, tz=UTC))
        df_filter_2 = ~results_df_raw["flag"]
        results_df = results_df_raw[df_filter_2]
        unique_ids = results_df_raw["MT"].unique()
        transponder_colors = ["green", "orange", "blue"]
        if subplots:
            # make a plot with 3 subplots showing ResiRange vs time for each unique_id
            fig, axs = plt.subplots(3, 1, figsize=(20, 8), sharex=True)
            fig.suptitle(f"Residuals for {self.station_session.station_name} {survey_id} (Run {run_id})")
            for i, unique_id in enumerate(unique_ids):
                transponder_df = results_df[results_df["MT"] == unique_id].sort_values("time")
                axs[i].scatter(
                    transponder_df["time"],
                    transponder_df["ResiRange"],
                    s=1,
                    label=f"{unique_id}_unflagged {transponder_df['time'].count()}",
                    color=transponder_colors[i],
                )
                axs[i].set_ylabel("Residual (m)")
                axs[i].legend(loc="upper right")
                axs[i].grid()
            axs[-1].set_xlabel("Time")
            plt.xticks(rotation=45)
            # add gridlines
            for ax in axs:
                ax.grid()
        else:
            fig, ax = plt.subplots(figsize=(20, 8))
            for i, unique_id in enumerate(unique_ids):
                transponder_df = results_df[results_df["MT"] == unique_id].sort_values("time")
                ax.scatter(
                    transponder_df["time"],
                    transponder_df["ResiRange"],
                    s=1,
                    label=f"{unique_id}_unflagged {transponder_df['time'].count()}",
                    color=transponder_colors[i],
                )
            ax.set_ylabel("Residual (m)")
            ax.legend()
            ax.grid()
            ax.set_xlabel("Time")
            plt.xticks(rotation=45)
            # add gridlines
            ax.grid()
        plt.tight_layout()
        fig_path = f"{self.current_garpos_survey_dir.results}/{self.station_session.station_name}_{survey_id}_garpos_residuals.png"
        if savefig:
            logger.info(f"Saving figure to {fig_path}")
            plt.savefig(
                fig_path,
                dpi=300,
                bbox_inches="tight",
                pad_inches=0.1,
            )
        if showfig:
            plt.show()

    def plot_ts_results(
        self,
        survey_id: str = None,
        run_id: int | str = 0,
        res_filter: float = 10,
        savefig: bool = False,
        showfig: bool = True,
    ) -> None:
        """Plots the time series results for a given survey.
        Args:
            survey_id: ID of the survey to plot results for, by default None.
            run_id: The run ID of the survey results to plot, by default 0.
            res_filter: The residual filter value to filter outrageous values (m), by default 10.
            savefig: If True, save the figure, by default False.
            showfig: If True, display the figure, by default True.
        """
        surveys_to_process = []
        for survey in self.station_session.campaign_meta.surveys:
            if survey.id == survey_id or survey_id is None:
                surveys_to_process.append((survey.id, survey.type.value))

        for sid, survey_type in surveys_to_process:
            try:
                self.set_survey(sid)
                self._plot_ts_results(
                    survey_id=sid,
                    survey_type=survey_type,
                    run_id=run_id,
                    res_filter=res_filter,
                    savefig=savefig,
                    showfig=showfig,
                )
            except Exception as e:
                logger.warning(f"Skipping plotting for survey {survey_id}: {e}")
                continue

    def _plot_ts_results(
        self,
        survey_id: str,
        survey_type: str = None,
        run_id: int | str = 0,
        res_filter: float = 10,
        savefig: bool = False,
        showfig: bool = True,
        results_dir: Path | None = None,
    ) -> None:
        """
        Plots the time series results for a given survey.

        Args:
            survey_id: The ID of the survey to plot results for.
            survey_type: Optional survey type to include in the title.
            run_id: The GARPOS run ID to plot results for.
            res_filter: The residual filter value to apply.
            savefig: Whether to save the figure as a PNG file.
            showfig: Whether to display the figure.
        """

        results_dir: Path = (
            self.current_garpos_survey_dir.results if results_dir is None else results_dir
        )
        run_dir = results_dir / f"run_{run_id}"
        if not run_dir.exists():
            raise FileNotFoundError(f"Run directory {run_dir} does not exist.")

        try:
            garpos_results = GarposInput.from_datafile(self._load_results_file(run_dir))
        except FileNotFoundError as e:
            logger.warning(str(e))
            return

        array_enu = garpos_results.array_center_enu
        array_dpos = garpos_results.delta_center_position
        if array_enu is None or array_dpos is None:
            raise ValueError("Array center or delta position not found in GARPOS results.")

        array_final_position = array_dpos.model_copy()
        array_final_position.east += array_enu.east
        array_final_position.north += array_enu.north
        array_final_position.up += array_enu.up

        results_df_raw = pd.read_csv(garpos_results.shot_data)
        results_df_raw = ObservationData.validate(results_df_raw, lazy=True)
        results_df_raw["time"] = results_df_raw.ST.apply(lambda x: datetime.fromtimestamp(x, tz=UTC))
        df_filter_1 = results_df_raw["ResiRange"].abs() < res_filter
        df_filter_2 = results_df_raw["flag"].eq(False)
        results_df = results_df_raw[df_filter_1 & df_filter_2]
        # Use raw IDs so we allocate plot space for every transponder present,
        # even if a transponder has no points after filtering.
        unique_ids = results_df_raw["MT"].unique()

        # Build a plot plan so we don't create empty (extra) subplots.
        # Always include the unfiltered plot when raw data exists; include the
        # filtered plot only when there are points after filtering.
        plot_plan: list[tuple[str, str]] = []
        for unique_id in unique_ids:
            df_raw_transponder = results_df_raw[results_df_raw["MT"] == unique_id]
            if not df_raw_transponder.empty:
                plot_plan.append((unique_id, "unfiltered"))
            df_filtered_transponder = results_df[results_df["MT"] == unique_id]
            if not df_filtered_transponder.empty:
                plot_plan.append((unique_id, "filtered"))

        # Number of time-series subplot rows (each entry in plot_plan is one row)
        total_rows = len(plot_plan)

        # Dynamic figure sizing:
        # - ~1 inch per time-series subplot row.
        # - fixed extra inches for map/box/hist panels, spacer, and top text.
        # Slightly > 1 inch per plot row to leave room for titles.
        ts_row_height_in = 1.2
        extra_height_in = 8.0
        spacer_rows = 2  # gap between last time-series x ticks and lower panels
        lower_panel_rows = 6  # box (3) + hist (3), map shares these rows on the right
        min_extra_rows = spacer_rows + lower_panel_rows
        extra_rows = max(int(np.ceil(extra_height_in / ts_row_height_in)), min_extra_rows)
        total_height = (total_rows + extra_rows) * ts_row_height_in

        plt.figure(figsize=(20, total_height))
        title = f"{self.station_session.station_name}"
        if survey_type is not None:
            title += f" {survey_type}"
        title += f" Survey {survey_id} Results"
        plt.suptitle(title, x=0.6, y=0.96, fontsize=16)  # Move title higher up
        # GridSpec: with the figure height above, 1 row ~= 1 inch.
        gs = gridspec.GridSpec(total_rows + extra_rows, 16, hspace=1.35, wspace=0.35)

        # Adjust subplot parameters to add more space at the top
        plt.subplots_adjust(top=0.90, left=0.04, right=0.99, bottom=0.06)

        dpos_std = array_dpos.get_std_dev()
        dpos = array_dpos.get_position()
        figure_text = f"Array Final Position: East {array_final_position.east:.4f} m, North {array_final_position.north:.4f} m, Up {array_final_position.up:.4f} m\n"
        figure_text += f" Sig East {dpos_std[0]:.2f} m  Sig North {dpos_std[1]:.2f} m  Sig Up {dpos_std[2]:.2f} m \n"
        figure_text += f"Array Delta Position :  East {dpos[0]:.3f} m, North {dpos[1]:.3f} m, Up {dpos[2]:.3f} m \n"
        for _, transponder in enumerate(garpos_results.transponders):
            try:
                dpos = transponder.position_enu.get_position()
                figure_text += f"TSP {transponder.id} : East {dpos[0]:.3f} m, North {dpos[1]:.3f} m, Up {dpos[2]:.3f} m \n"
            except ValueError:
                figure_text += f"TSP {transponder.id} : No results found\n"

        print(figure_text)

        lower_start = total_rows + spacer_rows

        """
            Plot the waveglider track and transponder positions
            """
        # Make the ENU track plot larger: more columns and a bit more height.
        ax3 = plt.subplot(gs[lower_start : (lower_start + 6), 9:])
        ax3.set_aspect("equal", "box")
        ax3.set_xlabel("East (m)")
        ax3.set_ylabel("North (m)", labelpad=-1)
        colormap_times = results_df_raw.ST.to_numpy()
        colormap_times_scaled = (colormap_times - colormap_times.min()) / 3600
        norm = Normalize(
            vmin=0,
            vmax=(colormap_times.max() - colormap_times.min()) / 3600,
        )
        sc = ax3.scatter(
            results_df_raw["ant_e0"],
            results_df_raw["ant_n0"],
            c=colormap_times_scaled,
            cmap="viridis",
            label="Vessel",
            norm=norm,
            alpha=0.25,
        )
        ax3.scatter(0, 0, label="Origin", color="magenta", s=100)

        """
        Plot the time series of residuals - separate plot for each transponder
        """

        # Color mapping per transponder ID
        id_colors = {uid: colors[idx % len(colors)] for idx, uid in enumerate(unique_ids)}

        # Plot separate unfiltered/filtered plots based on plot_plan
        shared_ax = None
        for row_idx, (unique_id, kind) in enumerate(plot_plan):
            if shared_ax is None:
                ax_ts = plt.subplot(gs[row_idx : row_idx + 1, 1:14])
                shared_ax = ax_ts
            else:
                ax_ts = plt.subplot(gs[row_idx : row_idx + 1, 1:14], sharex=shared_ax)

            if kind == "unfiltered":
                df_ts = results_df_raw[results_df_raw["MT"] == unique_id].sort_values("time")
                title_ts = f"Transponder {unique_id} - Unfiltered Data"
                label_ts = f"{unique_id} Unfiltered"
            else:
                df_ts = results_df[results_df["MT"] == unique_id].sort_values("time")
                title_ts = f"Transponder {unique_id} - Filtered Data (|residuals| < {res_filter}m, flag=False)"
                label_ts = f"{unique_id} Filtered"

            # ax_ts.plot(
            #     df_ts["time"],
            #     df_ts["ResiRange"].abs(),
            #     label=label_ts,
            #     color=id_colors.get(unique_id, "black"),
            #     linewidth=1,
            #     alpha=0.85,
            # )
            ax_ts.scatter(
                df_ts["time"],
                df_ts["ResiRange"].abs(),
                label=label_ts,
                c=id_colors.get(unique_id, "black"),
                linewidths=3,
            )
            ax_ts.set_title(title_ts, fontsize=11, pad=6)
            ax_ts.legend(loc="upper right")
            ax_ts.grid(True, alpha=0.3)

            # Hide datetime ticks on all but the bottom-most time-series plot
            if row_idx < (len(plot_plan) - 1):
                ax_ts.tick_params(
                    axis="x",
                    which="both",
                    bottom=False,
                    top=False,
                    labelbottom=False,
                    labeltop=False,
                )
            else:
                ax_ts.xaxis.set_major_formatter(mdates.DateFormatter("%m-%d %H"))
                ax_ts.xaxis.set_major_locator(mdates.HourLocator(interval=6))
                ax_ts.set_xlabel("Time - Month / Day / Hour")
                plt.setp(ax_ts.xaxis.get_majorticklabels(), rotation=45, ha="right")

        # Create a y-label subplot on the left side
        ax_ylabel = plt.subplot(gs[:total_rows, 0])
        ax_ylabel.text(
            0.5,
            0.5,
            "Range-Residuals (m)",
            rotation=90,
            va="center",
            ha="center",
            fontsize=14,
            weight="bold",
            transform=ax_ylabel.transAxes,
        )
        ax_ylabel.axis("off")  # Hide the axes

        for transponder in garpos_results.transponders:
            try:
                idx = unique_ids.tolist().index(transponder.id)
                ax3.scatter(
                    transponder.position_enu.east,
                    transponder.position_enu.north,
                    label=f"{transponder.id}",
                    color=colors[idx % len(colors)],
                    s=100,
                )
            except ValueError as e:
                logger.warning(
                    f"Transponder {transponder.id} not found in results, skipping plotting. {e}"
                )
        plt.colorbar(sc, label="Time (hr)", norm=norm)
        ax3.legend()

        """
            Plot the residual range boxplot and histogram
            """
        ax2 = plt.subplot(gs[lower_start : (lower_start + 3), :9])
        resiRange = results_df_raw["ResiRange"].abs()

        resiRange_np = resiRange.to_numpy()
        resiRange_filter = np.abs(resiRange_np) < 50
        resiRange = resiRange[resiRange_filter]
        max_value = resiRange.max()
        flier_props = dict(marker=".", markerfacecolor="r", markersize=5, alpha=0.25)
        ax2.boxplot(resiRange.to_numpy(), vert=False, flierprops=flier_props)
        # keep axis plot limit slightly larger than max value for visibility
        ax2.set_xlim(0, max_value * 1.1)
        median = resiRange.median()
        q1 = resiRange.quantile(0.25)
        q3 = resiRange.quantile(0.75)
        ax2.text(
            0.5,
            1.2,
            f"Median: {median:.2f} , IQR 1: {q1:.2f}, IQR 3: {q3:.2f}",
            fontsize=10,
            verticalalignment="center",
            horizontalalignment="center",
        )
        ax2.set_xlabel("Residual Range (m)", labelpad=-1)
        ax2.yaxis.set_visible(False)
        ax2.set_title("Box Plot of Residual Range Values")
        bins = np.arange(0, res_filter, 0.05)
        counts, bins = np.histogram(resiRange_np, bins=bins, density=True)
        ax4 = plt.subplot(gs[(lower_start + 3) : (lower_start + 6), :9])
        ax4.sharex(ax2)
        ax4.hist(bins[:-1], bins, weights=counts, edgecolor="black")
        ax4.axvline(median, color="blue", linestyle="-", label=f"Median: {median:.3f}")
        ax4.set_xlabel("Residual Range (m)", labelpad=-1)
        ax4.set_ylabel("Frequency")
        ax4.set_title(f"Histogram of Residual Range Values, within {res_filter:.1f} meters")
        ax4.legend()
        # add figure text
        plt.gcf().text(0.02, 0.98, figure_text, fontsize=9, ha="left", va="top")

        # Avoid tight_layout() here; it tends to compress the GridSpec time-series
        # area when there are only a few transponders.

        if showfig:
            plt.show()
        fig_path = run_dir / f"_{run_id}_results.png"

        if savefig:
            logger.info(f"Saving figure to {fig_path}")
            plt.savefig(
                fig_path,
                dpi=300,
                bbox_inches="tight",
                pad_inches=0.1,
            )
