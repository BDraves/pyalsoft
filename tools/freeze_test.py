"""Build and run a one-file PyInstaller application using PyALSoft."""

from __future__ import annotations

import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
AUDIO_FIXTURES = ROOT / "tests" / "fixtures" / "audio"


def _smoke_command(executable: Path) -> list[Path]:
    return [executable, AUDIO_FIXTURES]


def main() -> None:
    import PyInstaller.__main__  # type: ignore[import-untyped]

    with tempfile.TemporaryDirectory(prefix="pyalsoft-freeze-") as temporary:
        work = Path(temporary)
        PyInstaller.__main__.run(
            [
                "--clean",
                "--onefile",
                "--name",
                "pyalsoft-freeze-test",
                "--distpath",
                str(work / "dist"),
                "--workpath",
                str(work / "build"),
                "--specpath",
                str(work),
                str(ROOT / "tools" / "smoke_test_runtime.py"),
            ]
        )
        executable = work / "dist" / "pyalsoft-freeze-test"
        if sys.platform == "win32":
            executable = executable.with_suffix(".exe")
        subprocess.run(_smoke_command(executable), check=True)


if __name__ == "__main__":
    main()
