"""Platform-specific wheel assembly for the bundled OpenAL Soft runtime."""

from __future__ import annotations

import platform
import subprocess
import sys
import tomllib
from pathlib import Path
from typing import Any

from hatchling.builders.hooks.plugin.interface import BuildHookInterface


class CustomBuildHook(BuildHookInterface):
    """Add the matching native runtime to supported platform wheels."""

    def initialize(self, version: str, build_data: dict[str, Any]) -> None:
        specification = _runtime_specification()
        if specification is None:
            return

        runtime_id, library_name, wheel_platform = specification
        root = Path(self.root)
        vendored = (
            root / "vendor" / "openal-soft" / "runtime" / runtime_id / library_name
        )
        staged = root / "build" / "native" / runtime_id
        if runtime_id != "win_amd64":
            manifest = root / "vendor" / "openal-soft" / "source.toml"
            configuration = tomllib.loads(manifest.read_text(encoding="utf-8"))
            checksum = configuration["library_source_sha256"]
            if not isinstance(checksum, str):
                raise TypeError(f"{manifest}: library_source_sha256 must be a string")
            staged /= checksum[:12]
        staged /= library_name
        source = staged if staged.is_file() else vendored

        if not source.is_file() and version != "editable":
            subprocess.run(
                [sys.executable, str(root / "tools" / "build_openal_soft.py")],
                cwd=root,
                check=True,
            )
            source = staged
        if not source.is_file():
            return

        destination = f"pyalsoft/_native/{library_name}"
        build_data["force_include"][str(source)] = destination
        build_data["force_include_editable"][str(source)] = destination
        build_data["pure_python"] = False
        build_data["tag"] = f"py3-none-{wheel_platform}"


def _runtime_specification() -> tuple[str, str, str] | None:
    """Return the runtime directory, filename, and initial wheel platform tag."""

    machine = platform.machine().casefold()
    if machine in {"amd64", "x86_64"}:
        architecture = "x86_64"
    elif machine in {"arm64", "aarch64"}:
        architecture = "arm64"
    else:
        return None

    if sys.platform == "win32" and architecture == "x86_64":
        return "win_amd64", "soft_oal.dll", "win_amd64"
    if sys.platform == "linux":
        if architecture == "arm64":
            return "linux_aarch64", "libopenal.so.1", "linux_aarch64"
        return "linux_x86_64", "libopenal.so.1", "linux_x86_64"
    if sys.platform == "darwin":
        if architecture == "arm64":
            return "macos_arm64", "libopenal.1.dylib", "macosx_11_0_arm64"
        return "macos_x86_64", "libopenal.1.dylib", "macosx_10_13_x86_64"
    return None
