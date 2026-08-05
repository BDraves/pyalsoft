"""Generate Python declarations from the vendored OpenAL XML registry.

The implementation lives in :mod:`tools.bindings`; this module remains the stable
script and import entry point used by contributors and CI.
"""

from __future__ import annotations

import sys
from pathlib import Path

if not __package__:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tools import bindings as _bindings  # noqa: E402

DEFAULT_DOCS_OUTPUT = _bindings.DEFAULT_DOCS_OUTPUT
DEFAULT_OUTPUT_DIR = _bindings.DEFAULT_OUTPUT_DIR
DEFAULT_README_OUTPUT = _bindings.DEFAULT_README_OUTPUT
DEFAULT_REGISTRY = _bindings.DEFAULT_REGISTRY
DEFAULT_SEMANTICS = _bindings.DEFAULT_SEMANTICS
DEFAULT_SOURCE = _bindings.DEFAULT_SOURCE
RegistryError = _bindings.RegistryError
_validate_supported_xml = _bindings._validate_supported_xml
load_semantic_overrides = _bindings.load_semantic_overrides
load_source_info = _bindings.load_source_info
parse_registry = _bindings.parse_registry
render_documentation = _bindings.render_documentation
render_outputs = _bindings.render_outputs
render_readme_badge = _bindings.render_readme_badge
verify_registry = _bindings.verify_registry
main = _bindings.main

__all__ = [
    "DEFAULT_DOCS_OUTPUT",
    "DEFAULT_OUTPUT_DIR",
    "DEFAULT_README_OUTPUT",
    "DEFAULT_REGISTRY",
    "DEFAULT_SEMANTICS",
    "DEFAULT_SOURCE",
    "RegistryError",
    "_validate_supported_xml",
    "load_semantic_overrides",
    "load_source_info",
    "main",
    "parse_registry",
    "render_documentation",
    "render_outputs",
    "render_readme_badge",
    "verify_registry",
]

if __name__ == "__main__":
    raise SystemExit(main())
