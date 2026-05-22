from __future__ import annotations

import dataclasses
import importlib
import inspect
import pkgutil
import sys
from pathlib import Path
from typing import Any, get_type_hints
from collections import defaultdict


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"

if SRC.exists():
    sys.path.insert(0, str(SRC))
else:
    sys.path.insert(0, str(ROOT))


PACKAGE = "earthscope_sfg_workflows"
API_DIR = ROOT / "docs" / "api"
API_INDEX = API_DIR / "index.md"

# If a module name matches a key in this dict, the value will be used as the section 
# title instead of a humanized version of the module name.
custom_titles = {
    "data_mgmt": "Data Management",
}

SKIP_METHOD_PREFIXES = (
    "_",
    "model_",
    "dict",
    "json",
    "copy",
    "schema",
)

SKIP_FIELD_PREFIXES = (
    "_",
    "__pydantic",
    "model_",
)


def fmt_annotation(value: Any) -> str:
    if value in (inspect.Signature.empty, None, ""):
        return ""

    if hasattr(value, "__name__"):
        return value.__name__

    text = str(value)
    return (
        text.replace("typing.", "")
        .replace("<class '", "")
        .replace("'>", "")
    )


def safe_signature(obj: Any) -> str:
    try:
        return str(inspect.signature(obj))
    except Exception:
        return "()"


def md_escape_table_cell(text: str) -> str:
    return str(text).replace("|", r"\|").replace("\n", " ")


def own_doc(obj: Any) -> str:
    raw = getattr(obj, "__doc__", None)

    if not raw:
        return "_No docstring._"

    cleaned = inspect.cleandoc(raw)

    if "A base class for creating Pydantic models" in cleaned:
        return "_No docstring._"

    if "__pydantic_core_schema__" in cleaned:
        return "_No docstring._"

    return cleaned


def should_skip_field(name: str) -> bool:
    return name.startswith(SKIP_FIELD_PREFIXES)


def should_skip_method(name: str) -> bool:
    return name.startswith(SKIP_METHOD_PREFIXES)


def module_filename(module_name: str) -> str:
    return f"{module_name}.md"


def write_fields(lines: list[str], cls: type) -> None:
    fields: list[tuple[str, str, str]] = []

    if hasattr(cls, "model_fields") and isinstance(cls.model_fields, dict):
        for name, field in cls.model_fields.items():
            if should_skip_field(name):
                continue

            annotation = fmt_annotation(getattr(field, "annotation", ""))
            description = getattr(field, "description", None) or ""
            fields.append((name, annotation, description))

    elif hasattr(cls, "__fields__") and isinstance(cls.__fields__, dict):
        for name, field in cls.__fields__.items():
            if should_skip_field(name):
                continue

            if isinstance(field, tuple):
                annotation = fmt_annotation(field[0]) if field else ""
                description = ""
            else:
                annotation = fmt_annotation(
                    getattr(field, "outer_type_", None)
                    or getattr(field, "annotation", "")
                )
                field_info = getattr(field, "field_info", None)
                description = getattr(field_info, "description", None) or ""

            fields.append((name, annotation, description))

    elif dataclasses.is_dataclass(cls):
        for field in dataclasses.fields(cls):
            if should_skip_field(field.name):
                continue

            fields.append((field.name, fmt_annotation(field.type), ""))

    else:
        try:
            hints = get_type_hints(cls)
        except Exception:
            hints = getattr(cls, "__annotations__", {})

        for name, annotation in hints.items():
            if should_skip_field(name):
                continue

            fields.append((name, fmt_annotation(annotation), ""))

    if not fields:
        return

    lines += [
        "",
        "**Fields**",
        "",
        "| Name | Type | Description |",
        "|---|---|---|",
    ]

    for name, annotation, description in fields:
        lines.append(
            "| `{}` | `{}` | {} |".format(
                md_escape_table_cell(name),
                md_escape_table_cell(annotation),
                md_escape_table_cell(description or ""),
            )
        )


