"""PyInstaller hook registration for PyALSoft."""

from __future__ import annotations

from pathlib import Path


def get_hook_dirs() -> list[str]:
    """Return PyALSoft's bundled PyInstaller hook directory."""

    return [str(Path(__file__).with_name("_pyinstaller_hooks"))]


__all__ = ["get_hook_dirs"]
