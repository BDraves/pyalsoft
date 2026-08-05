"""Generate Python declarations from the vendored OpenAL XML registry."""

from __future__ import annotations

import argparse
import ast
import hashlib
import keyword
import re
import tomllib
import xml.etree.ElementTree as ET
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import quote

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_REGISTRY = ROOT / "vendor" / "openal-soft" / "al.xml"
DEFAULT_SOURCE = ROOT / "vendor" / "openal-soft" / "source.toml"
DEFAULT_SEMANTICS = ROOT / "tools" / "semantic_overrides.toml"
DEFAULT_OUTPUT_DIR = ROOT / "src" / "pyalsoft" / "_generated"
DEFAULT_DOCS_OUTPUT = ROOT / "docs" / "reference.md"
DEFAULT_README_OUTPUT = ROOT / "README.md"

_OPENAL_BADGE_START = "<!-- openal-soft-version-badge:start -->"
_OPENAL_BADGE_END = "<!-- openal-soft-version-badge:end -->"

_INTEGER_LITERAL = re.compile(r"-?(?:0[xX][0-9A-Fa-f]+|[0-9]+)\Z")
_FLOAT_LITERAL = re.compile(
    r"-?(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)(?:[eE][+-]?[0-9]+)?f\Z"
)
_IDENTIFIER = re.compile(r"[A-Za-z_][A-Za-z0-9_]*\Z")
_ARRAY_PROPERTY_TYPE = re.compile(
    r"(?P<base>[A-Za-z_][A-Za-z0-9_]*)(?:\[(?P<arity>[1-9][0-9]*)\])?\Z"
)
_EXTERNAL_CONSTANTS = {
    "FLT_MIN": ("1.1754943508222875e-38", "float"),
    "FLT_MAX": ("3.4028234663852886e+38", "float"),
}
_CTYPES_TYPEDEFS = {
    "char": "_ctypes.c_char",
    "signed char": "_ctypes.c_byte",
    "unsigned char": "_ctypes.c_ubyte",
    "short": "_ctypes.c_short",
    "unsigned short": "_ctypes.c_ushort",
    "int": "_ctypes.c_int",
    "unsigned int": "_ctypes.c_uint",
    "float": "_ctypes.c_float",
    "double": "_ctypes.c_double",
    "void": "None",
    "alsoft_impl_int64_t": "_ctypes.c_int64",
    "alsoft_impl_uint64_t": "_ctypes.c_uint64",
}
_TYPEDEF_DECLARATION = re.compile(
    r"typedef (?P<source>.+?) (?P<name>[A-Za-z_][A-Za-z0-9_]*);\Z"
)
_OPAQUE_STRUCT_DECLARATION = re.compile(
    r"(?:typedef )?struct (?P<tag>[A-Za-z_][A-Za-z0-9_]*)"
    r"(?: (?P<alias>[A-Za-z_][A-Za-z0-9_]*))?;\Z"
)
_FUNCTION_POINTER_DECLARATION = re.compile(
    r"typedef (?P<return_type>.+?) "
    r"\((?:ALC?_APIENTRY)?\s*\*(?P<name>[A-Za-z_][A-Za-z0-9_]*)\)"
    r"\((?P<parameters>.*?)\)"
    r"(?: ALC?_API_NOEXCEPT17)?;\Z"
)
_DEFINE_DECLARATION = re.compile(
    r"#define (?P<name>[A-Za-z_][A-Za-z0-9_]*)"
    r"(?: (?P<replacement>.*))?\Z"
)
_ELEMENT_ATTRIBUTES = {
    "registry": frozenset(),
    "comment": frozenset(),
    "types": frozenset({"namespace"}),
    "name": frozenset(),
    "enums": frozenset({"group", "namespace"}),
    "property": frozenset({"class", "default", "group", "kind", "on", "range", "type"}),
    "commands": frozenset({"namespace"}),
    "proto": frozenset({"group"}),
    "param": frozenset({"class", "group", "len"}),
    "ptype": frozenset(),
    "feature": frozenset({"api", "name", "number"}),
    "require": frozenset({"api", "comment", "depends"}),
    "extensions": frozenset(),
    "extension": frozenset({"annex", "name", "supported"}),
}
_ELEMENT_PARENTS = {
    "registry": frozenset({None}),
    "comment": frozenset({"registry", "enum", "command", "extension"}),
    "types": frozenset({"registry"}),
    "type": frozenset({"types", "type", "require"}),
    "name": frozenset({"type", "proto", "param"}),
    "enums": frozenset({"registry"}),
    "enum": frozenset({"enums", "require"}),
    "property": frozenset({"enum"}),
    "commands": frozenset({"registry"}),
    "command": frozenset({"commands", "require"}),
    "proto": frozenset({"command"}),
    "param": frozenset({"command"}),
    "ptype": frozenset({"proto", "param"}),
    "feature": frozenset({"registry"}),
    "require": frozenset({"feature", "extension"}),
    "extensions": frozenset({"registry"}),
    "extension": frozenset({"extensions"}),
}


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
    extension: str | None
    comment: str | None


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


def _required_attribute(element: ET.Element, name: str) -> str:
    value = element.get(name)
    if value is None or not value.strip():
        raise RegistryError(f"<{element.tag}> is missing required attribute {name!r}")
    return value.strip()


def _declaration_name(element: ET.Element) -> str:
    attribute_name = element.get("name")
    if attribute_name:
        return attribute_name.strip()
    child_name = element.findtext("name")
    if child_name:
        return child_name.strip()
    raise RegistryError(f"<{element.tag}> declaration has no name")


def _normalize_c_declaration(value: str) -> str:
    return " ".join(value.split())


def _split_list(value: str | None, separator: str = ",") -> tuple[str, ...]:
    if value is None:
        return ()
    return tuple(item.strip() for item in value.split(separator) if item.strip())


def _child_comments(element: ET.Element) -> tuple[str, ...]:
    return tuple(
        comment
        for child in element.findall("./comment")
        if (comment := _normalize_c_declaration("".join(child.itertext())))
    )


def _mixed_text(element: ET.Element, *, omit_name: bool = False) -> str:
    pieces = [element.text or ""]
    for child in element:
        if not (omit_name and child.tag == "name"):
            pieces.extend(child.itertext())
        pieces.append(child.tail or "")
    return _normalize_c_declaration("".join(pieces))


def _validate_supported_xml(root: ET.Element) -> None:
    """Fail instead of silently discarding a newly introduced XML field."""

    parents = {child: parent for parent in root.iter() for child in parent}
    for element in root.iter():
        if not isinstance(element.tag, str):
            continue
        allowed_parents = _ELEMENT_PARENTS.get(element.tag)
        if allowed_parents is None:
            raise RegistryError(f"unsupported registry element <{element.tag}>")
        parent = parents.get(element)
        parent_tag = parent.tag if parent is not None else None
        if parent_tag not in allowed_parents:
            raise RegistryError(
                f"unsupported <{element.tag}> location under <{parent_tag}>"
            )

        if element.tag == "type":
            if parent_tag == "types":
                allowed_attributes = frozenset({"category", "comment", "name"})
            elif parent_tag == "require":
                allowed_attributes = frozenset({"name"})
            else:
                allowed_attributes = frozenset()
        elif element.tag == "enum":
            if parent_tag == "enums":
                allowed_attributes = frozenset(
                    {"comment", "deprecated", "group", "name", "value"}
                )
            else:
                allowed_attributes = frozenset({"name"})
        elif element.tag == "command":
            if parent_tag == "commands":
                allowed_attributes = frozenset(
                    {"command", "comment", "deprecated", "export", "funcpointer"}
                )
            else:
                allowed_attributes = frozenset({"name"})
        else:
            allowed_attributes = _ELEMENT_ATTRIBUTES[element.tag]
        unknown = set(element.attrib) - allowed_attributes
        if unknown:
            joined = ", ".join(sorted(unknown))
            raise RegistryError(
                f"unsupported attribute(s) on <{element.tag}>: {joined}"
            )


def _parse_types(root: ET.Element) -> tuple[TypeDecl, ...]:
    declarations: list[TypeDecl] = []
    for group in root.findall("./types"):
        namespace = _required_attribute(group, "namespace")
        for element in group.findall("./type"):
            declarations.append(
                TypeDecl(
                    namespace=namespace,
                    name=_declaration_name(element),
                    category=element.get("category", ""),
                    declaration=_mixed_text(element),
                    comment=element.get("comment"),
                )
            )
    return tuple(declarations)


def _parse_defines(types: Sequence[TypeDecl]) -> tuple[DefineDecl, ...]:
    declarations: list[DefineDecl] = []
    for declaration in types:
        if declaration.category != "define":
            continue
        match = _DEFINE_DECLARATION.fullmatch(declaration.declaration)
        if match is None or match.group("name") != declaration.name:
            raise RegistryError(
                f"unsupported define declaration {declaration.declaration!r}"
            )
        declarations.append(
            DefineDecl(
                declaration.namespace,
                declaration.name,
                match.group("replacement"),
            )
        )
    return tuple(declarations)


def _parse_property(element: ET.Element) -> PropertyDecl:
    return PropertyDecl(
        objects=_split_list(_required_attribute(element, "on")),
        value_types=_split_list(element.get("type")),
        range=element.get("range"),
        default=element.get("default"),
        groups=_split_list(element.get("group")),
        object_class=element.get("class"),
        kind=element.get("kind"),
    )


