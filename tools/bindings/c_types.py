"""Resolve C declarations into Python literals and ctypes expressions."""

from __future__ import annotations

import ast
import re
from collections.abc import Mapping, Sequence

from .models import (
    ConstantValue,
    DefineDecl,
    EnumDecl,
    FunctionPointerDecl,
    Registry,
    RegistryError,
    TypeDecl,
)

_INTEGER_LITERAL = re.compile(r"-?(?:0[xX][0-9A-Fa-f]+|[0-9]+)\Z")
_FLOAT_LITERAL = re.compile(
    r"-?(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)(?:[eE][+-]?[0-9]+)?f\Z"
)
_IDENTIFIER = re.compile(r"[A-Za-z_][A-Za-z0-9_]*\Z")

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
