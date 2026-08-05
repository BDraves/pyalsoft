"""Assemble, check, and write generated artifacts."""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from urllib.parse import quote

from .config import load_semantic_overrides
from .models import Registry, SemanticOverrides, SourceInfo
from .render_api import (
    render_enums,
    render_extensions,
    render_objects,
    render_python_commands,
    render_semantics,
)
from .render_ctypes import (
    render_constants,
    render_ctypes_functions,
    render_ctypes_types,
)
from .render_docs import render_registry_metadata

_OPENAL_BADGE_START = "<!-- openal-soft-version-badge:start -->"
_OPENAL_BADGE_END = "<!-- openal-soft-version-badge:end -->"


def render_outputs(
    registry: Registry,
    source: SourceInfo,
    digest: str,
    overrides: SemanticOverrides | None = None,
) -> dict[str, str]:
    """Render every generated file without changing the working tree."""

    if overrides is None:
        overrides = load_semantic_overrides()
    return {
        "constants.py": render_constants(registry, source, digest),
        "commands.py": render_python_commands(registry, source, digest, overrides),
        "enums.py": render_enums(registry, source, digest, overrides),
        "extensions.py": render_extensions(registry, source, digest),
        "types.py": render_ctypes_types(registry, source, digest),
        "functions.py": render_ctypes_functions(registry, source, digest),
        "objects.py": render_objects(registry, source, digest, overrides),
        "registry.py": render_registry_metadata(registry, source, digest),
        "semantics.py": render_semantics(registry, source, digest, overrides),
    }


def render_readme_badge(readme: str, source: SourceInfo) -> str:
    """Update the generated OpenAL Soft version badge in a README."""

    if readme.count(_OPENAL_BADGE_START) != 1 or readme.count(_OPENAL_BADGE_END) != 1:
        raise ValueError("README must contain exactly one OpenAL Soft badge block")

    start = readme.index(_OPENAL_BADGE_START)
    end = readme.index(_OPENAL_BADGE_END, start)
    if end < start:
        raise ValueError("README OpenAL Soft badge block is malformed")

    badge_version = quote(source.version.replace("-", "--"), safe=".")
    release_version = quote(source.version, safe=".")
    block = "\n".join(
        (
            _OPENAL_BADGE_START,
            f"[![OpenAL Soft {source.version}]"
            f"(https://img.shields.io/badge/OpenAL_Soft-{badge_version}-557C94)]"
            f"(https://github.com/kcat/openal-soft/releases/tag/{release_version})",
            _OPENAL_BADGE_END,
        )
    )
    return readme[:start] + block + readme[end + len(_OPENAL_BADGE_END) :]


def _check_outputs(output_dir: Path, outputs: Mapping[str, str]) -> bool:
    stale: list[str] = []
    for name, expected in outputs.items():
        path = output_dir / name
        if not path.is_file() or path.read_text(encoding="utf-8") != expected:
            stale.append(name)
    if stale:
        print("Generated files are missing or stale:")
        for name in stale:
            print(f"  {output_dir / name}")
        return False
    return True


def _write_outputs(output_dir: Path, outputs: Mapping[str, str]) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    for name, content in outputs.items():
        path = output_dir / name
        path.write_text(content, encoding="utf-8", newline="\n")
        print(f"Wrote {path}")


def _check_file(path: Path, expected: str) -> bool:
    if path.is_file() and path.read_text(encoding="utf-8") == expected:
        return True
    print(f"Generated file is missing or stale:\n  {path}")
    return False


def _write_file(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8", newline="\n")
    print(f"Wrote {path}")
