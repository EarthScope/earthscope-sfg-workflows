from earthscope_sfg_cli import commands, manifest

pipeline_manifest = manifest.PipelineManifest.from_json(
    "dev/NCC1-preproc-manifest.json"
)

commands.run_manifest(pipeline_manifest)