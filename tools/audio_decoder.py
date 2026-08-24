"""Shared configuration for the bundled static-audio decoder."""

from __future__ import annotations

import platform
import sys
import tomllib
from collections.abc import Collection
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path

from tools.openal_soft import ROOT, native_runtime_root, verify_checksum

VENDOR = ROOT / "vendor" / "audio-decoder"
CONFIG_PATH = VENDOR / "source.toml"
_SOURCE_FILES = (
    "tools/audio_decoder.py",
    "tools/build_audio_decoder.py",
    "vendor/audio-decoder/CMakeLists.txt",
    "vendor/audio-decoder/miniaudio.h",
    "vendor/audio-decoder/pyalsoft_decoder.c",
    "vendor/audio-decoder/pyalsoft_decoder.h",
    "vendor/audio-decoder/source.toml",
    "vendor/audio-decoder/stb_vorbis.c",
)


@dataclass(frozen=True)
class DecoderTarget:
    """Native decoder build and wheel naming for one platform."""

    identifier: str
    bundled_name: str
    output_name: str
    wheel_platform: str


def decoder_target(
    system: str = sys.platform,
    machine: str = platform.machine(),
) -> DecoderTarget:
    """Return decoder build and package settings for a supported platform."""

    normalized_machine = machine.casefold()
    if normalized_machine in {"amd64", "x86_64"}:
        architecture = "x86_64"
    elif normalized_machine in {"arm64", "aarch64"}:
        architecture = "arm64"
    else:
        raise RuntimeError(f"unsupported audio decoder architecture: {machine}")

    if system == "win32" and architecture == "x86_64":
        return DecoderTarget(
            "win_amd64", "pyalsoft_decoder.dll", "pyalsoft_decoder.dll", "win_amd64"
        )
    if system == "linux":
        wheel_architecture = "aarch64" if architecture == "arm64" else "x86_64"
        return DecoderTarget(
            f"linux_{wheel_architecture}",
            "libpyalsoft_decoder.so",
            "libpyalsoft_decoder.so",
            f"linux_{wheel_architecture}",
        )
    if system == "darwin":
        if architecture == "arm64":
            return DecoderTarget(
                "macos_arm64",
                "libpyalsoft_decoder.dylib",
                "libpyalsoft_decoder.dylib",
                "macosx_11_0_arm64",
            )
        return DecoderTarget(
            "macos_x86_64",
            "libpyalsoft_decoder.dylib",
            "libpyalsoft_decoder.dylib",
            "macosx_10_13_x86_64",
        )
    raise RuntimeError(f"unsupported audio decoder target: {system} {machine}")


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


def verify_vendored_sources() -> None:
    """Reject locally modified third-party decoder sources."""

    config = source_configuration({"miniaudio_sha256", "stb_vorbis_sha256"})
    for filename, key in (
        ("miniaudio.h", "miniaudio_sha256"),
        ("stb_vorbis.c", "stb_vorbis_sha256"),
    ):
        path = VENDOR / filename
        verify_checksum(str(path), path.read_bytes(), config[key])


def decoder_source_fingerprint() -> str:
    """Return a stable fingerprint for every input to the decoder library."""

    digest = sha256()
    for filename in _SOURCE_FILES:
        data = (ROOT / filename).read_bytes()
        digest.update(filename.encode("ascii"))
        digest.update(b"\0")
        digest.update(len(data).to_bytes(8, "big"))
        digest.update(data)
    return digest.hexdigest()


def staged_decoder_path(
    target: DecoderTarget | None = None, project_root: Path = ROOT
) -> Path:
    """Return the configured staging path for a decoder runtime."""

    selected = target or decoder_target()
    return (
        native_runtime_root(project_root)
        / selected.identifier
        / decoder_source_fingerprint()[:12]
        / selected.bundled_name
    )


def vendored_decoder_path(target: DecoderTarget | None = None) -> Path:
    """Return the source-addressed path for a checked-in decoder runtime."""

    selected = target or decoder_target()
    return (
        VENDOR
        / "runtime"
        / selected.identifier
        / decoder_source_fingerprint()[:12]
        / selected.bundled_name
    )
