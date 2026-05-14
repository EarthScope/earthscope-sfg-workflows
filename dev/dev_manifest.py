from earthscope_sfg_cli import commands, manifest

if __name__ == "__main__":
    pipeline_manifest = manifest.PipelineManifest.load(
        "dev/NCC1-preproc-manifest.json"
    )

    commands.run_manifest(pipeline_manifest)