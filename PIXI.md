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

This repo is configured to consume `../earthscope-sfg-tools` as an editable local dependency via Pixi.

If you clone this repository without the sibling `earthscope-sfg-tools` directory, update
`[tool.pixi.pypi-dependencies]` in `pyproject.toml` to use a published `earthscope-sfg-tools`
version instead.
