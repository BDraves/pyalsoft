"""Integrity tests for the vendored OpenAL Soft runtime."""

from __future__ import annotations

import hashlib
import tomllib
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
VENDOR = ROOT / "vendor" / "openal-soft"
CONFIG = tomllib.loads((VENDOR / "source.toml").read_text(encoding="utf-8"))


@pytest.mark.parametrize(
    ("relative_path", "checksum_key"),
    [
        ("al.xml", "sha256"),
        ("openal-soft-1.25.2.tar.bz2", "library_source_sha256"),
        ("runtime/win_amd64/soft_oal.dll", "windows_amd64_sha256"),
        ("COPYING", "license_sha256"),
        ("LICENSE-pffft", "pffft_license_sha256"),
    ],
)
def test_vendored_file_matches_manifest(
    relative_path: str,
    checksum_key: str,
) -> None:
    expected = CONFIG[checksum_key]
    assert isinstance(expected, str)
    data = (VENDOR / relative_path).read_bytes()
    if relative_path == "al.xml":
        data = data.replace(b"\r\n", b"\n").replace(b"\r", b"\n")
    assert hashlib.sha256(data).hexdigest() == expected