def _parse_enums(root: ET.Element) -> tuple[EnumDecl, ...]:
    declarations: list[EnumDecl] = []
    for block in root.findall("./enums"):
        namespace = _required_attribute(block, "namespace")
        block_group = block.get("group")
        for element in block.findall("./enum"):
            declarations.append(
                EnumDecl(
                    namespace=namespace,
                    name=_required_attribute(element, "name"),
                    value=_required_attribute(element, "value"),
                    groups=_split_list(element.get("group")),
                    deprecated=element.get("deprecated"),
                    block_group=block_group,
                    comment=element.get("comment"),
                    comments=_child_comments(element),
                    properties=tuple(
                        _parse_property(item) for item in element.findall("./property")
                    ),
                )
            )
    return tuple(declarations)


def _parse_parameter(element: ET.Element) -> ParameterDecl:
    return ParameterDecl(
        name=_declaration_name(element),
        c_type=_mixed_text(element, omit_name=True),
        length=element.get("len"),
        group=element.get("group"),
        object_class=element.get("class"),
    )


def _parse_commands(root: ET.Element) -> tuple[CommandDecl, ...]:
    declarations: list[CommandDecl] = []
    for group in root.findall("./commands"):
        namespace = group.get("namespace", "AL")
        for element in group.findall("./command"):
            prototype = element.find("proto")
            if prototype is None:
                raise RegistryError("<command> declaration has no <proto>")
            name = _declaration_name(prototype)
            declarations.append(
                CommandDecl(
                    namespace=namespace,
                    name=name,
                    return_type=_mixed_text(prototype, omit_name=True),
                    parameters=tuple(
                        _parse_parameter(parameter)
                        for parameter in element.findall("./param")
                    ),
                    export=element.get("export"),
                    function_pointer=element.get("funcpointer", f"LP{name.upper()}"),
                    deprecated=element.get("deprecated"),
                    return_group=prototype.get("group"),
                    # One upstream declaration accidentally stores prose in a
                    # ``command`` attribute. Preserve the raw field below and
                    # also make that documentation discoverable as a comment.
                    comment=element.get("comment") or element.get("command"),
                    comments=_child_comments(element),
                    command_attribute=element.get("command"),
                )
            )
    return tuple(declarations)


def _parse_api_set(element: ET.Element, kind: str) -> ApiSetDecl:
    apis: tuple[str, ...]
    if kind == "feature":
        apis = (_required_attribute(element, "api"),)
    else:
        apis = tuple(
            api.strip()
            for api in _required_attribute(element, "supported").split("|")
            if api.strip()
        )

    requirements: list[RequirementDecl] = []
    for requirement in element.findall("./require"):
        members = tuple(
            ApiMemberDecl(child.tag, _required_attribute(child, "name"))
            for child in requirement
            if child.tag in {"command", "enum", "type"}
        )
        requirements.append(
            RequirementDecl(
                api=requirement.get("api"),
                comment=requirement.get("comment"),
                members=members,
                depends=requirement.get("depends"),
            )
        )

    return ApiSetDecl(
        kind=kind,
        name=_required_attribute(element, "name"),
        apis=apis,
        number=element.get("number"),
        annex=element.get("annex"),
        requirements=tuple(requirements),
        comments=_child_comments(element),
    )


def _parse_api_sets(root: ET.Element) -> tuple[ApiSetDecl, ...]:
    features = [
        _parse_api_set(element, "feature") for element in root.findall("./feature")
    ]
    extensions = [
        _parse_api_set(element, "extension")
        for element in root.findall("./extensions/extension")
    ]

    # The upstream registry may split one extension across repeated blocks. Keep
    # its first position while combining the requirement lists.
    merged: list[ApiSetDecl] = []
    positions: dict[str, int] = {}
    for declaration in (*features, *extensions):
        position = positions.get(declaration.name)
        if position is None:
            positions[declaration.name] = len(merged)
            merged.append(declaration)
            continue

        previous = merged[position]
        if (
            previous.kind,
            previous.apis,
            previous.number,
            previous.annex,
        ) != (
            declaration.kind,
            declaration.apis,
            declaration.number,
            declaration.annex,
        ):
            raise RegistryError(f"conflicting repeated API set {declaration.name!r}")
        new_requirements = tuple(
            requirement
            for requirement in declaration.requirements
            if requirement not in previous.requirements
        )
        merged[position] = ApiSetDecl(
            kind=previous.kind,
            name=previous.name,
            apis=previous.apis,
            number=previous.number,
            annex=previous.annex,
            requirements=previous.requirements + new_requirements,
            comments=tuple(dict.fromkeys((*previous.comments, *declaration.comments))),
        )
    return tuple(merged)


def _note_subject(
    element: ET.Element, parents: Mapping[ET.Element, ET.Element]
) -> str | None:
    if element.tag in {"types", "commands", "enums"}:
        return element.get("namespace")
    if element.tag in {"enum", "feature", "extension"}:
        return element.get("name")
    if element.tag == "command":
        prototype = element.find("proto")
        return _declaration_name(prototype) if prototype is not None else None
    if element.tag == "require":
        owner = parents.get(element)
        return owner.get("name") if owner is not None else None
    return None


def _parse_notes(root: ET.Element) -> tuple[RegistryNoteDecl, ...]:
    parents = {child: parent for parent in root.iter() for child in parent}
    notes: list[RegistryNoteDecl] = []
    for parent in root.iter():
        if not isinstance(parent.tag, str):
            continue
        for child in parent:
            if isinstance(child.tag, str):
                continue
            text = _normalize_c_declaration(child.text or "")
            if text:
                notes.append(
                    RegistryNoteDecl(
                        parent=str(parent.tag),
                        subject=_note_subject(parent, parents),
                        text=text,
                    )
                )
    return tuple(notes)


def _ensure_unique(label: str, names: Sequence[str]) -> None:
    seen: set[str] = set()
    duplicates: set[str] = set()
    for name in names:
        if name in seen:
            duplicates.add(name)
        seen.add(name)
    if duplicates:
        joined = ", ".join(sorted(duplicates))
        raise RegistryError(f"duplicate {label} names: {joined}")


def parse_registry(path: Path = DEFAULT_REGISTRY) -> Registry:
    """Parse the registry into language-neutral declarations."""

    parser = ET.XMLParser(target=ET.TreeBuilder(insert_comments=True))
    root = ET.fromstring(path.read_bytes(), parser=parser)
    if root.tag != "registry":
        raise RegistryError(f"expected <registry> root, found <{root.tag}>")
    _validate_supported_xml(root)

    types = _parse_types(root)
    registry = Registry(
        types=types,
        defines=_parse_defines(types),
        enums=_parse_enums(root),
        commands=_parse_commands(root),
        api_sets=_parse_api_sets(root),
        comments=_child_comments(root),
        notes=_parse_notes(root),
    )
    _ensure_unique("type", [declaration.name for declaration in registry.types])
    _ensure_unique("define", [declaration.name for declaration in registry.defines])
    _ensure_unique("enum", [declaration.name for declaration in registry.enums])
    _ensure_unique("command", [declaration.name for declaration in registry.commands])
    _ensure_unique("API set", [declaration.name for declaration in registry.api_sets])
    return registry


def verify_registry(path: Path, source: SourceInfo) -> str:
    """Return the registry digest after checking it against source.toml."""

    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    if digest != source.sha256:
        raise RegistryError(
            f"registry SHA-256 is {digest}, expected {source.sha256} from source.toml"
        )
    return digest


def _basetype_kind(declaration: TypeDecl) -> tuple[str, str]:
    opaque = _OPAQUE_STRUCT_DECLARATION.fullmatch(declaration.declaration)
    if opaque is not None:
        alias = opaque.group("alias")
        if declaration.name not in {opaque.group("tag"), alias}:
            raise RegistryError(
                f"opaque type {declaration.name!r} does not match its declaration"
            )
        return "opaque", opaque.group("tag")

    typedef = _TYPEDEF_DECLARATION.fullmatch(declaration.declaration)
    if typedef is None or typedef.group("name") != declaration.name:
        raise RegistryError(
            f"unsupported base type declaration {declaration.declaration!r}"
        )
    source = typedef.group("source")
    expression = _CTYPES_TYPEDEFS.get(source)
    if expression is None:
        raise RegistryError(
            f"unsupported ctypes source type {source!r} for {declaration.name}"
        )
    return "alias", expression


def _ctypes_base_expressions(
    registry: Registry, *, alias_prefix: str
) -> dict[str, str]:
    expressions = {
        "void": "None",
        "char": "_ctypes.c_char",
    }
    for declaration in registry.types:
        if declaration.category == "basetype":
            kind, detail = _basetype_kind(declaration)
            expression = f"{alias_prefix}{declaration.name}"
            expressions[declaration.name] = expression
            if kind == "opaque":
                expressions[f"struct {detail}"] = expression
        elif declaration.category == "funcpointer":
            expressions[declaration.name] = f"{alias_prefix}{declaration.name}"
    return expressions


