"""Shared GNSS→RINEX→KIN pipeline base class.

Both :class:`~.sv3_pipeline.SV3Pipeline` and
:class:`~.qc_pipeline.QCPipeline` inherit from
:class:`GnssRinexPipelineBase` to share the common scope-setup,
RINEX generation, PRIDE-PPP processing, and KIN-to-dataframe steps.
Subclasses provide the pipeline-specific TileDB arrays and configuration
through abstract properties, and can customise behaviour through hook methods.
"""

from __future__ import annotations

import datetime
import json
import sys
from abc import ABC, abstractmethod
from pathlib import Path

from pride_ppp import PrideProcessor, ProcessingMode, kin_to_kin_position_df, rinex_get_time_range
from tqdm.auto import tqdm

from earthscope_sfg_tools.novatel_tools.utils import get_metadata, get_metadatav2
from earthscope_sfg_tools.tiledb_integration import TDBKinPositionArray, tile2rinex

from earthscope_sfg_workflows.logging import ProcessLogger, change_all_logger_dirs

from ...data_mgmt.model import AssetEntry, AssetKind
from ..base import validate_network_station_campaign
from .config import PrideConfig, RinexConfig
from .exceptions import NoKinFound, NoLocalData, NoRinexBuilt, NoRinexFound


class GnssRinexPipelineBase:
    """Abstract base class providing shared GNSS→RINEX→KIN processing steps.

    Subclasses must implement the abstract properties and methods below to
    wire up their pipeline-specific TileDB arrays and configuration.  All
    public pipeline methods (``set_network_station_campaign``,
    ``get_rinex_files``, ``process_rinex``, ``process_kin``) are provided
    here and delegate to those abstract properties/hooks.
    """

    def _build_rinex_meta(self) -> None:
        """Create RINEX metadata JSON files for the current campaign if absent.

        Writes ``rinex_metav2.json`` and ``rinex_metav1.json`` into
        ``<campaign_root>/metadata/`` and updates the rinex config's
        ``settings_path`` to point at the v2 file.
        """
        meta_dir = self.workspace.campaign_layout().root / "metadata"
        meta_dir.mkdir(parents=True, exist_ok=True)
        rinex_metav2 = meta_dir / "rinex_metav2.json"
        rinex_metav1 = meta_dir / "rinex_metav1.json"

        if not rinex_metav2.exists():
            with open(rinex_metav2, "w") as f:
                json.dump(get_metadatav2(site=self.current_station_name), f)

        if not rinex_metav1.exists():
            with open(rinex_metav1, "w") as f:
                json.dump(get_metadata(site=self.current_station_name), f)

        self._rinex_config.settings_path = rinex_metav2

    @validate_network_station_campaign
    def get_rinex_files(self) -> None:
        """Generate and catalog daily RINEX files from the GNSS observation TileDB array.

        Uses :attr:`_gnss_obs_uri` as the source TileDB, and
        :attr:`_rinex_merge_label` to distinguish SV3 from QC merge records.
        After each file is created, :meth:`_on_rinex_path` is called (useful
        for per-file QC; no-op by default).

        Raises:
            NoRinexBuilt: If ``tile2rinex`` produces no files.
        """
        rinex_cfg = self._rinex_config
        rinex_dest = self.workspace.campaign_layout().intermediate

        year = (
            rinex_cfg.processing_year
            if rinex_cfg.processing_year != -1
            else int(self.current_campaign_name.split("_")[0])
        )
        gnss_uri = self._gnss_obs_uri

        ProcessLogger.info(
            f"Generating RINEX files for {self.current_network_name} "
            f"{self.current_station_name} {year}. This may take a few minutes..."
        )

        parent_ids = (
            f"N-{self.current_network_name}"
            f"|ST-{self.current_station_name}"
            f"|SV-{self.current_campaign_name}"
            f"|TDB-{gnss_uri}"
            f"|YEAR-{year}"
            f"{self._rinex_merge_label}"
        )
        merge_signature = {
            "parent_type": AssetKind.GNSSOBSTDB.value,
            "child_type": AssetKind.RINEX2.value,
            "parent_ids": [parent_ids],
        }

        if rinex_cfg.override or not self.workspace.is_merge_complete(**merge_signature):
            try:
                rinex_paths: list[Path] = tile2rinex(
                    gnss_obs_tdb=gnss_uri,
                    settings=rinex_cfg.settings_path,
                    writedir=rinex_dest,
                    time_interval=rinex_cfg.time_interval,
                    processing_year=year,
                    modulo_millis=rinex_cfg.modulo_millis,
                )

                if not rinex_paths:
                    ProcessLogger.warning(
                        f"No RINEX files generated for "
                        f"{self.current_network_name} {self.current_station_name} {year}."
                    )
                    raise NoRinexBuilt("No RINEX files were built.")

                rinex_entries: list[AssetEntry] = []
                upload_count = 0
                scope = self.workspace.scope

                for rinex_path in rinex_paths:
                    self._on_rinex_path(rinex_path)
                    start, end = rinex_get_time_range(rinex_path)
                    entry = AssetEntry(
                        kind=AssetKind.RINEX2,
                        scope=scope,
                        local_path=rinex_path,
                        timestamp_data_start=start,
                        timestamp_data_end=end,
                        timestamp_created=datetime.datetime.now(tz=datetime.UTC),
                    )
                    persisted = self.workspace.add_or_update_asset(entry)
                    rinex_entries.append(persisted if persisted is not None else entry)
                    if persisted is not None:
                        upload_count += 1

                self.workspace.add_merge_job(**merge_signature)

                ProcessLogger.info(
                    f"Generated {len(rinex_entries)} RINEX files spanning "
                    f"{rinex_entries[0].timestamp_data_start} to "
                    f"{rinex_entries[-1].timestamp_data_end}"
                )
                ProcessLogger.debug(
                    f"Added {upload_count} out of {len(rinex_entries)} RINEX files to the catalog"
                )

            except NoRinexBuilt:
                raise

            except Exception as e:
                if (message := ProcessLogger.error(f"Error generating RINEX files: {e}")) is not None:
                    print(message)
                sys.exit(1)

        else:
            rinex_entries = self.workspace.local_assets(AssetKind.RINEX2)
            ProcessLogger.debug(
                f"RINEX already generated for {self.current_network_name}, "
                f"{self.current_station_name}, {year}. "
                f"Found {len(rinex_entries)} entries."
            )

    @validate_network_station_campaign
    def process_rinex(self) -> None:
        """Run PRIDE-PPP on RINEX files to generate KIN and residual files.

        Steps:
        1. Retrieves RINEX files needing processing from the asset catalog.
        2. Filters to entries with a local path.
        3. Runs ``PrideProcessor.process_batch`` to convert RINEX → KIN.
        4. Creates :class:`~...data_mgmt.model.AssetEntry` records for each
           KIN and residual file and adds them to the catalog.

        Raises:
            NoRinexFound: If no processable RINEX files are found.
        """
        pride_cfg = self._pride_config

        ProcessLogger.info(
            f"Running PRIDE-PPPAR on RINEX for {self.current_network_name} "
            f"{self.current_station_name} {self.current_campaign_name}. "
            "This may take a few minutes..."
        )

        pride_dir = self.workspace.pride_directory
        intermediate_dir = self.workspace.campaign_layout().intermediate

        rinex_entries: list[AssetEntry] = self.workspace.assets_to_process(
            parent_kind=AssetKind.RINEX2,
            child_kind=AssetKind.KIN,
            override=pride_cfg.override,
        )
        rinex_entries = [e for e in rinex_entries if e.local_path is not None]

        if not rinex_entries:
            msg = (
                f"No RINEX files found to process for "
                f"{self.current_network_name} {self.current_station_name} "
                f"{self.current_campaign_name}"
            )
            ProcessLogger.error(msg)
            raise NoRinexFound(msg)

        ProcessLogger.info(f"Found {len(rinex_entries)} RINEX files to process")

        processor = PrideProcessor(
            pride_dir=pride_dir,
            output_dir=intermediate_dir,
            mode=ProcessingMode.DEFAULT,
        )
        rinex_path_map = {e.local_path: e for e in rinex_entries}
        kin_count = res_count = upload_count = 0
        scope = self.workspace.scope

        for result in tqdm(
            processor.process_batch(
                [e.local_path for e in rinex_entries],
                max_workers=pride_cfg.n_processes,
                override=pride_cfg.override,
            ),
            desc=(
                f"Processing RINEX with PRIDE-PPPAR for "
                f"{self.current_network_name} {self.current_station_name} "
                f"{self.current_campaign_name} using {pride_cfg.n_processes} workers"
            ),
            total=len(rinex_entries),
        ):
            rinex_entry = rinex_path_map.get(result.rinex_path)
            if result.kin_path is not None:
                kin_count += 1
                rinex_entry = self.workspace.update_asset(rinex_entry, is_processed=True)
                kin_entry = AssetEntry(
                    kind=AssetKind.KIN,
                    scope=scope,
                    local_path=result.kin_path,
                    parent_id=rinex_entry.id,
                    timestamp_data_start=rinex_entry.timestamp_data_start,
                    timestamp_data_end=rinex_entry.timestamp_data_end,
                    timestamp_created=datetime.datetime.now(tz=datetime.UTC),
                )
                if self.workspace.add_or_update_asset(kin_entry):
                    upload_count += 1

            # Handle both attribute names used across pipeline versions
            res_path = getattr(result, "res_path", None) or getattr(result, "residual_path", None)
            if res_path is not None:
                res_count += 1
                res_entry = AssetEntry(
                    kind=AssetKind.KINRESIDUALS,
                    scope=scope,
                    local_path=res_path,
                    parent_id=rinex_entry.id,
                    timestamp_data_start=rinex_entry.timestamp_data_start,
                    timestamp_data_end=rinex_entry.timestamp_data_end,
                    timestamp_created=datetime.datetime.now(tz=datetime.UTC),
                )
                if self.workspace.add_or_update_asset(res_entry):
                    upload_count += 1

        ProcessLogger.info(
            f"Generated {kin_count} KIN files and {res_count} residual files from "
            f"{len(rinex_entries)} RINEX files, added {upload_count} to catalog"
        )

    @validate_network_station_campaign
    def process_kin(self) -> None:
        """Process KIN files to generate kinematic-position dataframes.

        Steps:
        1. Retrieves KIN files needing processing from the asset catalog.
        2. Converts each KIN file to a structured dataframe via
           ``kin_to_kin_position_df``.
        3. Writes the dataframe to :attr:`_kin_position_tdb`.
        4. Marks each file as processed in the asset catalog.

        Raises:
            NoKinFound: If no KIN files are found for the current context.
        """
        ProcessLogger.info(
            f"Looking for KIN files to process for {self.current_network_name} "
            f"{self.current_station_name} {self.current_campaign_name}"
        )

        kin_entries: list[AssetEntry] = self.workspace.assets_to_process(
            parent_kind=AssetKind.KIN,
            override=self._rinex_config.override,
        )
        if not kin_entries:
            msg = (
                f"No KIN files found to process for "
                f"{self.current_network_name} {self.current_station_name} "
                f"{self.current_campaign_name}"
            )
            ProcessLogger.info(msg)
            raise NoKinFound(msg)

        ProcessLogger.info(f"Found {len(kin_entries)} KIN files to process")

        processed_count = 0
        for entry in tqdm(kin_entries, desc="Processing KIN files"):
            try:
                df = kin_to_kin_position_df(entry.local_path)
                if df is not None:
                    processed_count += 1
                    self.workspace.update_asset(entry, is_processed=True)
                    self._kin_position_tdb.write_df(df)
            except Exception as e:
                ProcessLogger.error(f"Error processing {entry.local_path}: {e}")

        ProcessLogger.info(
            f"Generated {processed_count} KinPosition dataframes from {len(kin_entries)} KIN files"
        )
