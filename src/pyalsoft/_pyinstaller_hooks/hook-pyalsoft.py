"""Collect PyALSoft's bundled OpenAL Soft and decoder runtimes."""

from PyInstaller.utils.hooks import collect_dynamic_libs  # type: ignore[import-untyped]

binaries = collect_dynamic_libs(
    "pyalsoft",
    search_patterns=["*.dll", "*.dylib", "lib*.so", "lib*.so.*"],
)
