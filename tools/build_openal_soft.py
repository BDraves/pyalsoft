"""Build the bundled OpenAL Soft runtime from its pinned source archive."""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
import tarfile
from pathlib import Path

if not __package__:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tools.openal_soft import (
    ROOT,
    VENDOR,
    RuntimeTarget,
    native_runtime_root,
    runtime_target,
    source_configuration,
    verify_checksum,
)  # noqa: E402


def _source_tree(config: dict[str, str]) -> Path:
    archive = VENDOR / f"openal-soft-{config['version']}.tar.bz2"
    data = archive.read_bytes()
    expected = config["library_source_sha256"]
    verify_checksum(str(archive), data, expected)

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
    native_root = native_runtime_root()

    if selected.identifier == "win_amd64":
        staged = native_root / selected.identifier / selected.bundled_name
        staged.parent.mkdir(parents=True, exist_ok=True)
        source = VENDOR / "runtime" / "win_amd64" / "soft_oal.dll"
        shutil.copy2(source, staged)
        return staged

    config = source_configuration({"version", "library_source_sha256"})
    staged = (
        native_root
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
    try:
        displayed = path.relative_to(ROOT)
    except ValueError:
        displayed = path
    print(f"staged {displayed}")


if __name__ == "__main__":
    main()
