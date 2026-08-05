"""Build and run a one-file PyInstaller application using PyALSoft."""

from __future__ import annotations

import subprocess
import sys
import tempfile
from pathlib import Path

import PyInstaller.__main__  # type: ignore[import-untyped]

ROOT = Path(__file__).resolve().parents[1]


def main() -> None:
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
        subprocess.run([executable], check=True)


if __name__ == "__main__":
    main()
