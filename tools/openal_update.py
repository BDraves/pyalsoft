"""Update the complete pinned OpenAL Soft release and generated bindings."""

from __future__ import annotations

import argparse
import io
import json
import os
import re
import tarfile
import tempfile
import urllib.parse
import urllib.request
import zipfile
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from tools.bindings.c_types import _resolve_constants, _resolve_defines  # noqa: E402
from tools.bindings.config import (  # noqa: E402
    load_semantic_overrides,
    load_source_info,
)
from tools.bindings.models import Registry, SourceInfo  # noqa: E402
from tools.bindings.outputs import (  # noqa: E402
    render_outputs,
    render_readme_badge,
)
from tools.bindings.paths import (  # noqa: E402
    DEFAULT_DOCS_OUTPUT,
    DEFAULT_OUTPUT_DIR,
    DEFAULT_README_OUTPUT,
    DEFAULT_REGISTRY,
    DEFAULT_SEMANTICS,
    DEFAULT_SOURCE,
)
from tools.bindings.registry import parse_registry  # noqa: E402
from tools.bindings.render_docs import render_documentation  # noqa: E402
from tools.bindings.semantics import (  # noqa: E402
    build_command_wrappers,
    build_effective_properties,
    build_enum_groups,
)
from tools.openal_soft import ROOT, VENDOR, sha256  # noqa: E402
from tools.sync_openal_soft import _member, _normalized_registry  # noqa: E402

API_ROOT = "https://api.github.com/repos/kcat/openal-soft"
STABLE_VERSION = re.compile(r"[0-9]+\.[0-9]+\.[0-9]+")
THIRD_PARTY_NOTICE = ROOT / "LICENSES" / "THIRD-PARTY.md"
CHANGELOG = ROOT / "CHANGELOG.md"


@dataclass(frozen=True, slots=True)
class Release:
    """A validated published OpenAL Soft release."""

    version: str
    commit: str
    html_url: str
    source_url: str
    source_digest: str | None
    binary_url: str
    binary_digest: str | None


@dataclass(frozen=True, slots=True)
class Impact:
    """Structural changes to the generated public binding surface."""

    added: tuple[str, ...]
    removed: tuple[str, ...]
    changed: tuple[str, ...]

    @property
    def recommendation(self) -> str:
        """Return the minimum recommended PyALSoft release classification."""

        if self.removed or self.changed:
            return "major-risk/manual review"
        if self.added:
            return "minor"
        return "patch"


@dataclass(frozen=True, slots=True)
class PreparedUpdate:
    """A fully downloaded and validated repository update."""

    current_version: str
    release: Release
    files: Mapping[Path, bytes]
    remove: tuple[Path, ...]
    impact: Impact
    report: str


