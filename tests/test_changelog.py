"""Tests for release-note extraction from CHANGELOG.md."""

import tomllib
from pathlib import Path
from typing import cast

import pytest

from tools.changelog import extract_release_notes

ROOT = Path(__file__).resolve().parents[1]


def test_project_version_has_release_notes() -> None:
    pyproject = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    version = cast(str, pyproject["project"]["version"])
    changelog = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")

    assert extract_release_notes(changelog, version)


def test_extract_release_notes() -> None:
    changelog = """\
# Changelog

## Unreleased

- Future change.

## 1.2.0 - 2026-08-10

### Added

- Released a useful feature.

## 1.1.0 - 2026-08-01

- Previous change.
"""

    assert extract_release_notes(changelog, "1.2.0") == (
        "### Added\n\n- Released a useful feature.\n"
    )


@pytest.mark.parametrize(
    ("changelog", "message"),
    [
        ("## Unreleased\n", "no section"),
        ("## 1.2.0 - 2026-08-10\n", "is empty"),
        (
            "## 1.2.0 - 2026-08-10\n\nFirst.\n## 1.2.0 - 2026-08-11\n\nSecond.\n",
            "multiple sections",
        ),
    ],
)
def test_extract_release_notes_rejects_invalid_section(
    changelog: str, message: str
) -> None:
    with pytest.raises(ValueError, match=message):
        extract_release_notes(changelog, "1.2.0")