def _split_c_type(c_type: str, known_types: Mapping[str, str]) -> tuple[str, int, bool]:
    tokens = c_type.replace("*", " * ").split()
    is_const = "const" in tokens
    tokens = [token for token in tokens if token != "const"]
    pointer_depth = tokens.count("*")
    words = [token for token in tokens if token != "*"]
    base = " ".join(words)

    # Function-pointer declarations include parameter names, unlike command
    # parameter metadata. Remove one trailing identifier only when doing so
    # reveals a known C type.
    if base not in known_types and len(words) > 1:
        unnamed = " ".join(words[:-1])
        if unnamed in known_types:
            base = unnamed

    if base not in known_types:
        raise RegistryError(f"unsupported C type {c_type!r}")
    return base, pointer_depth, is_const


def _ctypes_expression(c_type: str, known_types: Mapping[str, str]) -> str:
    base, pointer_depth, is_const = _split_c_type(c_type, known_types)
    if pointer_depth == 0:
        return known_types[base]

    if base in {"void", "ALvoid", "ALCvoid"}:
        expression = "_ctypes.c_void_p"
        pointer_depth -= 1
    elif is_const and base in {"char", "ALchar", "ALCchar"}:
        expression = "_ctypes.c_char_p"
        pointer_depth -= 1
    else:
        expression = known_types[base]

    for _ in range(pointer_depth):
        expression = f"_ctypes.POINTER({expression})"
    return expression


def _parse_function_pointer(
    declaration: TypeDecl, known_types: Mapping[str, str]
) -> FunctionPointerDecl:
    match = _FUNCTION_POINTER_DECLARATION.fullmatch(declaration.declaration)
    if match is None or match.group("name") != declaration.name:
        raise RegistryError(
            f"unsupported function pointer declaration {declaration.declaration!r}"
        )

    raw_parameters = match.group("parameters").strip()
    if not raw_parameters or raw_parameters == "void":
        parameter_types: tuple[str, ...] = ()
    else:
        parameter_types = tuple(
            parameter.strip() for parameter in raw_parameters.split(",")
        )
        for parameter in parameter_types:
            _split_c_type(parameter, known_types)

    return FunctionPointerDecl(
        name=declaration.name,
        return_type=match.group("return_type"),
        parameter_types=parameter_types,
    )


def _function_pointer_declarations(
    registry: Registry, known_types: Mapping[str, str]
) -> tuple[FunctionPointerDecl, ...]:
    return tuple(
        _parse_function_pointer(declaration, known_types)
        for declaration in registry.types
        if declaration.category == "funcpointer"
    )


def _resolve_constants(enums: Sequence[EnumDecl]) -> dict[str, ConstantValue]:
    raw_values = {declaration.name: declaration.value for declaration in enums}
    resolved: dict[str, ConstantValue] = {}

    def resolve(name: str, trail: tuple[str, ...] = ()) -> ConstantValue:
        existing = resolved.get(name)
        if existing is not None:
            return existing
        if name in trail:
            chain = " -> ".join((*trail, name))
            raise RegistryError(f"cyclic enum alias: {chain}")

        value = raw_values[name]
        if _INTEGER_LITERAL.fullmatch(value):
            if value.lower().startswith(("0x", "-0x")):
                literal = value
            else:
                literal = str(int(value, 10))
            result = ConstantValue(literal, "int")
        elif _FLOAT_LITERAL.fullmatch(value):
            result = ConstantValue(repr(float(value[:-1])), "float")
        elif _IDENTIFIER.fullmatch(value):
            external = _EXTERNAL_CONSTANTS.get(value)
            if external is not None:
                result = ConstantValue(*external)
            elif value in raw_values:
                result = resolve(value, (*trail, name))
            else:
                raise RegistryError(f"enum {name} aliases unknown enum {value}")
        else:
            raise RegistryError(f"unsupported enum value {value!r} for {name}")

        resolved[name] = result
        return result

    for enum in enums:
        resolve(enum.name)
    return resolved


def _resolve_defines(
    defines: Sequence[DefineDecl], enum_values: Mapping[str, ConstantValue]
) -> dict[str, ConstantValue | None]:
    raw_values = {declaration.name: declaration.replacement for declaration in defines}
    resolved: dict[str, ConstantValue | None] = {}

    def resolve(name: str, trail: tuple[str, ...] = ()) -> ConstantValue | None:
        if name in resolved:
            return resolved[name]
        if name in trail:
            chain = " -> ".join((*trail, name))
            raise RegistryError(f"cyclic define alias: {chain}")

        replacement = raw_values[name]
        result: ConstantValue | None
        if replacement is None:
            result = None
        elif _INTEGER_LITERAL.fullmatch(replacement):
            if replacement.lower().startswith(("0x", "-0x")):
                literal = replacement
            else:
                literal = str(int(replacement, 10))
            result = ConstantValue(literal, "int")
        elif _FLOAT_LITERAL.fullmatch(replacement):
            result = ConstantValue(repr(float(replacement[:-1])), "float")
        elif replacement.startswith(('"', "'")):
            try:
                value = ast.literal_eval(replacement)
            except (SyntaxError, ValueError):
                result = None
            else:
                result = (
                    ConstantValue(repr(value), "str")
                    if isinstance(value, str)
                    else None
                )
        elif _IDENTIFIER.fullmatch(replacement):
            if replacement in raw_values:
                result = resolve(replacement, (*trail, name))
            else:
                result = enum_values.get(replacement)
        else:
            result = None

        resolved[name] = result
        return result

    for declaration in defines:
        resolve(declaration.name)
    return resolved


def _generated_header(source: SourceInfo, digest: str) -> list[str]:
    return [
        "# Generated by tools/generate_bindings.py. Do not edit by hand.",
        f"# OpenAL Soft {source.version}: {source.commit}",
        f"# Source: {source.source_url}",
        f"# Registry SHA-256: {digest}",
        "# Registry notice: LICENSES/OpenAL-Registry.txt",
        "",
        "from __future__ import annotations",
        "",
    ]


def render_constants(registry: Registry, source: SourceInfo, digest: str) -> str:
    """Render every registry enum and Python-representable C definition."""

    enum_values = _resolve_constants(registry.enums)
    define_values = _resolve_defines(registry.defines, enum_values)
    public_defines = [
        declaration
        for declaration in registry.defines
        if define_values[declaration.name] is not None
    ]
    lines = _generated_header(source, digest)
    lines.extend(["from typing import Final", ""])
    for declaration in public_defines:
        value = define_values[declaration.name]
        assert value is not None
        lines.append(
            f"{declaration.name}: Final[{value.python_type}] = {value.literal}"
        )
    if public_defines:
        lines.append("")
    for enum in registry.enums:
        value = enum_values[enum.name]
        lines.append(f"{enum.name}: Final[{value.python_type}] = {value.literal}")
    lines.extend(["", "__all__ = ("])
    lines.extend(f"    {declaration.name!r}," for declaration in public_defines)
    lines.extend(f"    {enum.name!r}," for enum in registry.enums)
    lines.extend([")", ""])
    return "\n".join(lines)


def render_ctypes_types(registry: Registry, source: SourceInfo, digest: str) -> str:
    """Render concrete ctypes aliases, opaque handles, and callback types."""

    lines = _generated_header(source, digest)
    lines.extend(["import ctypes as _ctypes", ""])

    for declaration in registry.types:
        if declaration.category != "basetype":
            continue
        kind, detail = _basetype_kind(declaration)
        if kind == "opaque":
            if lines[-1]:
                lines.append("")
            lines.extend(
                [
                    f"class {declaration.name}(_ctypes.Structure):",
                    f'    """Opaque C structure ``{detail}``."""',
                    "",
                    "    pass",
                    "",
                ]
            )
        else:
            lines.append(f"{declaration.name} = {detail}")

    known_types = _ctypes_base_expressions(registry, alias_prefix="")
    callbacks = _function_pointer_declarations(registry, known_types)
    if callbacks:
        lines.append("")
    for callback in callbacks:
        signature = [
            _ctypes_expression(callback.return_type, known_types),
            *(
                _ctypes_expression(parameter, known_types)
                for parameter in callback.parameter_types
            ),
        ]
        lines.append(f"{callback.name} = _ctypes.CFUNCTYPE({', '.join(signature)})")

    public_names = [
        declaration.name
        for declaration in registry.types
        if declaration.category in {"basetype", "funcpointer"}
    ]
    lines.extend(["", "__all__ = ("])
    lines.extend(f"    {name!r}," for name in public_names)
    lines.extend([")", ""])
    return "\n".join(lines)


def _command_extensions(registry: Registry) -> dict[str, str]:
    extensions: dict[str, str] = {}
    for api_set in registry.api_sets:
        if api_set.kind != "extension":
            continue
        for requirement in api_set.requirements:
            for member in requirement.members:
                if member.kind != "command":
                    continue
                previous = extensions.get(member.name)
                if previous is not None and previous != api_set.name:
                    raise RegistryError(
                        f"command {member.name!r} belongs to multiple extensions"
                    )
                extensions[member.name] = api_set.name
    return extensions


def _member_extensions(registry: Registry, kind: str) -> dict[str, tuple[str, ...]]:
    memberships: dict[str, list[str]] = {}
    for api_set in registry.api_sets:
        if api_set.kind != "extension":
            continue
        for requirement in api_set.requirements:
            for member in requirement.members:
                if member.kind == kind:
                    memberships.setdefault(member.name, []).append(api_set.name)
    return {name: tuple(dict.fromkeys(items)) for name, items in memberships.items()}


def _snake_case(name: str) -> str:
    first = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", "_", name)
    second = re.sub(r"(?<=[A-Z])(?=[A-Z][a-z])", "_", first)
    result = second.lower()
    return f"{result}_" if keyword.iskeyword(result) else result