def _headers() -> dict[str, str]:
    headers = {
        "Accept": "application/vnd.github+json",
        "User-Agent": "pyalsoft-openal-updater",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    token = os.environ.get("GITHUB_TOKEN")
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers


def _download(url: str) -> bytes:
    request = urllib.request.Request(url, headers=_headers())
    with urllib.request.urlopen(request, timeout=60) as response:
        return bytes(response.read())


def _download_json(url: str) -> Mapping[str, Any]:
    value = json.loads(_download(url))
    if not isinstance(value, dict):
        raise ValueError(f"expected a JSON object from {url}")
    return value


def _required_string(value: Mapping[str, Any], key: str, label: str) -> str:
    selected = value.get(key)
    if not isinstance(selected, str) or not selected:
        raise ValueError(f"{label} must contain a non-empty {key!r}")
    return selected


def _release_asset(release: Mapping[str, Any], name: str) -> tuple[str, str | None]:
    assets = release.get("assets")
    if not isinstance(assets, list):
        raise ValueError("release must contain an asset list")
    matches = [
        asset
        for asset in assets
        if isinstance(asset, dict) and asset.get("name") == name
    ]
    if len(matches) != 1:
        raise ValueError(f"release must contain exactly one {name!r} asset")
    asset = matches[0]
    url = _required_string(asset, "browser_download_url", f"asset {name!r}")
    digest = asset.get("digest")
    if digest is not None and not isinstance(digest, str):
        raise ValueError(f"asset {name!r} digest must be a string or null")
    return url, digest


def _resolve_commit(tag: str) -> str:
    encoded = urllib.parse.quote(tag, safe="")
    reference = _download_json(f"{API_ROOT}/git/ref/tags/{encoded}")
    object_ = reference.get("object")
    if not isinstance(object_, dict):
        raise ValueError(f"tag {tag!r} does not identify a Git object")
    for _ in range(4):
        kind = object_.get("type")
        commit = object_.get("sha")
        if not isinstance(commit, str) or not re.fullmatch(r"[0-9a-f]{40}", commit):
            raise ValueError(f"tag {tag!r} has an invalid Git object SHA")
        if kind == "commit":
            return commit
        if kind != "tag":
            raise ValueError(f"tag {tag!r} resolves to unsupported object {kind!r}")
        tag_object = _download_json(f"{API_ROOT}/git/tags/{commit}")
        nested = tag_object.get("object")
        if not isinstance(nested, dict):
            raise ValueError(f"annotated tag {tag!r} has no target object")
        object_ = nested
    raise ValueError(f"tag {tag!r} has too many nested annotated tags")


def discover_release(version: str | None = None) -> Release:
    """Return a stable release and its immutable source locations."""

    endpoint = (
        "latest" if version is None else f"tags/{urllib.parse.quote(version, safe='')}"
    )
    raw = _download_json(f"{API_ROOT}/releases/{endpoint}")
    tag = _required_string(raw, "tag_name", "release")
    if version is not None and tag != version:
        raise ValueError(f"requested release {version!r}, received tag {tag!r}")
    if STABLE_VERSION.fullmatch(tag) is None:
        raise ValueError(f"release tag {tag!r} is not a stable X.Y.Z version")
    if raw.get("draft") is not False or raw.get("prerelease") is not False:
        raise ValueError(f"release {tag!r} is a draft or prerelease")

    binary_name = f"openal-soft-{tag}-bin.zip"
    binary_url, binary_digest = _release_asset(raw, binary_name)
    return Release(
        version=tag,
        commit=_resolve_commit(tag),
        html_url=_required_string(raw, "html_url", "release"),
        source_url=(
            f"https://openal-soft.org/openal-releases/openal-soft-{tag}.tar.bz2"
        ),
        source_digest=None,
        binary_url=binary_url,
        binary_digest=binary_digest,
    )


def _version_key(version: str) -> tuple[int, int, int]:
    if STABLE_VERSION.fullmatch(version) is None:
        raise ValueError(f"version {version!r} is not a stable X.Y.Z version")
    return tuple(int(part) for part in version.split("."))  # type: ignore[return-value]


def _verify_published_digest(label: str, data: bytes, digest: str | None) -> None:
    if digest is None:
        return
    algorithm, separator, expected = digest.partition(":")
    if separator != ":" or algorithm.casefold() != "sha256":
        raise ValueError(f"{label} has unsupported published digest {digest!r}")
    actual = sha256(data)
    if actual != expected.casefold():
        raise ValueError(
            f"{label} published SHA-256 mismatch: expected {expected}, got {actual}"
        )


def _validate_source_archive(data: bytes, version: str, registry: bytes) -> None:
    root = f"openal-soft-{version}"
    cmake_member = f"{root}/CMakeLists.txt"
    registry_member = f"{root}/registry/xml/al.xml"
    try:
        with tarfile.open(fileobj=io.BytesIO(data), mode="r:bz2") as archive:
            names = set(archive.getnames())
            missing = {cmake_member, registry_member} - names
            if missing:
                formatted = ", ".join(repr(name) for name in sorted(missing))
                raise ValueError(
                    f"source archive does not contain required members: {formatted}"
                )
            extracted = archive.extractfile(registry_member)
            if extracted is None:
                raise ValueError(
                    f"source archive member {registry_member!r} is not a file"
                )
            archived_registry = _normalized_registry(extracted.read())
    except tarfile.TarError as error:
        raise ValueError(
            "OpenAL Soft source asset is not a valid tar.bz2 archive"
        ) from error
    if archived_registry != registry:
        raise ValueError(
            "source archive registry does not match the release tag registry"
        )


def _validate_binary_archive(data: bytes, members: Sequence[str]) -> None:
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            names = set(archive.namelist())
    except zipfile.BadZipFile as error:
        raise ValueError(
            "OpenAL Soft binary asset is not a valid ZIP archive"
        ) from error
    missing = set(members) - names
    if missing:
        formatted = ", ".join(sorted(missing))
        raise ValueError(f"binary archive is missing required members: {formatted}")


def _toml_string(value: str) -> str:
    return json.dumps(value, ensure_ascii=True)


def _render_manifest(
    release: Release,
    registry: bytes,
    source_archive: bytes,
    binary_archive: bytes,
    dll: bytes,
    license_text: bytes,
    pffft_license: bytes,
) -> str:
    version = release.version
    prefix = f"openal-soft-{version}-bin"
    values = (
        ("version", version),
        ("commit", release.commit),
        ("registry_path", "registry/xml/al.xml"),
        (
            "source_url",
            "https://raw.githubusercontent.com/kcat/openal-soft/"
            f"{release.commit}/registry/xml/al.xml",
        ),
        ("sha256", sha256(registry)),
        ("library_source_url", release.source_url),
        ("library_source_sha256", sha256(source_archive)),
        ("binary_archive_url", release.binary_url),
        ("binary_archive_sha256", sha256(binary_archive)),
        ("windows_amd64_member", f"{prefix}/bin/Win64/soft_oal.dll"),
        ("windows_amd64_sha256", sha256(dll)),
        ("license_member", f"{prefix}/COPYING"),
        ("license_sha256", sha256(license_text)),
        ("pffft_license_member", f"{prefix}/LICENSE-pffft"),
        ("pffft_license_sha256", sha256(pffft_license)),
    )
    return "".join(f"{key} = {_toml_string(value)}\n" for key, value in values)


def _surface(registry: Registry) -> dict[str, object]:
    overrides = load_semantic_overrides(DEFAULT_SEMANTICS)
    surface: dict[str, object] = {}
    enum_values = _resolve_constants(registry.enums)
    define_values = _resolve_defines(registry.defines, enum_values)
    for define in registry.defines:
        value = define_values[define.name]
        if value is not None:
            surface[f"constant:{define.name}"] = (
                define.namespace,
                value.python_type,
                value.literal,
            )
    for enum in registry.enums:
        value = enum_values[enum.name]
        surface[f"constant:{enum.name}"] = (
            enum.namespace,
            value.python_type,
            value.literal,
        )
    for type_ in registry.types:
        if type_.category in {"basetype", "funcpointer"}:
            surface[f"type:{type_.name}"] = (type_.category, type_.declaration)
    for command in registry.commands:
        surface[f"command:{command.name}"] = (
            command.namespace,
            command.return_type,
            tuple(
                (
                    parameter.c_type,
                    parameter.length,
                    parameter.group,
                    parameter.object_class,
                )
                for parameter in command.parameters
            ),
        )
    for namespace, group, python_name, members, bitmask in build_enum_groups(
        registry, overrides
    ):
        surface[f"enum:{python_name}"] = (namespace, group, bitmask)
        for enum_member in members:
            surface[f"enum-member:{python_name}.{enum_member}"] = enum_member
    for wrapper in build_command_wrappers(registry, overrides):
        surface[f"wrapper:{wrapper.namespace}.{wrapper.python_name}"] = (
            wrapper.return_type,
            wrapper.return_group,
            tuple(
                (
                    parameter.python_name,
                    parameter.c_type,
                    parameter.direction,
                    parameter.length,
                    parameter.group,
                    parameter.object_class,
                    parameter.retained,
                    parameter.visible,
                )
                for parameter in wrapper.parameters
            ),
            wrapper.result_size,
            wrapper.string_list_name,
            wrapper.extension,
        )
    for property_ in build_effective_properties(registry, overrides):
        if not property_.generate:
            continue
        surface[f"property:{property_.object_name}.{property_.python_name}"] = (
            property_.value_types,
            property_.range,
            property_.default,
            property_.groups,
            property_.object_class,
            property_.kind,
            property_.readable,
            property_.writable,
            property_.getter,
            property_.setter,
            property_.arity,
            property_.enum_type,
            property_.extensions,
        )
    for api_set in registry.api_sets:
        if api_set.kind == "extension":
            surface[f"extension:{api_set.name}"] = api_set.apis
            memberships: dict[str, list[tuple[str | None, str | None]]] = {}
            for requirement in api_set.requirements:
                for api_member in requirement.members:
                    key = (
                        f"extension-member:{api_set.name}."
                        f"{api_member.kind}:{api_member.name}"
                    )
                    memberships.setdefault(key, []).append(
                        (requirement.api, requirement.depends)
                    )
            surface.update(
                (key, tuple(sorted(values, key=repr)))
                for key, values in memberships.items()
            )
    return surface


def classify_impact(current: Registry, updated: Registry) -> Impact:
    """Classify structural changes to the generated binding surface."""

    before = _surface(current)
    after = _surface(updated)
    added = tuple(sorted(after.keys() - before.keys()))
    removed = tuple(sorted(before.keys() - after.keys()))
    changed = tuple(
        sorted(
            name for name in before.keys() & after.keys() if before[name] != after[name]
        )
    )
    return Impact(added, removed, changed)


def _replace_notice(text: str, current: str, updated: str) -> str:
    archive = f"openal-soft-{current}.tar.bz2"
    if text.count(f"OpenAL Soft {current} shared library") != 1:
        raise ValueError("third-party notice has an unexpected OpenAL Soft version")
    if text.count(archive) != 1:
        raise ValueError("third-party notice has an unexpected source archive name")
    return text.replace(
        f"OpenAL Soft {current} shared library",
        f"OpenAL Soft {updated} shared library",
    ).replace(archive, f"openal-soft-{updated}.tar.bz2")


def _update_changelog(text: str, current: str, updated: str) -> str:
    entry = (
        f"- Updated bundled OpenAL Soft and generated bindings from {current} "
        f"to {updated}."
    )
    if entry in text:
        return text
    marker = "## Unreleased\n"
    start = text.find(marker)
    if start < 0:
        raise ValueError("CHANGELOG.md does not contain an Unreleased section")
    content_start = start + len(marker)
    end = text.find("\n## ", content_start)
    if end < 0:
        end = len(text)
    unreleased = text[content_start:end]
    changed = "\n### Changed\n"
    if changed in unreleased:
        position = content_start + unreleased.index(changed) + len(changed)
        return text[:position] + f"\n{entry}" + text[position:]
    insertion = f"\n### Changed\n\n{entry}\n"
    return text[:content_start] + insertion + text[content_start:]


def _impact_section(title: str, values: Sequence[str]) -> list[str]:
    lines = [f"### {title} ({len(values)})", ""]
    if values:
        lines.extend(f"- `{value}`" for value in values)
    else:
        lines.append("- None")
    lines.append("")
    return lines


def render_report(
    current: str,
    release: Release,
    impact: Impact,
    digests: Mapping[str, str],
) -> str:
    """Render the update provenance and compatibility report as Markdown."""

    lines = [
        f"## OpenAL Soft {current} to {release.version}",
        "",
        f"- Upstream release: {release.html_url}",
        f"- Upstream commit: `{release.commit}`",
        f"- Registry SHA-256: `{digests['registry']}`",
        f"- Source archive SHA-256: `{digests['source_archive']}`",
        f"- Binary archive SHA-256: `{digests['binary_archive']}`",
        f"- Win64 runtime SHA-256: `{digests['windows_runtime']}`",
        f"- Recommended PyALSoft release: **{impact.recommendation}**",
        "- PyALSoft version was not changed.",
        "",
    ]
    lines.extend(_impact_section("Added generated API", impact.added))
    lines.extend(_impact_section("Removed generated API", impact.removed))
    lines.extend(_impact_section("Changed generated API", impact.changed))
    lines.extend(
        [
            "### Maintainer review",
            "",
            "- [ ] Review generated API and semantic override changes.",
            "- [ ] Review version-specific native build workarounds.",
            "- [ ] Choose the PyALSoft version during normal release preparation.",
            "",
        ]
    )
    return "\n".join(lines)


def prepare_update(release: Release) -> PreparedUpdate | None:
    """Download and validate an update without changing tracked files."""

    current_source = load_source_info(DEFAULT_SOURCE)
    if release.version == current_source.version:
        return None
    if _version_key(release.version) < _version_key(current_source.version):
        raise ValueError(
            f"refusing to downgrade OpenAL Soft from {current_source.version} "
            f"to {release.version}"
        )

    registry_data = _normalized_registry(
        _download(
            "https://raw.githubusercontent.com/kcat/openal-soft/"
            f"{release.commit}/registry/xml/al.xml"
        )
    )
    source_archive = _download(release.source_url)
    binary_archive = _download(release.binary_url)
    _verify_published_digest("source asset", source_archive, release.source_digest)
    _verify_published_digest("binary asset", binary_archive, release.binary_digest)
    _validate_source_archive(source_archive, release.version, registry_data)

    prefix = f"openal-soft-{release.version}-bin"
    dll_member = f"{prefix}/bin/Win64/soft_oal.dll"
    license_member = f"{prefix}/COPYING"
    pffft_member = f"{prefix}/LICENSE-pffft"
    _validate_binary_archive(binary_archive, (dll_member, license_member, pffft_member))
    dll = _member(binary_archive, dll_member)
    license_text = _member(binary_archive, license_member)
    pffft_license = _member(binary_archive, pffft_member)

    manifest = _render_manifest(
        release,
        registry_data,
        source_archive,
        binary_archive,
        dll,
        license_text,
        pffft_license,
    )
    source = SourceInfo(
        version=release.version,
        commit=release.commit,
        registry_path="registry/xml/al.xml",
        source_url=(
            "https://raw.githubusercontent.com/kcat/openal-soft/"
            f"{release.commit}/registry/xml/al.xml"
        ),
        sha256=sha256(registry_data),
    )

    with tempfile.TemporaryDirectory(prefix="pyalsoft-openal-update-") as temporary:
        registry_path = Path(temporary) / "al.xml"
        registry_path.write_bytes(registry_data)
        updated_registry = parse_registry(registry_path)
    current_registry = parse_registry(DEFAULT_REGISTRY)
    overrides = load_semantic_overrides(DEFAULT_SEMANTICS)
    generated = render_outputs(updated_registry, source, source.sha256, overrides)
    documentation = render_documentation(
        updated_registry, source, source.sha256, overrides
    )
    readme = render_readme_badge(
        DEFAULT_README_OUTPUT.read_text(encoding="utf-8"), source
    )
    notice = _replace_notice(
        THIRD_PARTY_NOTICE.read_text(encoding="utf-8"),
        current_source.version,
        release.version,
    )
    changelog = _update_changelog(
        CHANGELOG.read_text(encoding="utf-8"),
        current_source.version,
        release.version,
    )
    impact = classify_impact(current_registry, updated_registry)

    archive = VENDOR / f"openal-soft-{release.version}.tar.bz2"
    files: dict[Path, bytes] = {
        DEFAULT_SOURCE: manifest.encode(),
        DEFAULT_REGISTRY: registry_data,
        archive: source_archive,
        VENDOR / "runtime" / "win_amd64" / "soft_oal.dll": dll,
        VENDOR / "COPYING": license_text,
        VENDOR / "LICENSE-pffft": pffft_license,
        DEFAULT_DOCS_OUTPUT: documentation.encode(),
        DEFAULT_README_OUTPUT: readme.encode(),
        THIRD_PARTY_NOTICE: notice.encode(),
        CHANGELOG: changelog.encode(),
    }
    files.update(
        (DEFAULT_OUTPUT_DIR / name, content.encode())
        for name, content in generated.items()
    )
    current_archive = VENDOR / f"openal-soft-{current_source.version}.tar.bz2"
    remove = () if current_archive == archive else (current_archive,)
    return PreparedUpdate(
        current_source.version,
        release,
        files,
        remove,
        impact,
        render_report(
            current_source.version,
            release,
            impact,
            {
                "registry": sha256(registry_data),
                "source_archive": sha256(source_archive),
                "binary_archive": sha256(binary_archive),
                "windows_runtime": sha256(dll),
            },
        ),
    )


def apply_update(update: PreparedUpdate) -> None:
    """Apply a previously validated update to the working tree."""

    with tempfile.TemporaryDirectory(prefix=".openal-update-", dir=ROOT) as temporary:
        staging = Path(temporary)
        staged: list[tuple[Path, Path]] = []
        for destination, data in update.files.items():
            relative = destination.relative_to(ROOT)
            source = staging / relative
            source.parent.mkdir(parents=True, exist_ok=True)
            source.write_bytes(data)
            staged.append((source, destination))
        for source, destination in staged:
            destination.parent.mkdir(parents=True, exist_ok=True)
            source.replace(destination)
            print(f"updated {destination.relative_to(ROOT)}")
    for path in update.remove:
        if path.is_file():
            path.unlink()
            print(f"removed {path.relative_to(ROOT)}")


def _argument_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--version",
        help="update to one explicit stable release instead of the latest release",
    )
    parser.add_argument(
        "--report",
        type=Path,
        help="write a Markdown provenance and API-impact report",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Update to a selected stable OpenAL Soft release."""

    arguments = _argument_parser().parse_args(argv)
    if (
        arguments.version is not None
        and STABLE_VERSION.fullmatch(arguments.version) is None
    ):
        raise ValueError("--version must use the stable X.Y.Z form")
    release = discover_release(arguments.version)
    update = prepare_update(release)
    if update is None:
        print(f"OpenAL Soft {release.version} is already current.")
        return 0
    apply_update(update)
    if arguments.report is not None:
        arguments.report.parent.mkdir(parents=True, exist_ok=True)
        arguments.report.write_text(update.report, encoding="utf-8", newline="\n")
        print(f"wrote {arguments.report}")
    else:
        print(update.report)
    return 0
