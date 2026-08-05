"""Parse and validate the vendored OpenAL XML registry."""

from __future__ import annotations

import hashlib
import re
import xml.etree.ElementTree as ET
from collections.abc import Mapping, Sequence
from pathlib import Path

from .models import (
    ApiMemberDecl,
    ApiSetDecl,
    CommandDecl,
    DefineDecl,
    EnumDecl,
    ParameterDecl,
    PropertyDecl,
    Registry,
    RegistryError,
    RegistryNoteDecl,
    RequirementDecl,
    SourceInfo,
    TypeDecl,
)
from .paths import DEFAULT_REGISTRY

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