def iter_package_modules(package_name: str):
    pkg = importlib.import_module(package_name)

    if hasattr(pkg, "__path__"):
        for modinfo in pkgutil.walk_packages(pkg.__path__, prefix=f"{package_name}."):
            if "._" in modinfo.name:
                continue

            try:
                yield importlib.import_module(modinfo.name)
            except Exception as exc:
                print(f"Skipping {modinfo.name}: {exc}", file=sys.stderr)
    else:
        yield pkg


def public_members_defined_in_module(module):
    members = []

    for name, obj in inspect.getmembers(module):
        if name.startswith("_"):
            continue

        if not (inspect.isfunction(obj) or inspect.isclass(obj)):
            continue

        if getattr(obj, "__module__", None) != module.__name__:
            continue

        members.append((name, obj))

    return members


def class_methods_defined_in_module(cls: type, module_name: str):
    methods = []

    for name, obj in inspect.getmembers(cls, inspect.isfunction):
        if should_skip_method(name):
            continue

        if getattr(obj, "__module__", None) != module_name:
            continue

        methods.append((name, obj))

    return methods


def render_module_page(module) -> list[str]:
    short_name = module.__name__.replace(f"{PACKAGE}.", "")
    file_title = short_name.split(".")[-1]

    lines = [
        f"# {file_title}",
        "",
        f"`{module.__name__}`",
        "",
        own_doc(module),
        "",
    ]

    members = public_members_defined_in_module(module)

    if not members:
        lines += ["_No public functions or classes found._", ""]
        return lines

    for name, obj in members:
        if inspect.isfunction(obj):
            lines += [
                f"## `{name}{safe_signature(obj)}`",
                "",
                own_doc(obj),
                "",
            ]

        elif inspect.isclass(obj):
            if not obj.__module__.startswith(PACKAGE):
                continue

            lines += [
                f"## class `{name}`",
                "",
                own_doc(obj),
            ]

            write_fields(lines, obj)

            methods = class_methods_defined_in_module(obj, module.__name__)

            if methods:
                lines += [
                    "",
                    "**Methods**",
                    "",
                ]

            for method_name, method in methods:
                lines += [
                    f"### `{name}.{method_name}{safe_signature(method)}`",
                    "",
                    own_doc(method),
                    "",
                ]

            lines.append("")

    return lines

def humanize_title(value: str) -> str:
    """Convert snake_case or module names into readable titles."""

    if value in custom_titles:
        return custom_titles[value]

    return value.replace("_", " ").title()


def write_myst_yml(api_toc_lines: list[str]) -> None:
    """Render myst.yml from myst.yml.template with the generated API TOC."""

    template_path = ROOT / "docs/myst.yml.template"
    output_path = ROOT / "myst.yml"

    template = template_path.read_text(encoding="utf-8")

    placeholder = "{{ API_TOC }}"

    if placeholder not in template:
        raise RuntimeError(
            f"{template_path} is missing the {placeholder} placeholder."
        )

    api_toc = "\n".join(api_toc_lines)

    output = template.replace(placeholder, api_toc)

    output_path.write_text(output, encoding="utf-8")


