"""Tests for native runtime targeting and freezer integration."""

from __future__ import annotations

from pathlib import Path

import pytest

from pyalsoft._pyinstaller import get_hook_dirs
from tools.build_openal_soft import build_runtime
from tools.openal_soft import (
    NATIVE_ROOT_ENVIRONMENT,
    native_runtime_root,
    runtime_target,
)


def test_native_runtime_root_defaults_to_project_build_directory(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.delenv(NATIVE_ROOT_ENVIRONMENT, raising=False)
    assert native_runtime_root(tmp_path) == tmp_path / "build" / "native"


def test_native_runtime_root_can_be_configured(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    configured = tmp_path / "native-cache"
    monkeypatch.setenv(NATIVE_ROOT_ENVIRONMENT, str(configured))
    assert native_runtime_root(tmp_path / "project") == configured


def test_native_runtime_root_rejects_relative_override(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(NATIVE_ROOT_ENVIRONMENT, "relative/native-cache")
    with pytest.raises(ValueError, match="must be an absolute path"):
        native_runtime_root()


def test_build_runtime_uses_configured_native_root(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    configured = tmp_path / "native-cache"
    monkeypatch.setenv(NATIVE_ROOT_ENVIRONMENT, str(configured))

    path = build_runtime(runtime_target("win32", "AMD64"))

    assert path == configured / "win_amd64" / "soft_oal.dll"
    assert path.is_file()


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