def _command_python_name(command: CommandDecl) -> str:
    prefix = "al" if command.namespace == "AL" else "alc"
    name = command.name
    if name.startswith(prefix) and len(name) > len(prefix):
        suffix = name[len(prefix) :]
        if suffix[0].isupper():
            name = suffix
    return _snake_case(name)


def _parameter_python_name(name: str) -> str:
    if name in {"n", "nb"}:
        return "count"
    return _snake_case(name)


def _enum_group_python_name(namespace: str, group: str) -> str:
    return f"{namespace}{group}"


def _property_python_name(enum_name: str, object_name: str) -> str:
    name = enum_name.removeprefix("ALC_").removeprefix("AL_")
    prefixes = {
        "source": "SOURCE_",
        "buffer": "BUFFER_",
        "listener": "LISTENER_",
        "effect": "EFFECT_",
        "filter": "FILTER_",
        "auxiliary effect slot": "EFFECTSLOT_",
        "context": "CONTEXT_",
    }
    name = name.removeprefix(prefixes.get(object_name, ""))
    return name.lower()


def _property_type(value_types: tuple[str, ...]) -> tuple[str, int | None] | None:
    preferred = sorted(
        value_types,
        key=lambda item: (
            not item.startswith("ALfloat"),
            not item.startswith("ALdouble"),
            item,
        ),
    )
    if not preferred:
        return None
    match = _ARRAY_PROPERTY_TYPE.fullmatch(preferred[0])
    if match is None:
        return None
    arity = match.group("arity")
    return match.group("base"), int(arity) if arity is not None else None


def _property_accessors(
    object_name: str,
    base_type: str,
    arity: int | None,
    command_names: set[str],
) -> tuple[str | None, str | None]:
    stems = {
        "source": "Source",
        "buffer": "Buffer",
        "listener": "Listener",
        "effect": "Effect",
        "filter": "Filter",
        "auxiliary effect slot": "AuxiliaryEffectSlot",
    }
    stem = stems.get(object_name)
    if stem is None or base_type.endswith("void") or base_type.endswith("void*"):
        return None, None

    is_vector = arity is not None
    if base_type in {"ALfloat"}:
        suffix = "fv" if is_vector else "f"
    elif base_type in {"ALdouble"}:
        suffix = "dvSOFT" if is_vector else "dSOFT"
    elif base_type in {"ALint64SOFT", "ALuint64SOFT"}:
        suffix = "i64vSOFT" if is_vector else "i64SOFT"
    elif base_type in {"ALboolean", "ALenum", "ALint", "ALuint"}:
        suffix = "iv" if is_vector else "i"
    else:
        return None, None

    getter = f"alGet{stem}{suffix}"
    setter = f"al{stem}{suffix}"
    return (
        getter if getter in command_names else None,
        setter if setter in command_names else None,
    )


def _validate_overrides(registry: Registry, overrides: SemanticOverrides) -> None:
    enum_by_name = {item.name: item for item in registry.enums}
    command_by_name = {item.name: item for item in registry.commands}
    for name, property_override in overrides.properties.items():
        enum = enum_by_name.get(name)
        if enum is None or not enum.properties:
            raise RegistryError(
                f"property override refers to unknown property {name!r}"
            )
        if property_override.reason is None and (
            property_override.objects is not None
            or property_override.value_types is not None
            or property_override.enum_groups is not None
            or not property_override.generate
        ):
            raise RegistryError(f"property override {name!r} must explain its change")

    for name, command_override in overrides.commands.items():
        command = command_by_name.get(name)
        if command is None:
            raise RegistryError(f"command override refers to unknown command {name!r}")
        parameters = {item.name for item in command.parameters}
        for parameter_name, length in command_override.lengths.items():
            if parameter_name not in parameters:
                raise RegistryError(
                    f"command override {name!r} refers to unknown parameter "
                    f"{parameter_name!r}"
                )
            if not length.isdigit() and length not in parameters:
                raise RegistryError(
                    f"command override {name!r} length refers to unknown "
                    f"parameter {length!r}"
                )
        for parameter_name, direction in command_override.directions.items():
            if parameter_name not in parameters:
                raise RegistryError(
                    f"command override {name!r} refers to unknown parameter "
                    f"{parameter_name!r}"
                )
            if direction not in {"in", "out", "inout"}:
                raise RegistryError(
                    f"command override {name!r} has invalid direction {direction!r}"
                )


def build_enum_groups(
    registry: Registry, overrides: SemanticOverrides
) -> tuple[tuple[str, str, str, tuple[str, ...], bool], ...]:
    """Build enum groups used by Python type annotations and documentation."""

    members: dict[tuple[str, str], list[str]] = {}
    bitmasks: set[tuple[str, str]] = set()
    for enum in registry.enums:
        override = overrides.properties.get(enum.name)
        enum_groups = (
            override.enum_groups
            if override is not None and override.enum_groups is not None
            else enum.groups
        )
        groups = (*enum_groups, *((enum.block_group,) if enum.block_group else ()))
        for group in groups:
            members.setdefault((enum.namespace, group), []).append(enum.name)
        for property_ in enum.properties:
            if property_.kind == "bitmask":
                bitmasks.update((enum.namespace, group) for group in property_.groups)
    return tuple(
        (
            namespace,
            group,
            _enum_group_python_name(namespace, group),
            tuple(dict.fromkeys(group_members)),
            (namespace, group) in bitmasks,
        )
        for (namespace, group), group_members in members.items()
    )


def build_effective_properties(
    registry: Registry, overrides: SemanticOverrides
) -> tuple[EffectiveProperty, ...]:
    """Normalize XML properties into safe generated descriptor definitions."""

    _validate_overrides(registry, overrides)
    command_names = {item.name for item in registry.commands}
    enum_extensions = _member_extensions(registry, "enum")
    group_names = {
        (namespace, group): python_name
        for namespace, group, python_name, _members, _bitmask in build_enum_groups(
            registry, overrides
        )
    }
    properties: list[EffectiveProperty] = []
    for enum in registry.enums:
        override = overrides.properties.get(enum.name)
        for property_ in enum.properties:
            objects = (
                override.objects
                if override is not None and override.objects is not None
                else property_.objects
            )
            value_types = (
                override.value_types
                if override is not None and override.value_types is not None
                else property_.value_types
            )
            type_info = _property_type(value_types)
            base_type, arity = type_info if type_info is not None else ("", None)
            prose = " ".join(
                item for item in (enum.comment, *enum.comments) if item is not None
            )
            writable = "query only" not in prose.lower()
            if override is not None and override.writable is not None:
                writable = override.writable
            requested_generation = override.generate if override is not None else True
            enum_type = (
                next(
                    (
                        group_names[(enum.namespace, group)]
                        for group in property_.groups
                        if (enum.namespace, group) in group_names
                    ),
                    None,
                )
                if base_type in {"ALenum", "ALCenum"}
                else None
            )
            for object_name in objects:
                getter, setter = _property_accessors(
                    object_name, base_type, arity, command_names
                )
                generate = requested_generation and getter is not None
                properties.append(
                    EffectiveProperty(
                        namespace=enum.namespace,
                        object_name=object_name,
                        enum_name=enum.name,
                        python_name=_property_python_name(enum.name, object_name),
                        value_types=value_types,
                        range=property_.range,
                        default=property_.default,
                        groups=property_.groups,
                        object_class=property_.object_class,
                        kind=property_.kind,
                        readable=True,
                        writable=writable,
                        generate=generate,
                        getter=getter if generate else None,
                        setter=setter if generate and writable else None,
                        arity=arity,
                        enum_type=enum_type,
                        extensions=enum_extensions.get(enum.name, ()),
                        comment=prose or None,
                    )
                )

    seen: set[tuple[str, str]] = set()
    for effective_property in properties:
        key = effective_property.object_name, effective_property.python_name
        if key in seen:
            raise RegistryError(
                f"duplicate generated property {effective_property.object_name}."
                f"{effective_property.python_name}"
            )
        seen.add(key)
    return tuple(properties)


def _parameter_direction(
    parameter: ParameterDecl,
    known_types: Mapping[str, str],
    opaque_types: set[str],
) -> str:
    base, pointer_depth, is_const = _split_c_type(parameter.c_type, known_types)
    if pointer_depth == 0 or (base in opaque_types and pointer_depth == 1):
        return "in"
    if is_const:
        return "in"
    if base in {"void", "ALvoid", "ALCvoid"} and pointer_depth == 1:
        return "in"
    return "out"


def _needs_result_size(
    command: CommandDecl, outputs: Sequence[WrapperParameter]
) -> bool:
    unknown = [item for item in outputs if item.length is None]
    if len(unknown) != 1:
        return False
    parameter = unknown[0]
    lowered = parameter.name.lower()
    return lowered.endswith("values") or bool(
        re.search(
            r"(?:booleanv|integerv|floatv|doublev|fv|iv|dv|i64v)(?:Direct)?(?:SOFT|EXT)?\Z",
            command.name,
        )
    )


