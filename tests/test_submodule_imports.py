"""Smoke tests that import each subpackage to surface missing/broken imports."""

import importlib

import pytest

SUBMODULES = [
    "earthscope_sfg_workflows",
    "earthscope_sfg_workflows.logging",
    "earthscope_sfg_workflows.utils.command_line_utils",
    "earthscope_sfg_workflows.utils.model_update",
    "earthscope_sfg_workflows.utils.custom_warnings_exceptions",
    "earthscope_sfg_workflows.config",
    "earthscope_sfg_workflows.config.env_config",
    "earthscope_sfg_workflows.config.file_config",
    "earthscope_sfg_workflows.config.garpos_config",
    "earthscope_sfg_workflows.config.loadconfigs",
    "earthscope_sfg_workflows.config.shotdata_filters",
    "earthscope_sfg_workflows.data_mgmt",
    "earthscope_sfg_workflows.data_mgmt.utils",
    "earthscope_sfg_workflows.prefiltering",
    "earthscope_sfg_workflows.prefiltering.schemas",
    "earthscope_sfg_workflows.prefiltering.utils",
    "earthscope_sfg_workflows.modeling",
    "earthscope_sfg_workflows.modeling.garpos_tools.data_prep",
    "earthscope_sfg_workflows.modeling.garpos_tools.functions",
    "earthscope_sfg_workflows.modeling.garpos_tools.garpos_handler",
    "earthscope_sfg_workflows.modeling.garpos_tools.load_utils",
    "earthscope_sfg_workflows.modeling.garpos_tools.plotting",
    "earthscope_sfg_workflows.modeling.garpos_tools.schemas",
    "earthscope_sfg_workflows.pipelines",
    "earthscope_sfg_workflows.pipelines.config",
    "earthscope_sfg_workflows.pipelines.exceptions",
    "earthscope_sfg_workflows.pipelines.plotting",
    "earthscope_sfg_workflows.pipelines.qc_pipeline",
    "earthscope_sfg_workflows.pipelines.shotdata_gnss_refinement",
    "earthscope_sfg_workflows.pipelines.sv2_ops",
    "earthscope_sfg_workflows.pipelines.sv3_pipeline",
    "earthscope_sfg_workflows.services",
    "earthscope_sfg_workflows.services.ingest_service",
    "earthscope_sfg_workflows.services.layout_service",
    "earthscope_sfg_workflows.services.pipeline_service",
    "earthscope_sfg_workflows.services.sync_service",
    "earthscope_sfg_workflows.workflows",
    "earthscope_sfg_workflows.workflows.workflow_handler",
]


@pytest.mark.parametrize("module", SUBMODULES)
def test_submodule_imports(module: str) -> None:
    importlib.import_module(module)
