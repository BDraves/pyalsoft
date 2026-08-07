"""Shared OpenAL Soft manifest, integrity, and platform support."""

from __future__ import annotations

import hashlib
import os
import platform
import sys
import tomllib
from collections.abc import Collection
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
VENDOR = ROOT / "vendor" / "openal-soft"
CONFIG_PATH = VENDOR / "source.toml"
NATIVE_ROOT_ENVIRONMENT = "PYALSOFT_NATIVE_ROOT"


@dataclass(frozen=True)
class RuntimeTarget:
    """Native runtime build and wheel naming for one platform."""

    identifier: str
    bundled_name: str
    output_pattern: str | None
    cmake_options: tuple[str, ...] = ()
    wheel_platform: str = ""


def native_runtime_root(project_root: Path = ROOT) -> Path:
    """Return the directory used to stage platform-native runtimes."""

    configured = os.environ.get(NATIVE_ROOT_ENVIRONMENT)
    if configured is None:
        return project_root / "build" / "native"

    root = Path(configured)
    if not root.is_absolute():
        raise ValueError(f"{NATIVE_ROOT_ENVIRONMENT} must be an absolute path")
    return root


def source_configuration(required: Collection[str]) -> dict[str, str]:
    """Load and type-check selected keys from the pinned source manifest."""

    raw = tomllib.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    missing = set(required) - raw.keys()
    if missing:
        names = ", ".join(sorted(missing))
        raise ValueError(f"{CONFIG_PATH} is missing required keys: {names}")

    result: dict[str, str] = {}
    for key in required:
        value = raw[key]
        if not isinstance(value, str):
            raise TypeError(f"{CONFIG_PATH}: {key} must be a string")
        result[key] = value
    return result


def sha256(data: bytes) -> str:
    """Return the lowercase SHA-256 digest of *data*."""

    return hashlib.sha256(data).hexdigest()


def verify_checksum(label: str, data: bytes, expected: str) -> bytes:
    """Return *data* after verifying its expected SHA-256 digest."""

    actual = sha256(data)
    if actual != expected:
        raise ValueError(f"{label} SHA-256 mismatch: expected {expected}, got {actual}")
    return data


def runtime_target(
    system: str = sys.platform,
    machine: str = platform.machine(),
) -> RuntimeTarget:
    """Return build and package settings for a supported Python platform."""

    normalized_machine = machine.casefold()
    if normalized_machine in {"amd64", "x86_64"}:
        architecture = "x86_64"
    elif normalized_machine in {"arm64", "aarch64"}:
        architecture = "arm64"
    else:
        raise RuntimeError(f"unsupported OpenAL Soft architecture: {machine}")

    if system == "win32" and architecture == "x86_64":
        return RuntimeTarget(
            "win_amd64",
            "soft_oal.dll",
            None,
            wheel_platform="win_amd64",
        )
    if system == "linux":
        identifier = "linux_aarch64" if architecture == "arm64" else "linux_x86_64"
        return RuntimeTarget(
            identifier,
            "libopenal.so.1",
            "libopenal.so.*",
            (
                "-DALSOFT_REQUIRE_ALSA=ON",
                "-DALSOFT_BACKEND_PIPEWIRE=OFF",
                "-DALSOFT_STATIC_LIBGCC=ON",
                "-DALSOFT_STATIC_STDCXX=ON",
            ),
            f"linux_{'aarch64' if architecture == 'arm64' else 'x86_64'}",
        )
    if system == "darwin":
        identifier = "macos_arm64" if architecture == "arm64" else "macos_x86_64"
        deployment_target = "11.0" if architecture == "arm64" else "10.13"
        wheel_platform = (
            "macosx_11_0_arm64" if architecture == "arm64" else "macosx_10_13_x86_64"
        )
        return RuntimeTarget(
            identifier,
            "libopenal.1.dylib",
            "libopenal.*.dylib",
            (
                "-DALSOFT_REQUIRE_COREAUDIO=ON",
                # OpenAL Soft 1.25.2 makes this warning fatal on Apple Clang 17.
                # Remove after vendoring upstream commit 681d049c or newer.
                "-DHAVE_WFUNCTION_EFFECTS=OFF",
                f"-DCMAKE_OSX_DEPLOYMENT_TARGET={deployment_target}",
            ),
            wheel_platform,
        )
    raise RuntimeError(f"unsupported OpenAL Soft target: {system} {machine}")
