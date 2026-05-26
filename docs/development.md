# Development

## Prerequisites

- [Pixi](https://pixi.sh) for environment and task management
- Git

---

## Setup

Clone the repo and install the default environment:

```bash
git clone https://github.com/EarthScope/earthscope-sfg-workflows.git
cd earthscope-sfg-workflows
pixi install
```

This installs Python, all dependencies, and dev tools into a Pixi-managed environment. No manual `pip install` or `conda create` needed.

### Split-repo setup

This repo is designed to sit alongside `earthscope-sfg-tools` as a sibling directory. If you have both checked out, Pixi will pick up `earthscope-sfg-tools` as a local editable dependency automatically via the workspace config.

If you only have this repo, update `[tool.pixi.pypi-dependencies]` in `pyproject.toml` to point at a published version of `earthscope-sfg-tools` instead.

### Bootstrap GARPOS and PRIDE-PPPAR

Some pipelines require GARPOS (Fortran) and PRIDE-PPPAR (compiled binaries). Build both with:

```bash
pixi run setup
```

This clones and compiles each tool into `.pixi/`. To verify the builds:

```bash
pixi run test-setup
```

---

## Environments

| Environment | Use case |
| --- | --- |
| `default` | Standard development |
| `geolab` | Development with Jupyter |
| `docs` | Building documentation |

```bash
pixi shell              # default environment
pixi shell -e geolab    # Jupyter environment
pixi shell -e docs      # docs environment
```

---

## Common tasks

```bash
pixi run lint           # ruff check src/ tests/
pixi run format         # ruff format src/ tests/
pixi run format-check   # ruff format --check src/ tests/
pixi run test           # pytest tests/ -v
```

Run a specific test file:

```bash
pixi run pytest tests/test_workflows_base_and_facades.py -v
```

---

## Docs

```bash
pixi run -e docs docs          # serve locally with live reload
pixi run -e docs docs-build    # build static HTML
```

API reference pages are auto-generated from docstrings by `scripts/generate_api_md.py`:

```bash
pixi run -e docs python scripts/generate_api_md.py
```

---

## Code style

- **Linter / formatter:** [Ruff](https://docs.astral.sh/ruff/), configured in `pyproject.toml`
- **Line length:** 100 characters
- **Docstrings:** [Google style](https://google.github.io/styleguide/pyguide.html#38-comments-and-docstrings)

A converter for normalizing legacy NumPy/reST docstrings is available at `dev/convert_docstrings.py`.

---

## Architecture notes

The data layer follows the **ports & adapters** pattern. When adding or changing data access:

- Define behavior in a port (`data_mgmt/ports.py`)
- Implement it in an adapter (`data_mgmt/adapters/`, `data_mgmt/catalog/`, etc.)
- Use the in-memory fake (`data_mgmt/adapters/memory.py`) for unit tests — no real I/O needed

See `plans/rfc-a-data-mgmt-ports-and-adapters.md` for design rationale.
