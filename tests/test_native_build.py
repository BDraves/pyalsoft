"""Tests for native runtime targeting and freezer integration."""

from __future__ import annotations

from pathlib import Path

import pytest

from pyalsoft._pyinstaller import get_hook_dirs
from tools.openal_soft import runtime_target


@pytest.mark.parametrize(
    ("system", "machine", "identifier", "library_name", "wheel_platform"),
    [
        ("win32", "AMD64", "win_amd64", "soft_oal.dll", "win_amd64"),
        (
            "linux",
            "x86_64",
            "linux_x86_64",
            "libopenal.so.1",
            "linux_x86_64",
        ),
        (
            "linux",
            "aarch64",
            "linux_aarch64",
            "libopenal.so.1",
            "linux_aarch64",
        ),
        (
            "darwin",
            "x86_64",
            "macos_x86_64",
            "libopenal.1.dylib",
            "macosx_10_13_x86_64",
        ),
        (
            "darwin",
            "arm64",
            "macos_arm64",
            "libopenal.1.dylib",
            "macosx_11_0_arm64",
        ),
    ],
)
def test_runtime_target(
    system: str,
    machine: str,
    identifier: str,
    library_name: str,
    wheel_platform: str,
) -> None:
    target = runtime_target(system, machine)
    assert target.identifier == identifier
    assert target.bundled_name == library_name
    assert target.wheel_platform == wheel_platform


def test_unsupported_runtime_target_is_rejected() -> None:
    with pytest.raises(RuntimeError, match="unsupported OpenAL Soft architecture"):
        runtime_target("linux", "riscv64")


@pytest.mark.parametrize("machine", ["x86_64", "arm64"])
def test_macos_disables_fatal_function_effects_warning(machine: str) -> None:
    target = runtime_target("darwin", machine)
    assert "-DHAVE_WFUNCTION_EFFECTS=OFF" in target.cmake_options


def test_pyinstaller_hook_is_packaged() -> None:
    hook_directory = Path(get_hook_dirs()[0])
    hook = hook_directory / "hook-pyalsoft.py"
    assert hook.is_file()
    assert "lib*.so.*" in hook.read_text(encoding="utf-8")
