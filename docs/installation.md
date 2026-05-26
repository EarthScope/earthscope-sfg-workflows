# Installation

## Prerequisites

- Python 3.11+
- [Pixi](https://pixi.sh) (recommended) or pip

---

## Install with Pixi (recommended)

[Pixi](https://pixi.sh) manages the Python environment and all dependencies automatically.

```bash
git clone https://github.com/EarthScope/earthscope-sfg-workflows.git
cd earthscope-sfg-workflows
pixi install
```

This installs Python, all dependencies, and the package itself into an isolated Pixi-managed environment.

---

## Optional dependencies

Some pipelines require GARPOS (Fortran) and PRIDE-PPPAR (compiled binaries). If you installed with Pixi, build both with:

```bash
pixi run setup
```

See [Development](development.md) for full setup details including split-repo configuration.
