"""Stable models consumed by generated OpenAL registry metadata."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class TypeSpec:
    """A C type declaration from the registry."""

    namespace: str
    name: str
    category: str
    declaration: str
    comment: str | None


@dataclass(frozen=True, slots=True)
class DefineSpec:
    """A C preprocessor definition and its Python value, when representable."""

    namespace: str
    name: str
    replacement: str | None
    python_value: int | float | str | None


@dataclass(frozen=True, slots=True)
class PropertySpec:
    """The valid use, type, range, and default metadata for an enum property."""

    objects: tuple[str, ...]
    value_types: tuple[str, ...]
    range: str | None
    default: str | None
    groups: tuple[str, ...]
    object_class: str | None
    kind: str | None


@dataclass(frozen=True, slots=True)
class EnumSpec:
    """An OpenAL enumeration value and its registry annotations."""

    namespace: str
    name: str
    value: str
    groups: tuple[str, ...]
    deprecated: str | None
    block_group: str | None
    comment: str | None
    comments: tuple[str, ...]
    properties: tuple[PropertySpec, ...]


@dataclass(frozen=True, slots=True)
class ParameterSpec:
    """A C command parameter from the registry."""

    name: str
    c_type: str
    length: str | None
    group: str | None
    object_class: str | None


@dataclass(frozen=True, slots=True)
class CommandSpec:
    """A core or extension command declaration."""

    namespace: str
    name: str
    return_type: str
    parameters: tuple[ParameterSpec, ...]
    export: str | None
    function_pointer: str
    deprecated: str | None
    return_group: str | None
    comment: str | None
    comments: tuple[str, ...]
    command_attribute: str | None


@dataclass(frozen=True, slots=True)
class ApiMemberSpec:
    """A type, enum, or command required by an API set."""

    kind: str
    name: str


@dataclass(frozen=True, slots=True)
class RequirementSpec:
    """A group of declarations required together."""

    api: str | None
    comment: str | None
    members: tuple[ApiMemberSpec, ...]
    depends: str | None


@dataclass(frozen=True, slots=True)
class ApiSetSpec:
    """A core feature or extension described by the registry."""

    kind: str
    name: str
    apis: tuple[str, ...]
    number: str | None
    annex: str | None
    requirements: tuple[RequirementSpec, ...]
    comments: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class RegistryNoteSpec:
    """An editorial XML note with its nearest registry context."""

    parent: str
    subject: str | None
    text: str


@dataclass(frozen=True, slots=True)
class EnumGroupSpec:
    """A semantic enum group exposed as a generated :class:`IntEnum`."""

    namespace: str
    name: str
    python_name: str
    members: tuple[str, ...]
    bitmask: bool


@dataclass(frozen=True, slots=True)
class ObjectPropertySpec:
    """A normalized object property assembled from XML and local corrections."""

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
    getter: str | None
    setter: str | None
    arity: int | None
    enum_type: str | None
    extensions: tuple[str, ...]
    comment: str | None


@dataclass(frozen=True, slots=True)
class WrapperParameterSpec:
    """Marshalling instructions for one generated Python command parameter."""

    name: str
    python_name: str
    c_type: str
    direction: str
    length: str | None
    group: str | None
    object_class: str | None
    visible: bool


@dataclass(frozen=True, slots=True)
class CommandWrapperSpec:
    """The generated Python-facing form of a raw OpenAL command."""

    namespace: str
    name: str
    python_name: str
    return_type: str
    return_group: str | None
    parameters: tuple[WrapperParameterSpec, ...]
    result_size: bool
    extension: str | None
    comment: str | None