def build_command_wrappers(
    registry: Registry, overrides: SemanticOverrides
) -> tuple[CommandWrapper, ...]:
    """Infer Python call signatures and marshalling from command declarations."""

    _validate_overrides(registry, overrides)
    known_types = _ctypes_base_expressions(registry, alias_prefix="")
    opaque_types = {
        declaration.name
        for declaration in registry.types
        if declaration.category == "basetype"
        and _basetype_kind(declaration)[0] == "opaque"
    }
    extensions = _command_extensions(registry)
    wrappers: list[CommandWrapper] = []
    for command in registry.commands:
        override = overrides.commands.get(command.name)
        provisional: list[WrapperParameter] = []
        for parameter in command.parameters:
            direction = _parameter_direction(parameter, known_types, opaque_types)
            length = parameter.length
            if override is not None:
                direction = override.directions.get(parameter.name, direction)
                length = override.lengths.get(parameter.name, length)
            provisional.append(
                WrapperParameter(
                    name=parameter.name,
                    python_name=_parameter_python_name(parameter.name),
                    c_type=parameter.c_type,
                    direction=direction,
                    length=length,
                    group=parameter.group,
                    object_class=parameter.object_class,
                    visible=direction != "out",
                )
            )

        directions_by_length: dict[str, set[str]] = {}
        parameter_names = {item.name for item in provisional}
        for wrapper_parameter in provisional:
            if wrapper_parameter.length in parameter_names:
                directions_by_length.setdefault(wrapper_parameter.length, set()).add(
                    wrapper_parameter.direction
                )
        hidden_controllers = {
            name
            for name, directions in directions_by_length.items()
            if directions == {"in"}
        }
        parameters = tuple(
            WrapperParameter(
                name=item.name,
                python_name=item.python_name,
                c_type=item.c_type,
                direction=item.direction,
                length=item.length,
                group=item.group,
                object_class=item.object_class,
                visible=item.visible and item.name not in hidden_controllers,
            )
            for item in provisional
        )
        visible_names = [item.python_name for item in parameters if item.visible]
        if len(visible_names) != len(set(visible_names)):
            raise RegistryError(
                f"generated parameter names collide for command {command.name!r}"
            )
        outputs = [item for item in parameters if item.direction == "out"]
        comment = " ".join(
            item for item in (command.comment, *command.comments) if item is not None
        )
        wrappers.append(
            CommandWrapper(
                namespace=command.namespace,
                name=command.name,
                python_name=_command_python_name(command),
                return_type=command.return_type,
                return_group=command.return_group,
                parameters=parameters,
                result_size=_needs_result_size(command, outputs),
                extension=extensions.get(command.name),
                comment=comment or None,
            )
        )

    for namespace in {item.namespace for item in wrappers}:
        names = [item.python_name for item in wrappers if item.namespace == namespace]
        _ensure_unique(f"{namespace} Python command", names)
    return tuple(wrappers)


def render_ctypes_functions(registry: Registry, source: SourceInfo, digest: str) -> str:
    """Render callable ctypes prototypes and command loading metadata."""

    lines = _generated_header(source, digest)
    lines.extend(
        [
            "import ctypes as _ctypes",
            "",
            "from pyalsoft._generated import types as _types",
            "",
        ]
    )
    known_types = _ctypes_base_expressions(registry, alias_prefix="_types.")

    for command in registry.commands:
        signature = [
            _ctypes_expression(command.return_type, known_types),
            *(
                _ctypes_expression(parameter.c_type, known_types)
                for parameter in command.parameters
            ),
        ]
        lines.append(
            f"{command.function_pointer} = _ctypes.CFUNCTYPE({', '.join(signature)})"
        )

    lines.extend(["", "PROTOTYPES = {"])
    lines.extend(
        f"    {command.name!r}: {command.function_pointer},"
        for command in registry.commands
    )
    lines.extend(["}", "", "COMMAND_NAMESPACES = {"])
    lines.extend(
        f"    {command.name!r}: {command.namespace!r}," for command in registry.commands
    )
    lines.extend(["}", "", "COMMAND_EXPORTS = {"])
    lines.extend(
        f"    {command.name!r}: {command.export!r}," for command in registry.commands
    )

    command_extensions = _command_extensions(registry)
    lines.extend(["}", "", "COMMAND_EXTENSIONS = {"])
    lines.extend(
        f"    {name!r}: {extension!r},"
        for name, extension in command_extensions.items()
    )
    lines.extend(["}", "", "EXTENSION_APIS = {"])
    lines.extend(
        f"    {api_set.name!r}: {api_set.apis!r},"
        for api_set in registry.api_sets
        if api_set.kind == "extension"
    )

    lines.extend(["}", "", "__all__ = ("])
    lines.extend(f"    {command.function_pointer!r}," for command in registry.commands)
    lines.extend(
        [
            "    'PROTOTYPES',",
            "    'COMMAND_NAMESPACES',",
            "    'COMMAND_EXPORTS',",
            "    'COMMAND_EXTENSIONS',",
            "    'EXTENSION_APIS',",
            ")",
            "",
        ]
    )
    return "\n".join(lines)


def _enum_member_python_name(name: str, namespace: str) -> str:
    result = name.removeprefix(f"{namespace}_")
    return f"VALUE_{result}" if result[0].isdigit() else result


def render_enums(
    registry: Registry,
    source: SourceInfo,
    digest: str,
    overrides: SemanticOverrides,
) -> str:
    """Render semantic enum groups without removing the flat C constants."""

    groups = build_enum_groups(registry, overrides)
    lines = _generated_header(source, digest)
    lines.extend(
        [
            "from enum import IntEnum, IntFlag",
            "",
            "from pyalsoft._generated import constants as _constants",
            "",
        ]
    )
    for namespace, group, python_name, members, bitmask in groups:
        base = "IntFlag" if bitmask else "IntEnum"
        lines.extend(
            [
                f"class {python_name}({base}):",
                f'    """Values in the registry ``{group}`` group."""',
                "",
            ]
        )
        python_members = [
            _enum_member_python_name(member, namespace) for member in members
        ]
        if len(python_members) != len(set(python_members)):
            raise RegistryError(f"enum member names collide in {python_name}")
        lines.extend(
            f"    {member_name} = _constants.{member}"
            for member_name, member in zip(python_members, members, strict=True)
        )
        lines.append("")

    lines.extend(["__all__ = ("])
    lines.extend(f"    {python_name!r}," for _ns, _g, python_name, _m, _b in groups)
    lines.extend([")", ""])
    return "\n".join(lines)


def _render_wrapper_parameter(parameter: WrapperParameter) -> str:
    return (
        "WrapperParameterSpec("
        f"{parameter.name!r}, {parameter.python_name!r}, {parameter.c_type!r}, "
        f"{parameter.direction!r}, {parameter.length!r}, {parameter.group!r}, "
        f"{parameter.object_class!r}, {parameter.visible!r})"
    )


def render_semantics(
    registry: Registry,
    source: SourceInfo,
    digest: str,
    overrides: SemanticOverrides,
) -> str:
    """Render normalized enum, property, and command wrapper semantics."""

    groups = build_enum_groups(registry, overrides)
    properties = build_effective_properties(registry, overrides)
    wrappers = build_command_wrappers(registry, overrides)
    lines = _generated_header(source, digest)
    lines.extend(
        [
            "from pyalsoft._specs import (",
            "    CommandWrapperSpec,",
            "    EnumGroupSpec,",
            "    ObjectPropertySpec,",
            "    WrapperParameterSpec,",
            ")",
            "",
            "ENUM_GROUPS: tuple[EnumGroupSpec, ...] = (",
        ]
    )
    lines.extend(
        "    EnumGroupSpec("
        f"{namespace!r}, {group!r}, {python_name!r}, {members!r}, {bitmask!r}),"
        for namespace, group, python_name, members, bitmask in groups
    )
    lines.extend([")", "", "OBJECT_PROPERTIES: tuple[ObjectPropertySpec, ...] = ("])
    lines.extend(
        "    ObjectPropertySpec("
        f"{item.namespace!r}, {item.object_name!r}, {item.enum_name!r}, "
        f"{item.python_name!r}, {item.value_types!r}, {item.range!r}, "
        f"{item.default!r}, {item.groups!r}, {item.object_class!r}, "
        f"{item.kind!r}, {item.readable!r}, {item.writable!r}, "
        f"{item.getter!r}, {item.setter!r}, {item.arity!r}, "
        f"{item.enum_type!r}, {item.extensions!r}, {item.comment!r}),"
        for item in properties
    )
    lines.extend([")", "", "COMMAND_WRAPPERS: tuple[CommandWrapperSpec, ...] = ("])
    for wrapper in wrappers:
        rendered_parameters = ", ".join(
            _render_wrapper_parameter(item) for item in wrapper.parameters
        )
        rendered_parameters = (
            f"({rendered_parameters},)" if rendered_parameters else "()"
        )
        lines.append(
            "    CommandWrapperSpec("
            f"{wrapper.namespace!r}, {wrapper.name!r}, {wrapper.python_name!r}, "
            f"{wrapper.return_type!r}, {wrapper.return_group!r}, "
            f"{rendered_parameters}, {wrapper.result_size!r}, "
            f"{wrapper.extension!r}, {wrapper.comment!r}),"
        )
    lines.extend(
        [
            ")",
            "",
            "ENUM_GROUPS_BY_NAME = {",
            "    (item.namespace, item.name): item for item in ENUM_GROUPS",
            "}",
            "OBJECT_PROPERTIES_BY_KEY = {",
            "    (item.object_name, item.enum_name): item for item in OBJECT_PROPERTIES",
            "}",
            "COMMAND_WRAPPERS_BY_NAME = {item.name: item for item in COMMAND_WRAPPERS}",
            "",
            "__all__ = (",
            "    'COMMAND_WRAPPERS',",
            "    'COMMAND_WRAPPERS_BY_NAME',",
            "    'ENUM_GROUPS',",
            "    'ENUM_GROUPS_BY_NAME',",
            "    'OBJECT_PROPERTIES',",
            "    'OBJECT_PROPERTIES_BY_KEY',",
            ")",
            "",
        ]
    )
    return "\n".join(lines)


