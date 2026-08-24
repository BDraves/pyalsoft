"""Tests for automated full-release OpenAL Soft updates."""

from __future__ import annotations

import io
import zipfile
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

from tools.bindings.config import load_source_info
from tools.bindings.models import EnumDecl
from tools.bindings.paths import DEFAULT_REGISTRY, DEFAULT_SOURCE
from tools.bindings.registry import parse_registry
from tools.openal_soft import sha256, source_archive_path
from tools.openal_update import (
    Impact,
    Release,
    _update_changelog,
    _validate_binary_archive,
    _validate_source_archive,
    _verify_published_digest,
    apply_update,
    classify_impact,
    discover_release,
    prepare_update,
    render_report,
)


def _release_json(version: str = "1.25.3") -> dict[str, Any]:
    return {
        "tag_name": version,
        "html_url": f"https://github.com/kcat/openal-soft/releases/tag/{version}",
        "draft": False,
        "prerelease": False,
        "assets": [
            {
                "name": f"openal-soft-{version}-bin.zip",
                "browser_download_url": f"https://example.test/{version}-bin.zip",
                "digest": None,
            },
        ],
    }


def test_discover_release_resolves_annotated_tag_to_commit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    commit = "a" * 40
    tag_object = "b" * 40

    def fake_json(url: str) -> dict[str, Any]:
        if url.endswith("/releases/latest"):
            return _release_json()
        if url.endswith("/git/ref/tags/1.25.3"):
            return {"object": {"type": "tag", "sha": tag_object}}
        if url.endswith(f"/git/tags/{tag_object}"):
            return {"object": {"type": "commit", "sha": commit}}
        raise AssertionError(url)

    monkeypatch.setattr("tools.openal_update._download_json", fake_json)

    release = discover_release()

    assert release.version == "1.25.3"
    assert release.commit == commit
    assert release.source_url == (
        "https://openal-soft.org/openal-releases/openal-soft-1.25.3.tar.bz2"
    )
    assert release.binary_url.endswith("1.25.3-bin.zip")


@pytest.mark.parametrize(
    ("change", "message"),
    [
        ({"prerelease": True}, "draft or prerelease"),
        ({"tag_name": "1.25.3-nightly"}, "not a stable"),
        ({"assets": []}, "exactly one"),
    ],
)
def test_discover_release_rejects_unsupported_releases(
    monkeypatch: pytest.MonkeyPatch,
    change: dict[str, Any],
    message: str,
) -> None:
    raw = _release_json()
    raw.update(change)
    monkeypatch.setattr("tools.openal_update._download_json", lambda _url: raw)

    with pytest.raises(ValueError, match=message):
        discover_release()


def test_published_asset_digest_is_verified() -> None:
    data = b"upstream asset"

    _verify_published_digest("asset", data, f"sha256:{sha256(data)}")

    with pytest.raises(ValueError, match="published SHA-256 mismatch"):
        _verify_published_digest("asset", data, f"sha256:{'0' * 64}")


def test_binary_archive_requires_all_expected_members() -> None:
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w") as archive:
        archive.writestr("present", b"data")

    with pytest.raises(ValueError, match="missing required members: absent"):
        _validate_binary_archive(stream.getvalue(), ("present", "absent"))


def test_source_archive_path_comes_from_manifest_version() -> None:
    assert source_archive_path({"version": "2.0.1"}) == (
        source_archive_path().parent / "openal-soft-2.0.1.tar.bz2"
    )


def test_current_source_archive_contains_the_pinned_registry() -> None:
    source = load_source_info()
    _validate_source_archive(
        source_archive_path().read_bytes(),
        source.version,
        DEFAULT_REGISTRY.read_bytes(),
    )


def test_api_impact_distinguishes_additions_changes_and_removals() -> None:
    current = parse_registry()
    extra = EnumDecl(
        "AL",
        "AL_TEST_AUTOMATION",
        "0x7fffffff",
        ("Boolean",),
        None,
        None,
        None,
        (),
        (),
    )
    added = replace(current, enums=(*current.enums, extra))
    changed_enum = replace(current.enums[0], value="0x7ffffffe")
    changed = replace(current, enums=(changed_enum, *current.enums[1:]))
    removed = replace(current, enums=current.enums[1:])

    assert classify_impact(current, current).recommendation == "patch"
    addition = classify_impact(current, added)
    assert addition.recommendation == "minor"
    assert "constant:AL_TEST_AUTOMATION" in addition.added
    assert classify_impact(current, changed).recommendation.startswith("major-risk")
    assert classify_impact(current, removed).recommendation.startswith("major-risk")


def test_changelog_update_reuses_changed_section() -> None:
    changelog = """# Changelog

## Unreleased

### Changed

- Existing change.

## 1.0.0 - 2026.01.01
"""

    updated = _update_changelog(changelog, "1.25.2", "1.25.3")

    assert updated.count("### Changed") == 1
    assert "from 1.25.2 to 1.25.3" in updated
    assert _update_changelog(updated, "1.25.2", "1.25.3") == updated


def _source_archive(version: str, registry: bytes) -> bytes:
    import tarfile

    stream = io.BytesIO()
    content = b"cmake_minimum_required(VERSION 3.25)\n"
    info = tarfile.TarInfo(f"openal-soft-{version}/CMakeLists.txt")
    info.size = len(content)
    with tarfile.open(fileobj=stream, mode="w:bz2") as archive:
        archive.addfile(info, io.BytesIO(content))
        registry_info = tarfile.TarInfo(f"openal-soft-{version}/registry/xml/al.xml")
        registry_info.size = len(registry)
        archive.addfile(registry_info, io.BytesIO(registry))
    return stream.getvalue()


