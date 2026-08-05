"""Repository paths used by the binding generator."""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_REGISTRY = ROOT / "vendor" / "openal-soft" / "al.xml"
DEFAULT_SOURCE = ROOT / "vendor" / "openal-soft" / "source.toml"
DEFAULT_SEMANTICS = ROOT / "tools" / "semantic_overrides.toml"
DEFAULT_OUTPUT_DIR = ROOT / "src" / "pyalsoft" / "bindings" / "_generated"
DEFAULT_DOCS_OUTPUT = ROOT / "docs" / "reference.md"
DEFAULT_README_OUTPUT = ROOT / "README.md"
