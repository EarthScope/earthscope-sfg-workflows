# PIXI Guide

This repository uses [pixi](https://pixi.sh) for environment and task management.

## Setup

From the repository root:

```bash
pixi install
```

## Environments

Configured environments:

- `default`
- `tiledb`
- `geolab`

Examples:

```bash
pixi shell
pixi shell -e tiledb
pixi shell -e geolab
```

## Common tasks

```bash
pixi task list
pixi run lint
pixi run format-check
pixi run format
pixi run test
```

## Split-repo development note

`earthscope-sfg-tools` and `pride-ppp` (from the GNSSommelier monorepo) are consumed as
git dependencies pinned to a tag/rev in `pyproject.toml` and resolved through `pixi.lock`
— no sibling checkout is required. When moving a pin, update every declaration and re-run
`pixi lock`:

- `earthscope-sfg-tools`: `[project.dependencies]` **and** `[tool.pixi.pypi-dependencies]`
- GNSSommelier: `pride-ppp` in `[project.dependencies]`, plus `gpm-specs` and
  `gnss-product-management` in `[tool.pixi.pypi-dependencies]` — all three must share one
  rev, or `pixi install --locked` (enforced in CI) rejects the lockfile.

To develop against a local checkout of either dependency, install it into the pixi env
over the locked version:

```bash
pixi run -- uv pip install --python "$(command -v python)" --no-deps --force-reinstall ../earthscope-sfg-tools
```

Restore the locked env afterwards with `rm -rf .pixi/envs && pixi install`.
