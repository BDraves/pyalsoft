"""Build the bundled OpenAL Soft runtime from its pinned source archive."""

from __future__ import annotations

import argparse
import hashlib
import platform
import shutil
import subprocess
import sys
import tarfile
import tomllib
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
VENDOR = ROOT / "vendor" / "openal-soft"
CONFIG_PATH = VENDOR / "source.toml"


@dataclass(frozen=True)
class RuntimeTarget:
    """Native runtime build and package naming for one platform."""

    identifier: str
    bundled_name: str
    output_pattern: str | None
    cmake_options: tuple[str, ...] = ()


def runtime_target(
    system: str = sys.platform,
    machine: str = platform.machine(),
) -> RuntimeTarget:
    """Return the supported runtime target for a Python platform."""

    normalized_machine = machine.casefold()
    if normalized_machine in {"amd64", "x86_64"}:
        architecture = "x86_64"
    elif normalized_machine in {"arm64", "aarch64"}:
        architecture = "arm64"
    else:
        raise RuntimeError(f"unsupported OpenAL Soft architecture: {machine}")

    if system == "win32" and architecture == "x86_64":
        return RuntimeTarget("win_amd64", "soft_oal.dll", None)
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
        )
    if system == "darwin":
        identifier = "macos_arm64" if architecture == "arm64" else "macos_x86_64"
        deployment_target = "11.0" if architecture == "arm64" else "10.13"
        return RuntimeTarget(
            identifier,
            "libopenal.1.dylib",
            "libopenal.*.dylib",
            (
                "-DALSOFT_REQUIRE_COREAUDIO=ON",
                f"-DCMAKE_OSX_DEPLOYMENT_TARGET={deployment_target}",
            ),
        )
    raise RuntimeError(f"unsupported OpenAL Soft target: {system} {machine}")


def _configuration() -> dict[str, str]:
    raw = tomllib.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    required = {"version", "library_source_sha256"}
    missing = required - raw.keys()
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


def _source_tree(config: dict[str, str]) -> Path:
    archive = VENDOR / f"openal-soft-{config['version']}.tar.bz2"
    data = archive.read_bytes()
    actual = hashlib.sha256(data).hexdigest()
    expected = config["library_source_sha256"]
    if actual != expected:
        raise ValueError(
            f"{archive} SHA-256 mismatch: expected {expected}, got {actual}"
        )

    source_parent = ROOT / "build" / "openal-soft" / f"source-{expected[:12]}"
    source = source_parent / f"openal-soft-{config['version']}"
    if not (source / "CMakeLists.txt").is_file():
        source_parent.mkdir(parents=True, exist_ok=True)
        with tarfile.open(archive, mode="r:bz2") as bundle:
            bundle.extractall(source_parent, filter="data")
    if not (source / "CMakeLists.txt").is_file():
        raise ValueError(f"{archive} did not contain the expected source tree")
    return source


def _built_library(build_directory: Path, target: RuntimeTarget) -> Path:
    if target.output_pattern is None:
        raise AssertionError("a source build must define an output pattern")
    candidates = sorted(
        path
        for path in build_directory.rglob(target.output_pattern)
        if path.is_file() and not path.is_symlink()
    )
    if not candidates:
        raise FileNotFoundError(
            f"OpenAL Soft build did not produce {target.output_pattern!r}"
        )
    return candidates[-1]


def build_runtime(target: RuntimeTarget | None = None) -> Path:
    """Stage the matching native runtime and return its path."""

    selected = target or runtime_target()

    if selected.identifier == "win_amd64":
        staged = ROOT / "build" / "native" / selected.identifier / selected.bundled_name
        staged.parent.mkdir(parents=True, exist_ok=True)
        source = VENDOR / "runtime" / "win_amd64" / "soft_oal.dll"
        shutil.copy2(source, staged)
        return staged

    config = _configuration()
    staged = (
        ROOT
        / "build"
        / "native"
        / selected.identifier
        / config["library_source_sha256"][:12]
        / selected.bundled_name
    )
    staged.parent.mkdir(parents=True, exist_ok=True)
    source_tree = _source_tree(config)
    build_directory = (
        ROOT
        / "build"
        / "openal-soft"
        / f"{selected.identifier}-{config['library_source_sha256'][:12]}"
    )
    common_options = (
        "-DCMAKE_BUILD_TYPE=Release",
        "-DLIBTYPE=SHARED",
        "-DALSOFT_UTILS=OFF",
        "-DALSOFT_EXAMPLES=OFF",
        "-DALSOFT_TESTS=OFF",
        "-DALSOFT_INSTALL=OFF",
        "-DALSOFT_INSTALL_CONFIG=OFF",
        "-DALSOFT_INSTALL_HRTF_DATA=OFF",
        "-DALSOFT_INSTALL_AMBDEC_PRESETS=OFF",
        "-DALSOFT_UPDATE_BUILD_VERSION=OFF",
        "-DALSOFT_EMBED_HRTF_DATA=ON",
    )
    subprocess.run(
        [
            "cmake",
            "-S",
            str(source_tree),
            "-B",
            str(build_directory),
            *common_options,
            *selected.cmake_options,
        ],
        check=True,
    )
    subprocess.run(
        [
            "cmake",
            "--build",
            str(build_directory),
            "--config",
            "Release",
            "--target",
            "OpenAL",
            "--parallel",
        ],
        check=True,
    )
    shutil.copy2(_built_library(build_directory, selected), staged)
    return staged


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.parse_args()
    path = build_runtime()
    print(f"staged {path.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
