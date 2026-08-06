"""Platform-specific wheel assembly for the bundled OpenAL Soft runtime."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from typing import Any

from hatchling.builders.hooks.plugin.interface import BuildHookInterface

PROJECT_ROOT = Path(__file__).resolve().parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from tools.openal_soft import (  # noqa: E402
    native_runtime_root,
    runtime_target,
    source_configuration,
)


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
        staged = native_runtime_root(root) / runtime_id
        if runtime_id != "win_amd64":
            configuration = source_configuration({"library_source_sha256"})
            checksum = configuration["library_source_sha256"]
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

    try:
        target = runtime_target()
    except RuntimeError:
        return None
    return target.identifier, target.bundled_name, target.wheel_platform
