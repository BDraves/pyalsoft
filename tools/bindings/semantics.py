"""Infer safe, Python-friendly semantics from registry declarations."""

from __future__ import annotations

import keyword
import re
from collections.abc import Mapping, Sequence

from .c_types import _basetype_kind, _ctypes_base_expressions, _split_c_type
from .models import (
    CommandDecl,
    CommandWrapper,
    EffectiveProperty,
    ParameterDecl,
    Registry,
    RegistryError,
    SemanticOverrides,
    WrapperParameter,
)
from .registry import _ensure_unique

_ARRAY_PROPERTY_TYPE = re.compile(
    r"(?P<base>[A-Za-z_][A-Za-z0-9_]*)(?:\[(?P<arity>[1-9][0-9]*)\])?\Z"
)


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
