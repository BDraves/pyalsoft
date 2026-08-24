"""Build the bundled OpenAL Soft runtime from its pinned source archive."""

from __future__ import annotations

import argparse
import os
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
    source_archive_path,
    source_configuration,
    verify_checksum,
)  # noqa: E402

BUILD_JOBS_ENVIRONMENT = "PYALSOFT_BUILD_JOBS"
DEFAULT_BUILD_JOBS = 2


def _build_jobs(requested: int | None = None) -> int:
    """Return a validated, explicitly bounded native-build job count."""

    if requested is not None:
        if isinstance(requested, bool) or not isinstance(requested, int):
            raise TypeError("jobs must be an integer")
        if requested < 1:
            raise ValueError("jobs must be a positive integer")
        return requested

    configured = os.environ.get(BUILD_JOBS_ENVIRONMENT)
    if configured is None:
        return DEFAULT_BUILD_JOBS
    try:
        jobs = int(configured)
    except ValueError as error:
        raise ValueError(
            f"{BUILD_JOBS_ENVIRONMENT} must be a positive integer"
        ) from error
    if jobs < 1:
        raise ValueError(f"{BUILD_JOBS_ENVIRONMENT} must be a positive integer")
    return jobs


def _cmake_build_command(build_directory: Path, jobs: int) -> list[str]:
    """Return the native build command with an explicit parallelism bound."""

    return [
        "cmake",
        "--build",
        str(build_directory),
        "--config",
        "Release",
        "--target",
        "OpenAL",
        "--parallel",
        str(jobs),
    ]


def _source_tree(config: dict[str, str]) -> Path:
    archive = source_archive_path(config)
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


def build_runtime(
    target: RuntimeTarget | None = None,
    *,
    jobs: int | None = None,
) -> Path:
    """Stage the matching native runtime and return its path."""

    selected = target or runtime_target()
    native_root = native_runtime_root()

    if selected.identifier == "win_amd64":
        staged = native_root / selected.identifier / selected.bundled_name
        staged.parent.mkdir(parents=True, exist_ok=True)
        source = VENDOR / "runtime" / "win_amd64" / "soft_oal.dll"
        shutil.copy2(source, staged)
        return staged

    build_jobs = _build_jobs(jobs)

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
    subprocess.run(_cmake_build_command(build_directory, build_jobs), check=True)
    shutil.copy2(_built_library(build_directory, selected), staged)
    return staged


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--jobs",
        type=int,
        help=(
            "maximum concurrent compiler jobs "
            f"(default: {DEFAULT_BUILD_JOBS}; override with "
            f"{BUILD_JOBS_ENVIRONMENT})"
        ),
    )
    args = parser.parse_args()
    try:
        jobs = _build_jobs(args.jobs)
    except (TypeError, ValueError) as error:
        parser.error(str(error))
    path = build_runtime(jobs=jobs)
    try:
        displayed = path.relative_to(ROOT)
    except ValueError:
        displayed = path
    print(f"staged {displayed}")


if __name__ == "__main__":
    main()
