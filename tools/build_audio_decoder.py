"""Build the bundled decoder-only native helper."""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from pathlib import Path

if not __package__:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tools.audio_decoder import (  # noqa: E402
    VENDOR,
    DecoderTarget,
    decoder_target,
    staged_decoder_path,
    verify_vendored_sources,
)
from tools.openal_soft import ROOT  # noqa: E402


def _built_library(build_directory: Path, target: DecoderTarget) -> Path:
    candidates = sorted(
        path
        for path in build_directory.rglob(target.output_name)
        if path.is_file() and not path.is_symlink()
    )
    if not candidates:
        raise FileNotFoundError(
            f"audio decoder build did not produce {target.output_name!r}"
        )
    return candidates[-1]


def build_decoder(target: DecoderTarget | None = None) -> Path:
    """Build and stage the matching decoder helper, returning its path."""

    selected = target or decoder_target()
    verify_vendored_sources()
    generator_options: list[str] = []
    build_suffix = ""
    if (
        sys.platform == "win32"
        and shutil.which("cl") is None
        and shutil.which("clang") is not None
        and shutil.which("ninja") is not None
    ):
        generator_options = ["-G", "Ninja", "-DCMAKE_C_COMPILER=clang"]
        build_suffix = "-clang"
    build_directory = (
        ROOT / "build" / "audio-decoder" / f"{selected.identifier}{build_suffix}"
    )
    configure_command = [
        "cmake",
        "-S",
        str(VENDOR),
        "-B",
        str(build_directory),
        "-DCMAKE_BUILD_TYPE=Release",
        *generator_options,
    ]
    if sys.platform == "darwin":
        deployment_target = "11.0" if selected.identifier == "macos_arm64" else "10.13"
        configure_command.append(f"-DCMAKE_OSX_DEPLOYMENT_TARGET={deployment_target}")
    subprocess.run(configure_command, check=True)
    subprocess.run(
        [
            "cmake",
            "--build",
            str(build_directory),
            "--config",
            "Release",
            "--target",
            "pyalsoft_decoder",
            "--parallel",
            "2",
        ],
        check=True,
    )
    staged = staged_decoder_path(selected)
    staged.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(_built_library(build_directory, selected), staged)
    return staged


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--vendor-runtime",
        action="store_true",
        help="also copy the current runtime into the vendored platform directory",
    )
    arguments = parser.parse_args()
    path = build_decoder()
    if arguments.vendor_runtime:
        target = decoder_target()
        vendored = VENDOR / "runtime" / target.identifier / target.bundled_name
        vendored.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, vendored)
    try:
        displayed = path.relative_to(ROOT)
    except ValueError:
        displayed = path
    print(f"staged {displayed}")


if __name__ == "__main__":
    main()