def _group_python_names(
    registry: Registry, overrides: SemanticOverrides
) -> dict[tuple[str, str], str]:
    return {
        (namespace, group): python_name
        for namespace, group, python_name, _members, _bitmask in build_enum_groups(
            registry, overrides
        )
    }


def _python_scalar_annotation(
    base: str,
    namespace: str,
    group: str | None,
    group_names: Mapping[tuple[str, str], str],
    function_pointers: set[str],
) -> str:
    if group is not None and (namespace, group) in group_names:
        return f"_enums.{group_names[(namespace, group)]} | int"
    if base in {"ALboolean", "ALCboolean"}:
        return "bool"
    if base in {"ALfloat", "ALdouble", "ALCfloat", "ALCdouble"}:
        return "float"
    if base in function_pointers:
        return f"_types.{base}"
    if base in {"void", "ALvoid", "ALCvoid"} or base.startswith("struct "):
        return "object"
    if base in {"ALCdevice", "ALCcontext"}:
        return "object | None"
    return "int"


def _wrapper_input_annotation(
    parameter: WrapperParameter,
    namespace: str,
    known_types: Mapping[str, str],
    group_names: Mapping[tuple[str, str], str],
    function_pointers: set[str],
) -> str:
    base, pointer_depth, _is_const = _split_c_type(parameter.c_type, known_types)
    scalar = _python_scalar_annotation(
        base, namespace, parameter.group, group_names, function_pointers
    )
    if parameter.direction == "inout":
        return "object"
    if pointer_depth == 0 or (
        base in {"ALCdevice", "ALCcontext"} and pointer_depth == 1
    ):
        return scalar
    if base in {"char", "ALchar", "ALCchar"}:
        return "str | bytes | None"
    if base in {"void", "ALvoid", "ALCvoid"} or base.startswith("struct "):
        return "_api.ReadableBuffer | object"
    return f"Sequence[{scalar}] | None"


def _wrapper_output_annotation(
    parameter: WrapperParameter,
    wrapper: CommandWrapper,
    known_types: Mapping[str, str],
    group_names: Mapping[tuple[str, str], str],
    function_pointers: set[str],
) -> str:
    base, _pointer_depth, _is_const = _split_c_type(parameter.c_type, known_types)
    if base in {"char", "ALchar", "ALCchar"}:
        scalar = "str"
    elif base in {"void", "ALvoid", "ALCvoid"}:
        scalar = "object | None"
    else:
        scalar = _python_scalar_annotation(
            base, wrapper.namespace, parameter.group, group_names, function_pointers
        )
    is_vector = (parameter.length is not None and parameter.length != "1") or (
        parameter.length is None and wrapper.result_size
    )
    return f"tuple[{scalar}, ...]" if is_vector and scalar != "str" else scalar


def _wrapper_return_annotation(
    wrapper: CommandWrapper,
    known_types: Mapping[str, str],
    group_names: Mapping[tuple[str, str], str],
    function_pointers: set[str],
) -> str:
    result_types: list[str] = []
    return_base, return_depth, _is_const = _split_c_type(
        wrapper.return_type, known_types
    )
    if not (return_base == "void" and return_depth == 0):
        if return_depth and return_base in {"char", "ALchar", "ALCchar"}:
            result_types.append("str | None")
        elif return_depth:
            result_types.append("object | None")
        else:
            result_types.append(
                _python_scalar_annotation(
                    return_base,
                    wrapper.namespace,
                    wrapper.return_group,
                    group_names,
                    function_pointers,
                )
            )
    result_types.extend(
        _wrapper_output_annotation(
            item, wrapper, known_types, group_names, function_pointers
        )
        for item in wrapper.parameters
        if item.direction == "out"
    )
    if not result_types:
        return "None"
    if len(result_types) == 1:
        return result_types[0]
    return f"tuple[{', '.join(result_types)}]"


def _wrapper_method_parameters(
    wrapper: CommandWrapper,
    known_types: Mapping[str, str],
    group_names: Mapping[tuple[str, str], str],
    function_pointers: set[str],
) -> list[str]:
    output_lengths = {
        item.length
        for item in wrapper.parameters
        if item.direction == "out" and item.length is not None
    }
    parameters: list[str] = []
    for parameter in wrapper.parameters:
        if not parameter.visible:
            continue
        annotation = _wrapper_input_annotation(
            parameter,
            wrapper.namespace,
            known_types,
            group_names,
            function_pointers,
        )
        default = ""
        if (
            parameter.name in output_lengths
            and parameter.name in {"n", "count"}
            and wrapper.name.startswith(("alGen", "alcGen"))
        ):
            default = " = 1"
        parameters.append(f"{parameter.python_name}: {annotation}{default}")
    if wrapper.result_size:
        parameters.append("result_size: int = 1")
    if wrapper.namespace == "AL" and wrapper.extension == "AL_EXT_direct_context":
        parameters.append("resolution_device: object | None = None")
    return parameters


def _render_command_method(
    wrapper: CommandWrapper,
    known_types: Mapping[str, str],
    group_names: Mapping[tuple[str, str], str],
    function_pointers: set[str],
) -> list[str]:
    parameters = _wrapper_method_parameters(
        wrapper, known_types, group_names, function_pointers
    )
    signature = ", ".join(("self", *parameters))
    return_annotation = _wrapper_return_annotation(
        wrapper, known_types, group_names, function_pointers
    )
    values = ", ".join(
        f"{item.name!r}: {item.python_name}"
        for item in wrapper.parameters
        if item.visible
    )
    args = f"{wrapper.name!r}, {{{values}}}"
    if wrapper.result_size:
        args += ", result_size=result_size"
    if wrapper.namespace == "AL" and wrapper.extension == "AL_EXT_direct_context":
        args += ", resolution_device=resolution_device"
    documentation = wrapper.comment or f"Python wrapper for ``{wrapper.name}``."
    documentation = documentation.replace('"""', "'''")
    if wrapper.extension is not None:
        documentation += f" Requires ``{wrapper.extension}``."
    return [
        f"    def {wrapper.python_name}({signature}) -> {return_annotation}:",
        f'        """{documentation}"""',
        "",
        f"        return cast({return_annotation}, self._invoke({args}))",
        "",
    ]


def render_python_commands(
    registry: Registry,
    source: SourceInfo,
    digest: str,
    overrides: SemanticOverrides,
) -> str:
    """Render Python-value command namespaces over the raw ctypes functions."""

    wrappers = build_command_wrappers(registry, overrides)
    known_types = _ctypes_base_expressions(registry, alias_prefix="")
    group_names = _group_python_names(registry, overrides)
    function_pointers = {
        item.name for item in registry.types if item.category == "funcpointer"
    }
    lines = _generated_header(source, digest)
    lines.extend(
        [
            "from collections.abc import Sequence",
            "from typing import TYPE_CHECKING, cast",
            "",
            "from pyalsoft import _api",
            "from pyalsoft._generated import enums as _enums",
            "from pyalsoft._generated import types as _types",
            "",
            "if TYPE_CHECKING:",
            "    from pyalsoft._generated.objects import (",
            "        AuxiliaryEffectSlot,",
            "        Buffer,",
            "        Effect,",
            "        Filter,",
            "        Listener,",
            "        Source,",
            "    )",
            "",
            "",
            "class ALCommands(_api.CommandNamespace):",
            '    """Python-value wrappers for commands in the AL namespace."""',
            "",
        ]
    )
    for wrapper in wrappers:
        if wrapper.namespace == "AL":
            lines.extend(
                _render_command_method(
                    wrapper, known_types, group_names, function_pointers
                )
            )
    lines.extend(
        [
            "    def source(self, identifier: int) -> Source:",
            '        """Wrap an existing source identifier."""',
            "",
            "        from pyalsoft._generated.objects import Source",
            "",
            "        return Source(self.library, identifier)",
            "",
            "    def buffer(self, identifier: int) -> Buffer:",
            '        """Wrap an existing buffer identifier."""',
            "",
            "        from pyalsoft._generated.objects import Buffer",
            "",
            "        return Buffer(self.library, identifier)",
            "",
            "    def effect(self, identifier: int) -> Effect:",
            '        """Wrap an existing effect identifier."""',
            "",
            "        from pyalsoft._generated.objects import Effect",
            "",
            "        return Effect(self.library, identifier)",
            "",
            "    def filter(self, identifier: int) -> Filter:",
            '        """Wrap an existing filter identifier."""',
            "",
            "        from pyalsoft._generated.objects import Filter",
            "",
            "        return Filter(self.library, identifier)",
            "",
            "    def auxiliary_effect_slot(self, identifier: int) -> AuxiliaryEffectSlot:",
            '        """Wrap an existing auxiliary effect slot identifier."""',
            "",
            "        from pyalsoft._generated.objects import AuxiliaryEffectSlot",
            "",
            "        return AuxiliaryEffectSlot(self.library, identifier)",
            "",
            "    @property",
            "    def listener(self) -> Listener:",
            '        """Return the current context\'s singleton listener."""',
            "",
            "        from pyalsoft._generated.objects import Listener",
            "",
            "        return Listener(self.library)",
            "",
            "",
            "class ALCCommands(_api.CommandNamespace):",
            '    """Python-value wrappers for commands in the ALC namespace."""',
            "",
        ]
    )
    for wrapper in wrappers:
        if wrapper.namespace == "ALC":
            lines.extend(
                _render_command_method(
                    wrapper, known_types, group_names, function_pointers
                )
            )
    lines.extend(["__all__ = ('ALCommands', 'ALCCommands')", ""])
    return "\n".join(lines)


