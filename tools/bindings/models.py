"""Language-neutral registry and generator models."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass


class RegistryError(ValueError):
    """Raised when the registry contains an unsupported or invalid declaration."""


@dataclass(frozen=True, slots=True)
class SourceInfo:
    """Identity and integrity information for the vendored registry."""

    version: str
    commit: str
    registry_path: str
    source_url: str
    sha256: str


@dataclass(frozen=True, slots=True)
class TypeDecl:
    namespace: str
    name: str
    category: str
    declaration: str
    comment: str | None


@dataclass(frozen=True, slots=True)
class DefineDecl:
    namespace: str
    name: str
    replacement: str | None


@dataclass(frozen=True, slots=True)
class PropertyDecl:
    objects: tuple[str, ...]
    value_types: tuple[str, ...]
    range: str | None
    default: str | None
    groups: tuple[str, ...]
    object_class: str | None
    kind: str | None


@dataclass(frozen=True, slots=True)
class EnumDecl:
    namespace: str
    name: str
    value: str
    groups: tuple[str, ...]
    deprecated: str | None
    block_group: str | None
    comment: str | None
    comments: tuple[str, ...]
    properties: tuple[PropertyDecl, ...]


@dataclass(frozen=True, slots=True)
class ParameterDecl:
    name: str
    c_type: str
    length: str | None
    group: str | None
    object_class: str | None


@dataclass(frozen=True, slots=True)
class CommandDecl:
    namespace: str
    name: str
    return_type: str
    parameters: tuple[ParameterDecl, ...]
    export: str | None
    function_pointer: str
    deprecated: str | None
    return_group: str | None
    comment: str | None
    comments: tuple[str, ...]
    command_attribute: str | None


@dataclass(frozen=True, slots=True)
class ApiMemberDecl:
    kind: str
    name: str


@dataclass(frozen=True, slots=True)
class RequirementDecl:
    api: str | None
    comment: str | None
    members: tuple[ApiMemberDecl, ...]
    depends: str | None


@dataclass(frozen=True, slots=True)
class ApiSetDecl:
    kind: str
    name: str
    apis: tuple[str, ...]
    number: str | None
    annex: str | None
    requirements: tuple[RequirementDecl, ...]
    comments: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class RegistryNoteDecl:
    parent: str
    subject: str | None
    text: str


@dataclass(frozen=True, slots=True)
class Registry:
    types: tuple[TypeDecl, ...]
    defines: tuple[DefineDecl, ...]
    enums: tuple[EnumDecl, ...]
    commands: tuple[CommandDecl, ...]
    api_sets: tuple[ApiSetDecl, ...]
    comments: tuple[str, ...]
    notes: tuple[RegistryNoteDecl, ...]


@dataclass(frozen=True, slots=True)
class ConstantValue:
    literal: str
    python_type: str


@dataclass(frozen=True, slots=True)
class FunctionPointerDecl:
    name: str
    return_type: str
    parameter_types: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class PropertyOverride:
    objects: tuple[str, ...] | None
    value_types: tuple[str, ...] | None
    enum_groups: tuple[str, ...] | None
    writable: bool | None
    generate: bool
    reason: str | None


@dataclass(frozen=True, slots=True)
class CommandOverride:
    lengths: Mapping[str, str]
    directions: Mapping[str, str]
    string_list_name: str | None


@dataclass(frozen=True, slots=True)
class SemanticOverrides:
    properties: Mapping[str, PropertyOverride]
    commands: Mapping[str, CommandOverride]


@dataclass(frozen=True, slots=True)
class EffectiveProperty:
    namespace: str
    object_name: str
    enum_name: str
    python_name: str
    value_types: tuple[str, ...]
    range: str | None
    default: str | None
    groups: tuple[str, ...]
    object_class: str | None
    kind: str | None
    readable: bool
    writable: bool
    generate: bool
    getter: str | None
    setter: str | None
    arity: int | None
    enum_type: str | None
    extensions: tuple[str, ...]
    comment: str | None


@dataclass(frozen=True, slots=True)
class WrapperParameter:
    name: str
    python_name: str
    c_type: str
    direction: str
    length: str | None
    group: str | None
    object_class: str | None
    visible: bool


@dataclass(frozen=True, slots=True)
class CommandWrapper:
    namespace: str
    name: str
    python_name: str
    return_type: str
    return_group: str | None
    parameters: tuple[WrapperParameter, ...]
    result_size: bool
    string_list_name: str | None
    extension: str | None
    comment: str | None
