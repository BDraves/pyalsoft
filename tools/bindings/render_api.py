"""Render the typed, Python-facing API modules."""

from __future__ import annotations

from collections.abc import Mapping

from .c_types import _ctypes_base_expressions, _split_c_type
from .common import _generated_header
from .models import (
    CommandWrapper,
    EffectiveProperty,
    Registry,
    RegistryError,
    SemanticOverrides,
    SourceInfo,
    WrapperParameter,
)
from .registry import _ensure_unique
from .semantics import (
    _property_type,
    _snake_case,
    build_command_wrappers,
    build_effective_properties,
    build_enum_groups,
)


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
            "from pyalsoft.bindings._generated import constants as _constants",
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
        f"{parameter.object_class!r}, {parameter.retained!r}, "
        f"{parameter.visible!r})"
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
            "from pyalsoft.bindings._specs import (",
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
    if (
        parameter.direction == "inout" or parameter.retained
    ) and (base in {"void", "ALvoid", "ALCvoid"} or base.startswith("struct ")):
        return "_api.WritableBuffer | object"
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
    lines = [
        f"    def {wrapper.python_name}({signature}) -> {return_annotation}:",
        f'        """{documentation}"""',
        "",
        f"        return cast({return_annotation}, self._invoke({args}))",
        "",
    ]
    if wrapper.string_list_name is not None:
        string_list_documentation = (
            f"Return a NUL-separated string list from ``{wrapper.name}``."
        )
        if wrapper.name == "alcGetString":
            string_list_documentation += (
                " Requires a null device and a device-list selector."
            )
        lines.extend(
            [
                f"    def {wrapper.string_list_name}({signature}) -> tuple[str, ...]:",
                f'        """{string_list_documentation}"""',
                "",
                "        return cast(",
                "            tuple[str, ...],",
                f"            self._invoke_string_list({args}),",
                "        )",
                "",
            ]
        )
    return lines


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
            "from pyalsoft.bindings import _api",
            "from pyalsoft.bindings._generated import enums as _enums",
            "from pyalsoft.bindings._generated import types as _types",
            "",
            "if TYPE_CHECKING:",
            "    from pyalsoft.bindings._generated.objects import (",
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
            "        from pyalsoft.bindings._generated.objects import Source",
            "",
            "        return Source(self.library, identifier)",
            "",
            "    def buffer(self, identifier: int) -> Buffer:",
            '        """Wrap an existing buffer identifier."""',
            "",
            "        from pyalsoft.bindings._generated.objects import Buffer",
            "",
            "        return Buffer(self.library, identifier)",
            "",
            "    def effect(self, identifier: int) -> Effect:",
            '        """Wrap an existing effect identifier."""',
            "",
            "        from pyalsoft.bindings._generated.objects import Effect",
            "",
            "        return Effect(self.library, identifier)",
            "",
            "    def filter(self, identifier: int) -> Filter:",
            '        """Wrap an existing filter identifier."""',
            "",
            "        from pyalsoft.bindings._generated.objects import Filter",
            "",
            "        return Filter(self.library, identifier)",
            "",
            "    def auxiliary_effect_slot(self, identifier: int) -> AuxiliaryEffectSlot:",
            '        """Wrap an existing auxiliary effect slot identifier."""',
            "",
            "        from pyalsoft.bindings._generated.objects import AuxiliaryEffectSlot",
            "",
            "        return AuxiliaryEffectSlot(self.library, identifier)",
            "",
            "    @property",
            "    def listener(self) -> Listener:",
            '        """Return the current context\'s singleton listener."""',
            "",
            "        from pyalsoft.bindings._generated.objects import Listener",
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
            "from pyalsoft.bindings import _objects",
            "from pyalsoft.bindings._generated import enums as _enums",
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
            "from pyalsoft.bindings import _extensions",
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
