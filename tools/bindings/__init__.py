"""Structured implementation of the OpenAL binding generator."""

from .cli import main
from .config import load_semantic_overrides, load_source_info
from .models import RegistryError
from .outputs import render_outputs, render_readme_badge
from .paths import (
    DEFAULT_DOCS_OUTPUT,
    DEFAULT_OUTPUT_DIR,
    DEFAULT_README_OUTPUT,
    DEFAULT_REGISTRY,
    DEFAULT_SEMANTICS,
    DEFAULT_SOURCE,
)
from .registry import _validate_supported_xml, parse_registry, verify_registry
from .render_docs import render_documentation

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
