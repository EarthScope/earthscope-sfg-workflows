# EarthScope Seafloor Geodesy Workflows

`earthscope-sfg-workflows` owns **workflow orchestration and data management**
for Seafloor Geodesy pipelines.

## Repository responsibility

This repository is responsible for:

- Data discovery/ingestion workflows
- Asset catalog and directory/workspace management
- Preprocess/midprocess/model orchestration
- Campaign/station/network-level execution and lifecycle control

This repository should depend on `earthscope-sfg-tools` for parsing, file
conversion, and core data models.

## Relationship with sibling repositories

- `earthscope-sfg-tools`: parsing + low-level processing primitives
- `earthscope-sfg-workflows`: orchestration + data management + pipelines
- `es_sfgtools` (legacy monorepo): source of code being split into the two
	repositories above

## Migration status

See `MIGRATION_PLAN.md` for the detailed migration matrix, import rewrite rules,
and phased execution plan.

## Development (Pixi)

This repo uses Pixi workspace configuration compatible with `earthscope-sfg-tools`.

```bash
pixi install
pixi run lint
pixi run test
```

See `PIXI.md` for details.