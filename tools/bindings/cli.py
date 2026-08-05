"""Command-line interface for binding generation."""

from __future__ import annotations

import argparse
from collections.abc import Sequence
from pathlib import Path

from .config import load_semantic_overrides, load_source_info
from .outputs import (
    _check_file,
    _check_outputs,
    _write_file,
    _write_outputs,
    render_outputs,
    render_readme_badge,
)
from .paths import (
    DEFAULT_DOCS_OUTPUT,
    DEFAULT_OUTPUT_DIR,
    DEFAULT_README_OUTPUT,
    DEFAULT_REGISTRY,
    DEFAULT_SEMANTICS,
    DEFAULT_SOURCE,
)
from .registry import parse_registry, verify_registry
from .render_docs import render_documentation


def _argument_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--registry", type=Path, default=DEFAULT_REGISTRY)
    parser.add_argument("--source", type=Path, default=DEFAULT_SOURCE)
    parser.add_argument("--semantics", type=Path, default=DEFAULT_SEMANTICS)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--docs-output", type=Path, default=DEFAULT_DOCS_OUTPUT)
    parser.add_argument("--readme-output", type=Path, default=DEFAULT_README_OUTPUT)
    parser.add_argument(
        "--check",
        action="store_true",
        help="fail if committed generated files differ from generator output",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Generate files or verify that the committed output is current."""

    arguments = _argument_parser().parse_args(argv)
    source = load_source_info(arguments.source)
    digest = verify_registry(arguments.registry, source)
    registry = parse_registry(arguments.registry)
    overrides = load_semantic_overrides(arguments.semantics)
    outputs = render_outputs(registry, source, digest, overrides)
    documentation = render_documentation(registry, source, digest, overrides)
    readme = render_readme_badge(
        arguments.readme_output.read_text(encoding="utf-8"), source
    )

    print(
        f"Parsed {len(registry.types)} types, {len(registry.enums)} enums, "
        f"{len(registry.commands)} commands, and "
        f"{len(registry.api_sets)} API sets from OpenAL Soft {source.version}."
    )
    if arguments.check:
        code_is_current = _check_outputs(arguments.output_dir, outputs)
        docs_are_current = _check_file(arguments.docs_output, documentation)
        readme_is_current = _check_file(arguments.readme_output, readme)
        return 0 if code_is_current and docs_are_current and readme_is_current else 1
    _write_outputs(arguments.output_dir, outputs)
    _write_file(arguments.docs_output, documentation)
    _write_file(arguments.readme_output, readme)
    return 0