def main() -> None:
    """Generate API Markdown pages, an API index page, and a MyST TOC snippet."""

    # -------------------------------------------------------------------------
    # 1. Ensure the API output directory exists.
    #
    # Example output:
    #   docs/api/index.md
    #   docs/api/earthscope_sfg_workflows.datamodels.metadata.community.site.md
    # -------------------------------------------------------------------------
    API_DIR.mkdir(parents=True, exist_ok=True)

    # -------------------------------------------------------------------------
    # 2. Discover importable modules in the package.
    #
    # Uses:
    #   iter_package_modules(PACKAGE)
    #
    # This walks earthscope_sfg_workflows and imports each Python module so we can
    # inspect its public classes/functions.
    # -------------------------------------------------------------------------
    modules = list(iter_package_modules(PACKAGE))

    # -------------------------------------------------------------------------
    # 3. Render one Markdown page per module.
    #
    # Uses:
    #   render_module_page(module)
    #   module_filename(module.__name__)
    #
    # Skips modules that do not expose any public functions or classes.
    # -------------------------------------------------------------------------
    written_modules: list[tuple[str, str]] = []

    for module in modules:
        page_lines = render_module_page(module)

        if "_No public functions or classes found._" in page_lines:
            continue

        filename = module_filename(module.__name__)
        path = API_DIR / filename

        path.write_text("\n".join(page_lines), encoding="utf-8")
        written_modules.append((module.__name__, filename))

    # -------------------------------------------------------------------------
    # Group modules into:
    #
    #   datamodels
    #     community
    #       site
    #
    #     earthscope
    #       site
    #
    # This prevents duplicate file labels from colliding.
    # -------------------------------------------------------------------------
    grouped_modules: dict[str, dict[str, list[tuple[str, str]]]] = defaultdict(
        lambda: defaultdict(list)
    )

    for module_name, filename in written_modules:
        short_name = module_name.replace(f"{PACKAGE}.", "")
        parts = short_name.split(".")

        # Top-level package group
        # e.g. datamodels, novatel_tools
        group = parts[0]

        # File/module name
        # e.g. site.py -> site
        file_label = parts[-1]

        # Parent folder immediately above file
        #
        # Example:
        #   datamodels.metadata.community.site
        # -> subgroup = community
        #
        #   datamodels.metadata.earthscope.site
        # -> subgroup = earthscope
        #
        subgroup = parts[-2] if len(parts) > 2 else "general"

        grouped_modules[group][subgroup].append((file_label, filename))

    # -------------------------------------------------------------------------
    # 5. Generate docs/api/index.md.
    #
    # This is the visible API landing page. It contains normal Markdown links
    # grouped by top-level package.
    #
    # Note:
    #   Links are relative to docs/api/index.md, so they should be ./<stem>,
    #   not ./api/<stem>.
    # -------------------------------------------------------------------------
    index_lines = [
        "# Workflows API Reference",
        "",
        "Click a file below to open its API page.",
        "",
    ]

    for group in sorted(grouped_modules):
        index_lines += [
            f"## {humanize_title(group)}",
            "",
        ]

        for subgroup in sorted(grouped_modules[group]):
            index_lines += [
                f"### {humanize_title(subgroup)}",
                "",
            ]

            for label, filename in sorted(grouped_modules[group][subgroup]):
                stem = Path(filename).stem
                index_lines.append(f"- [{label}](./{stem})")

            index_lines.append("")

    API_INDEX.write_text("\n".join(index_lines), encoding="utf-8")

    print(f"Wrote {API_INDEX}")
    print(f"Wrote {len(written_modules)} module pages to {API_DIR}")

    # -------------------------------------------------------------------------
    # 6. Generate a MyST TOC snippet for sidebar navigation.
    #
    # Output:
    #   docs/api_toc.yml
    #
    # Paste this under project.toc in myst.yml, or use it as a reference.
    #
    # Important:
    #   MyST warnings recommend including .md extensions explicitly.
    # -------------------------------------------------------------------------
    toc_lines = [
    "    - title: API Reference",
    "      children:",
    "        - file: docs/api/index.md",
    ]

    for group in sorted(grouped_modules):
        toc_lines += [
            f"        - title: {humanize_title(group)}",
            "          children:",
        ]

        for subgroup in sorted(grouped_modules[group]):
            toc_lines += [
                f"            - title: {humanize_title(subgroup)}",
                "              children:",
            ]

            for label, filename in sorted(grouped_modules[group][subgroup]):
                stem = Path(filename).stem

                toc_lines.append(
                    f"                - file: docs/api/{stem}.md"
                )

    toc_path = ROOT / "docs" / "api_toc.yml"
    toc_path.write_text("\n".join(toc_lines), encoding="utf-8")
    print(f"Wrote {toc_path}")
    write_myst_yml(toc_lines)
    print("Wrote myst.yml from myst.yml.template")

    


if __name__ == "__main__":
    main()

    