def _binary_archive(version: str) -> bytes:
    stream = io.BytesIO()
    prefix = f"openal-soft-{version}-bin"
    with zipfile.ZipFile(stream, "w") as archive:
        archive.writestr(f"{prefix}/bin/Win64/soft_oal.dll", b"dll")
        archive.writestr(f"{prefix}/COPYING", b"license")
        archive.writestr(f"{prefix}/LICENSE-pffft", b"pffft")
    return stream.getvalue()


def test_source_archive_must_match_the_tag_registry() -> None:
    with pytest.raises(ValueError, match="does not match the release tag"):
        _validate_source_archive(
            _source_archive("1.25.3", b"archived registry"),
            "1.25.3",
            b"tag registry",
        )


def test_prepare_then_apply_updates_the_full_release_atomically(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    import tools.openal_update as updater

    version = "1.25.3"
    vendor = tmp_path / "vendor" / "openal-soft"
    generated = tmp_path / "src" / "generated"
    docs = tmp_path / "docs" / "reference.md"
    readme = tmp_path / "README.md"
    notice = tmp_path / "LICENSES" / "THIRD-PARTY.md"
    changelog = tmp_path / "CHANGELOG.md"
    vendor.mkdir(parents=True)
    generated.mkdir(parents=True)
    docs.parent.mkdir(parents=True)
    notice.parent.mkdir(parents=True)
    current_registry = DEFAULT_REGISTRY.read_bytes()
    (vendor / "al.xml").write_bytes(current_registry)
    (vendor / "openal-soft-1.25.2.tar.bz2").write_bytes(b"old")
    (vendor / "source.toml").write_text(
        DEFAULT_SOURCE.read_text(encoding="utf-8"), encoding="utf-8"
    )
    readme.write_text(
        """<!-- openal-soft-version-badge:start -->
[![OpenAL Soft 1.25.2](old)](old)
<!-- openal-soft-version-badge:end -->
""",
        encoding="utf-8",
    )
    notice.write_text(
        "OpenAL Soft 1.25.2 shared library\nopenal-soft-1.25.2.tar.bz2\n",
        encoding="utf-8",
    )
    changelog.write_text("# Changelog\n\n## Unreleased\n", encoding="utf-8")

    monkeypatch.setattr(updater, "ROOT", tmp_path)
    monkeypatch.setattr(updater, "VENDOR", vendor)
    monkeypatch.setattr(updater, "DEFAULT_SOURCE", vendor / "source.toml")
    monkeypatch.setattr(updater, "DEFAULT_REGISTRY", vendor / "al.xml")
    monkeypatch.setattr(updater, "DEFAULT_OUTPUT_DIR", generated)
    monkeypatch.setattr(updater, "DEFAULT_DOCS_OUTPUT", docs)
    monkeypatch.setattr(updater, "DEFAULT_README_OUTPUT", readme)
    monkeypatch.setattr(updater, "THIRD_PARTY_NOTICE", notice)
    monkeypatch.setattr(updater, "CHANGELOG", changelog)

    source = _source_archive(version, current_registry)
    binary = _binary_archive(version)
    release = Release(
        version,
        "a" * 40,
        f"https://example.test/releases/{version}",
        "https://example.test/source",
        f"sha256:{sha256(source)}",
        "https://example.test/binary",
        f"sha256:{sha256(binary)}",
    )

    def fake_download(url: str) -> bytes:
        if url.endswith("registry/xml/al.xml"):
            return current_registry
        if url == release.source_url:
            return source
        if url == release.binary_url:
            return binary
        raise AssertionError(url)

    monkeypatch.setattr(updater, "_download", fake_download)
    original_manifest = (vendor / "source.toml").read_bytes()

    update = prepare_update(release)

    assert update is not None
    assert (vendor / "source.toml").read_bytes() == original_manifest
    assert update.impact.recommendation == "patch"
    apply_update(update)
    assert f'version = "{version}"' in (vendor / "source.toml").read_text()
    assert not (vendor / "openal-soft-1.25.2.tar.bz2").exists()
    assert (vendor / f"openal-soft-{version}.tar.bz2").read_bytes() == source
    assert (vendor / "runtime" / "win_amd64" / "soft_oal.dll").read_bytes() == b"dll"
    assert f"OpenAL Soft {version}" in readme.read_text()
    assert "from 1.25.2 to 1.25.3" in changelog.read_text()


def test_report_never_claims_to_change_pyalsoft_version() -> None:
    impact = Impact(("constant:AL_NEW",), (), ())
    release = Release(
        "1.25.3",
        "a" * 40,
        "https://example.test/release",
        "https://example.test/source",
        None,
        "https://example.test/binary",
        None,
    )

    report = render_report(
        "1.25.2",
        release,
        impact,
        {
            "registry": "1" * 64,
            "source_archive": "2" * 64,
            "binary_archive": "3" * 64,
            "windows_runtime": "4" * 64,
        },
    )

    assert "Recommended PyALSoft release: **minor**" in report
    assert "PyALSoft version was not changed" in report
    assert f"Registry SHA-256: `{'1' * 64}`" in report
