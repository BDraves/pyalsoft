"""Render low-level constants, ctypes types, and function declarations."""

from __future__ import annotations

from .c_types import (
    _basetype_kind,
    _ctypes_base_expressions,
    _ctypes_expression,
    _function_pointer_declarations,
    _resolve_constants,
    _resolve_defines,
)
from .common import _generated_header
from .models import Registry, SourceInfo
from .semantics import _command_extensions


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