def _property_annotation(property_: EffectiveProperty) -> str:
    if property_.object_class is not None:
        classes = {
            "source": "Source",
            "buffer": "Buffer",
            "effect": "Effect",
            "filter": "Filter",
            "auxiliary effect slot": "AuxiliaryEffectSlot",
        }
        return f"{classes[property_.object_class]} | None"
    type_info = _property_type(property_.value_types)
    if type_info is None:
        return "object"
    base, arity = type_info
    if property_.enum_type is not None:
        scalar = f"_enums.{property_.enum_type}"
    elif base in {"ALboolean", "ALCboolean"}:
        scalar = "bool"
    elif base in {"ALfloat", "ALdouble", "ALCfloat", "ALCdouble"}:
        scalar = "float"
    else:
        scalar = "int"
    if arity is None:
        return scalar
    return f"tuple[{', '.join(scalar for _ in range(arity))}]"


def render_objects(
    registry: Registry,
    source: SourceInfo,
    digest: str,
    overrides: SemanticOverrides,
) -> str:
    """Render typed OpenAL object handles and property descriptors."""

    properties = build_effective_properties(registry, overrides)
    classes = (
        ("Buffer", "buffer", "_objects.ALObject"),
        ("Effect", "effect", "_objects.ALObject"),
        ("Filter", "filter", "_objects.ALObject"),
        ("AuxiliaryEffectSlot", "auxiliary effect slot", "_objects.ALObject"),
        ("Source", "source", "_objects.ALObject"),
        ("Listener", "listener", "_objects.ALSingletonObject"),
    )
    lines = _generated_header(source, digest)
    lines.extend(
        [
            "from pyalsoft import _objects",
            "from pyalsoft._generated import enums as _enums",
            "",
        ]
    )
    for class_name, object_name, base in classes:
        lines.extend(
            [
                f"class {class_name}({base}):",
                f'    """Typed handle for an OpenAL {object_name}."""',
                "",
                f"    object_name = {object_name!r}",
                "",
            ]
        )
        generated = [
            item
            for item in properties
            if item.object_name == object_name and item.getter is not None
        ]
        for property_ in generated:
            annotation = _property_annotation(property_)
            lines.extend(
                [
                    f"    {property_.python_name}: _objects.ALProperty[{annotation}] = (",
                    "        _objects.ALProperty("
                    f"{property_.object_name!r}, {property_.enum_name!r})",
                    "    )",
                    "",
                ]
            )
        if not generated:
            lines.append("    pass")
            lines.append("")
    lines.extend(["__all__ = ("])
    lines.extend(f"    {class_name!r}," for class_name, _object_name, _base in classes)
    lines.extend([")", ""])
    return "\n".join(lines)


def render_extensions(registry: Registry, source: SourceInfo, digest: str) -> str:
    """Render discoverable properties for all registry extensions."""

    extensions = tuple(item for item in registry.api_sets if item.kind == "extension")
    python_names = [_snake_case(item.name) for item in extensions]
    _ensure_unique("Python extension", python_names)
    lines = _generated_header(source, digest)
    lines.extend(
        [
            "from pyalsoft import _extensions",
            "",
            "",
            "class ExtensionCapabilities(_extensions.ExtensionNamespace):",
            '    """Capabilities for every extension declared by the registry."""',
            "",
        ]
    )
    for api_set, python_name in zip(extensions, python_names, strict=True):
        lines.extend(
            [
                "    @property",
                f"    def {python_name}(self) -> _extensions.Extension:",
                f'        """Return capabilities for ``{api_set.name}``."""',
                "",
                f"        return self._get({api_set.name!r})",
                "",
            ]
        )
    lines.extend(["__all__ = ('ExtensionCapabilities',)", ""])
    return "\n".join(lines)


def _markdown(value: str | None) -> str:
    if value is None:
        return "n/a"
    return value.replace("|", "\\|").replace("\n", " ")


def render_documentation(
    registry: Registry,
    source: SourceInfo,
    digest: str,
    overrides: SemanticOverrides,
) -> str:
    """Render a human-readable reference from the same semantic model as code."""

    groups = build_enum_groups(registry, overrides)
    properties = build_effective_properties(registry, overrides)
    wrappers = build_command_wrappers(registry, overrides)
    known_types = _ctypes_base_expressions(registry, alias_prefix="")
    group_names = _group_python_names(registry, overrides)
    callbacks = {item.name for item in registry.types if item.category == "funcpointer"}
    lines = [
        "<!-- Generated by tools/generate_bindings.py. Do not edit by hand. -->",
        "",
        "# PyALSoft generated API reference",
        "",
        f"Registry: OpenAL Soft {source.version} (`{source.commit}`)",
        "",
        f"Registry SHA-256: `{digest}`",
        "",
        "The low-level `OpenALLibrary.al*` and `OpenALLibrary.alc*` attributes "
        "remain raw `ctypes` functions. The `library.al` and `library.alc` "
        "namespaces below accept Python values, infer input array lengths, "
        "allocate output parameters, and return normal Python values.",
        "",
        "```python",
        "import pyalsoft",
        "",
        "library = pyalsoft.load()",
        "device = library.alc.open_device(None)",
        "(source_id,) = library.al.gen_sources()",
        "source = library.al.source(source_id)",
        "source.pitch = 1.25",
        "```",
        "",
        "Extensions are exposed as generated capability objects. For example, "
        "`library.extensions.alc_ext_efx` lists its commands, enums, types, and "
        "dependencies; `is_present(device)` checks runtime availability.",
        "",
        "Semantic corrections that cannot be represented by the upstream XML "
        "are reviewed in `tools/semantic_overrides.toml`.",
        "",
        "## Object properties",
        "",
    ]
    for object_name in dict.fromkeys(item.object_name for item in properties):
        lines.extend(
            [
                f"### {object_name.title()}",
                "",
                "| Python property | OpenAL selector | Type | Access | Range | Default | Extension | Generated |",
                "| --- | --- | --- | --- | --- | --- | --- | --- |",
            ]
        )
        for property_ in properties:
            if property_.object_name != object_name:
                continue
            access = "read/write" if property_.writable else "read-only"
            extension = ", ".join(property_.extensions) or "core"
            generated = "yes" if property_.getter is not None else "schema only"
            lines.append(
                f"| `{property_.python_name}` | `{property_.enum_name}` | "
                f"`{' / '.join(property_.value_types) or 'unspecified'}` | "
                f"{access} | {_markdown(property_.range)} | "
                f"{_markdown(property_.default)} | {_markdown(extension)} | "
                f"{generated} |"
            )
        lines.append("")

    lines.extend(["## Python command namespaces", ""])
    for namespace in ("AL", "ALC"):
        lines.extend(
            [
                f"### `library.{namespace.lower()}`",
                "",
                "| Python signature | C command | Availability | Description |",
                "| --- | --- | --- | --- |",
            ]
        )
        for wrapper in wrappers:
            if wrapper.namespace != namespace:
                continue
            parameters = ", ".join(
                _wrapper_method_parameters(wrapper, known_types, group_names, callbacks)
            )
            result = _wrapper_return_annotation(
                wrapper, known_types, group_names, callbacks
            )
            availability = wrapper.extension or "core"
            lines.append(
                f"| `{wrapper.python_name}({parameters}) -> {result}` | "
                f"`{wrapper.name}` | `{availability}` | "
                f"{_markdown(wrapper.comment)} |"
            )
        lines.append("")

    lines.extend(["## Enum groups", ""])
    for namespace, group, python_name, members, bitmask in groups:
        base = "IntFlag" if bitmask else "IntEnum"
        rendered_members = ", ".join(f"`{item}`" for item in members)
        lines.extend(
            [
                f"### `{python_name}`",
                "",
                f"Registry group `{namespace}:{group}` generated as `{base}`.",
                "",
                rendered_members,
                "",
            ]
        )

    lines.extend(
        [
            "## Extensions",
            "",
            "| Extension | APIs | Dependencies | Commands | Enums |",
            "| --- | --- | --- | ---: | ---: |",
        ]
    )
    for api_set in registry.api_sets:
        if api_set.kind != "extension":
            continue
        dependencies = tuple(
            dict.fromkeys(
                requirement.depends
                for requirement in api_set.requirements
                if requirement.depends is not None
            )
        )
        command_count = sum(
            member.kind == "command"
            for requirement in api_set.requirements
            for member in requirement.members
        )
        enum_count = sum(
            member.kind == "enum"
            for requirement in api_set.requirements
            for member in requirement.members
        )
        lines.append(
            f"| `{api_set.name}` | `{', '.join(api_set.apis)}` | "
            f"{_markdown(', '.join(dependencies) or None)} | "
            f"{command_count} | {enum_count} |"
        )
    lines.append("")
    return "\n".join(lines)


