"""Load generator source metadata and reviewed semantic overrides."""

from __future__ import annotations

import tomllib
from collections.abc import Mapping
from pathlib import Path

from .models import (
    CommandOverride,
    PropertyOverride,
    RegistryError,
    SemanticOverrides,
    SourceInfo,
)
from .paths import DEFAULT_SEMANTICS, DEFAULT_SOURCE


def _required_string(settings: Mapping[str, object], key: str) -> str:
    value = settings.get(key)
    if not isinstance(value, str) or not value:
        raise RegistryError(f"source.toml must define a non-empty string {key!r}")
    return value


def load_source_info(path: Path = DEFAULT_SOURCE) -> SourceInfo:
    """Load the machine-readable identity of the vendored upstream file."""

    with path.open("rb") as stream:
        settings = tomllib.load(stream)
    return SourceInfo(
        version=_required_string(settings, "version"),
        commit=_required_string(settings, "commit"),
        registry_path=_required_string(settings, "registry_path"),
        source_url=_required_string(settings, "source_url"),
        sha256=_required_string(settings, "sha256").lower(),
    )


def _settings_table(value: object, label: str) -> Mapping[str, object]:
    if not isinstance(value, dict) or not all(isinstance(key, str) for key in value):
        raise RegistryError(f"{label} must be a TOML table")
    return value


def _optional_string_tuple(
    settings: Mapping[str, object], key: str, label: str
) -> tuple[str, ...] | None:
    value = settings.get(key)
    if value is None:
        return None
    if not isinstance(value, list) or not all(
        isinstance(item, str) and item for item in value
    ):
        raise RegistryError(f"{label}.{key} must be an array of non-empty strings")
    return tuple(value)


def _optional_bool(settings: Mapping[str, object], key: str, label: str) -> bool | None:
    value = settings.get(key)
    if value is not None and not isinstance(value, bool):
        raise RegistryError(f"{label}.{key} must be a boolean")
    return value


def _optional_string(
    settings: Mapping[str, object], key: str, label: str
) -> str | None:
    value = settings.get(key)
    if value is not None and not isinstance(value, str):
        raise RegistryError(f"{label}.{key} must be a string")
    return value


def _string_mapping(
    settings: Mapping[str, object], key: str, label: str
) -> Mapping[str, str]:
    value = settings.get(key, {})
    table = _settings_table(value, f"{label}.{key}")
    if not all(isinstance(item, str) for item in table.values()):
        raise RegistryError(f"{label}.{key} values must be strings")
    return {name: item for name, item in table.items() if isinstance(item, str)}


def load_semantic_overrides(
    path: Path = DEFAULT_SEMANTICS,
) -> SemanticOverrides:
    """Load reviewed semantics that are absent or incorrect in the XML."""

    with path.open("rb") as stream:
        settings = tomllib.load(stream)
    if settings.get("version") != 1:
        raise RegistryError("semantic overrides must declare version = 1")
    unknown_root = set(settings) - {"version", "property", "command"}
    if unknown_root:
        joined = ", ".join(sorted(unknown_root))
        raise RegistryError(f"unknown semantic override section(s): {joined}")

    property_table = _settings_table(settings.get("property", {}), "property")
    properties: dict[str, PropertyOverride] = {}
    for name, raw_value in property_table.items():
        label = f"property.{name}"
        values = _settings_table(raw_value, label)
        unknown = set(values) - {
            "objects",
            "value_types",
            "enum_groups",
            "writable",
            "generate",
            "reason",
        }
        if unknown:
            joined = ", ".join(sorted(unknown))
            raise RegistryError(f"unknown {label} setting(s): {joined}")
        generate = _optional_bool(values, "generate", label)
        properties[name] = PropertyOverride(
            objects=_optional_string_tuple(values, "objects", label),
            value_types=_optional_string_tuple(values, "value_types", label),
            enum_groups=_optional_string_tuple(values, "enum_groups", label),
            writable=_optional_bool(values, "writable", label),
            generate=True if generate is None else generate,
            reason=_optional_string(values, "reason", label),
        )

    command_table = _settings_table(settings.get("command", {}), "command")
    commands: dict[str, CommandOverride] = {}
    for name, raw_value in command_table.items():
        label = f"command.{name}"
        values = _settings_table(raw_value, label)
        unknown = set(values) - {"lengths", "directions"}
        if unknown:
            joined = ", ".join(sorted(unknown))
            raise RegistryError(f"unknown {label} setting(s): {joined}")
        commands[name] = CommandOverride(
            lengths=_string_mapping(values, "lengths", label),
            directions=_string_mapping(values, "directions", label),
        )

    return SemanticOverrides(properties=properties, commands=commands)
