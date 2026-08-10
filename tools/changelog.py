"""Extract one version's release notes from the project changelog."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CHANGELOG = ROOT / "CHANGELOG.md"


def extract_release_notes(changelog: str, version: str) -> str:
    """Return the non-empty Markdown section for ``version``."""
    heading_prefix = f"## {version} - "
    lines = changelog.splitlines()
    starts = [
        index for index, line in enumerate(lines) if line.startswith(heading_prefix)
    ]
    if not starts:
        raise ValueError(f"CHANGELOG.md has no section for version {version}")
    if len(starts) > 1:
        raise ValueError(f"CHANGELOG.md has multiple sections for version {version}")

    start = starts[0] + 1
    end = next(
        (index for index in range(start, len(lines)) if lines[index].startswith("## ")),
        len(lines),
    )
    notes = "\n".join(lines[start:end]).strip()
    if not notes:
        raise ValueError(f"CHANGELOG.md section for version {version} is empty")
    return f"{notes}\n"


def main() -> None:
    """Write one version's release notes to standard output."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("version", help="release version without the leading v")
    parser.add_argument(
        "--changelog",
        type=Path,
        default=DEFAULT_CHANGELOG,
        help="path to CHANGELOG.md",
    )
    args = parser.parse_args()

    try:
        notes = extract_release_notes(
            args.changelog.read_text(encoding="utf-8"), args.version
        )
    except (OSError, ValueError) as error:
        parser.error(str(error))
    sys.stdout.write(notes)


if __name__ == "__main__":
    main()