def _render_parameter(parameter: ParameterDecl) -> str:
    return (
        "ParameterSpec("
        f"{parameter.name!r}, {parameter.c_type!r}, {parameter.length!r}, "
        f"{parameter.group!r}, {parameter.object_class!r})"
    )


def _render_property(property_: PropertyDecl) -> str:
    return (
        "PropertySpec("
        f"{property_.objects!r}, {property_.value_types!r}, {property_.range!r}, "
        f"{property_.default!r}, {property_.groups!r}, "
        f"{property_.object_class!r}, {property_.kind!r})"
    )


def _render_requirement(requirement: RequirementDecl) -> str:
    members = ", ".join(
        f"ApiMemberSpec({member.kind!r}, {member.name!r})"
        for member in requirement.members
    )
    members = f"({members},)" if members else "()"
    return (
        f"RequirementSpec({requirement.api!r}, {requirement.comment!r}, "
        f"{members}, {requirement.depends!r})"
    )


def render_registry_metadata(
    registry: Registry, source: SourceInfo, digest: str
) -> str:
    """Render language-neutral metadata alongside the ctypes binding layer."""

    enum_values = _resolve_constants(registry.enums)
    define_values = _resolve_defines(registry.defines, enum_values)
    lines = _generated_header(source, digest)
    lines.extend(
        [
            "from pyalsoft._specs import (",
            "    ApiMemberSpec,",
            "    ApiSetSpec,",
            "    CommandSpec,",
            "    DefineSpec,",
            "    EnumSpec,",
            "    ParameterSpec,",
            "    PropertySpec,",
            "    RegistryNoteSpec,",
            "    RequirementSpec,",
            "    TypeSpec,",
            ")",
            "",
            f"UPSTREAM_VERSION = {source.version!r}",
            f"UPSTREAM_COMMIT = {source.commit!r}",
            f"REGISTRY_SHA256 = {digest!r}",
            f"REGISTRY_COMMENTS = {registry.comments!r}",
            "REGISTRY_NOTES: tuple[RegistryNoteSpec, ...] = (",
        ]
    )
    lines.extend(
        f"    RegistryNoteSpec({item.parent!r}, {item.subject!r}, {item.text!r}),"
        for item in registry.notes
    )
    lines.extend(
        [
            ")",
            "",
            "TYPES: tuple[TypeSpec, ...] = (",
        ]
    )
    lines.extend(
        "    TypeSpec("
        f"{item.namespace!r}, {item.name!r}, {item.category!r}, "
        f"{item.declaration!r}, {item.comment!r}),"
        for item in registry.types
    )
    lines.extend([")", "", "DEFINES: tuple[DefineSpec, ...] = ("])
    for define in registry.defines:
        value = define_values[define.name]
        python_value = value.literal if value is not None else "None"
        lines.append(
            "    DefineSpec("
            f"{define.namespace!r}, {define.name!r}, {define.replacement!r}, "
            f"{python_value}),"
        )
    lines.extend([")", "", "ENUMS: tuple[EnumSpec, ...] = ("])
    for enum in registry.enums:
        properties = ", ".join(
            _render_property(property_) for property_ in enum.properties
        )
        properties = f"({properties},)" if properties else "()"
        lines.append(
            "    EnumSpec("
            f"{enum.namespace!r}, {enum.name!r}, {enum.value!r}, "
            f"{enum.groups!r}, {enum.deprecated!r}, {enum.block_group!r}, "
            f"{enum.comment!r}, {enum.comments!r}, {properties}),"
        )
    lines.extend([")", "", "COMMANDS: tuple[CommandSpec, ...] = ("])
    for command in registry.commands:
        parameters = ", ".join(
            _render_parameter(parameter) for parameter in command.parameters
        )
        parameters = f"({parameters},)" if parameters else "()"
        lines.append(
            "    CommandSpec("
            f"{command.namespace!r}, {command.name!r}, {command.return_type!r}, "
            f"{parameters}, {command.export!r}, {command.function_pointer!r}, "
            f"{command.deprecated!r}, {command.return_group!r}, "
            f"{command.comment!r}, {command.comments!r}, "
            f"{command.command_attribute!r}),"
        )
    lines.extend([")", "", "API_SETS: tuple[ApiSetSpec, ...] = ("])
    for api_set in registry.api_sets:
        requirements = ", ".join(
            _render_requirement(requirement) for requirement in api_set.requirements
        )
        requirements = f"({requirements},)" if requirements else "()"
        lines.append(
            "    ApiSetSpec("
            f"{api_set.kind!r}, {api_set.name!r}, {api_set.apis!r}, "
            f"{api_set.number!r}, {api_set.annex!r}, {requirements}, "
            f"{api_set.comments!r}),"
        )
    lines.extend([")", ""])
    return "\n".join(lines)


def render_outputs(
    registry: Registry,
    source: SourceInfo,
    digest: str,
    overrides: SemanticOverrides | None = None,
) -> dict[str, str]:
    """Render every generated file without changing the working tree."""

    if overrides is None:
        overrides = load_semantic_overrides()
    return {
        "constants.py": render_constants(registry, source, digest),
        "commands.py": render_python_commands(registry, source, digest, overrides),
        "enums.py": render_enums(registry, source, digest, overrides),
        "extensions.py": render_extensions(registry, source, digest),
        "types.py": render_ctypes_types(registry, source, digest),
        "functions.py": render_ctypes_functions(registry, source, digest),
        "objects.py": render_objects(registry, source, digest, overrides),
        "registry.py": render_registry_metadata(registry, source, digest),
        "semantics.py": render_semantics(registry, source, digest, overrides),
    }


def render_readme_badge(readme: str, source: SourceInfo) -> str:
    """Update the generated OpenAL Soft version badge in a README."""

    if readme.count(_OPENAL_BADGE_START) != 1 or readme.count(_OPENAL_BADGE_END) != 1:
        raise ValueError("README must contain exactly one OpenAL Soft badge block")

    start = readme.index(_OPENAL_BADGE_START)
    end = readme.index(_OPENAL_BADGE_END, start)
    if end < start:
        raise ValueError("README OpenAL Soft badge block is malformed")

    badge_version = quote(source.version.replace("-", "--"), safe=".")
    release_version = quote(source.version, safe=".")
    block = "\n".join(
        (
            _OPENAL_BADGE_START,
            f"[![OpenAL Soft {source.version}]"
            f"(https://img.shields.io/badge/OpenAL_Soft-{badge_version}-557C94)]"
            f"(https://github.com/kcat/openal-soft/releases/tag/{release_version})",
            _OPENAL_BADGE_END,
        )
    )
    return readme[:start] + block + readme[end + len(_OPENAL_BADGE_END) :]


def _check_outputs(output_dir: Path, outputs: Mapping[str, str]) -> bool:
    stale: list[str] = []
    for name, expected in outputs.items():
        path = output_dir / name
        if not path.is_file() or path.read_text(encoding="utf-8") != expected:
            stale.append(name)
    if stale:
        print("Generated files are missing or stale:")
        for name in stale:
            print(f"  {output_dir / name}")
        return False
    return True


def _write_outputs(output_dir: Path, outputs: Mapping[str, str]) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    for name, content in outputs.items():
        path = output_dir / name
        path.write_text(content, encoding="utf-8", newline="\n")
        print(f"Wrote {path}")


def _check_file(path: Path, expected: str) -> bool:
    if path.is_file() and path.read_text(encoding="utf-8") == expected:
        return True
    print(f"Generated file is missing or stale:\n  {path}")
    return False


def _write_file(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8", newline="\n")
    print(f"Wrote {path}")


def _argument_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--registry", type=Path, default=DEFAULT_REGISTRY)
    parser.add_argument("--source", type=Path, default=DEFAULT_SOURCE)
    parser.add_argument("--semantics", type=Path, default=DEFAULT_SEMANTICS)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--docs-output", type=Path, default=DEFAULT_DOCS_OUTPUT)
    parser.add_argument("--readme-output", type=Path, default=DEFAULT_README_OUTPUT)
    parser.add_argument(
        "--check",
        action="store_true",
        help="fail if committed generated files differ from generator output",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Generate files or verify that the committed output is current."""

    arguments = _argument_parser().parse_args(argv)
    source = load_source_info(arguments.source)
    digest = verify_registry(arguments.registry, source)
    registry = parse_registry(arguments.registry)
    overrides = load_semantic_overrides(arguments.semantics)
    outputs = render_outputs(registry, source, digest, overrides)
    documentation = render_documentation(registry, source, digest, overrides)
    readme = render_readme_badge(
        arguments.readme_output.read_text(encoding="utf-8"), source
    )

    print(
        f"Parsed {len(registry.types)} types, {len(registry.enums)} enums, "
        f"{len(registry.commands)} commands, and "
        f"{len(registry.api_sets)} API sets from OpenAL Soft {source.version}."
    )
    if arguments.check:
        code_is_current = _check_outputs(arguments.output_dir, outputs)
        docs_are_current = _check_file(arguments.docs_output, documentation)
        readme_is_current = _check_file(arguments.readme_output, readme)
        return 0 if code_is_current and docs_are_current and readme_is_current else 1
    _write_outputs(arguments.output_dir, outputs)
    _write_file(arguments.docs_output, documentation)
    _write_file(arguments.readme_output, readme)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
