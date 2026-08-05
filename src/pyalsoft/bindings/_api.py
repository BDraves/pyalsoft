"""Python-value command marshalling over the generated ctypes layer."""

from __future__ import annotations

import ctypes
from collections.abc import Mapping, Sequence
from typing import TYPE_CHECKING, Any, cast

from pyalsoft.bindings._generated import enums as _enums
from pyalsoft.bindings._generated import types as _types
from pyalsoft.bindings._generated.semantics import (
    COMMAND_WRAPPERS_BY_NAME,
    ENUM_GROUPS_BY_NAME,
)
from pyalsoft.bindings._specs import CommandWrapperSpec, WrapperParameterSpec

if TYPE_CHECKING:
    from pyalsoft.bindings._library import OpenALLibrary

type ReadableBuffer = bytes | bytearray | memoryview


def _c_type_parts(c_type: str) -> tuple[str, int, bool]:
    tokens = c_type.replace("*", " * ").split()
    is_const = "const" in tokens
    words = [token for token in tokens if token not in {"const", "*"}]
    return " ".join(words), tokens.count("*"), is_const


def _is_character(base: str) -> bool:
    return base in {"char", "ALchar", "ALCchar"}


def _is_void(base: str) -> bool:
    return base in {"void", "ALvoid", "ALCvoid"}


def _ctype_for(base: str) -> Any:
    if _is_character(base):
        return ctypes.c_char
    if _is_void(base):
        return ctypes.c_void_p
    name = base.removeprefix("struct ")
    try:
        return getattr(_types, name)
    except AttributeError as error:
        raise TypeError(f"no generated ctypes type for {base!r}") from error


def _object_identifier(value: object) -> object:
    identifier = getattr(value, "identifier", None)
    return identifier if isinstance(identifier, int) else value


def _encode_string(value: object, name: str) -> bytes | None:
    if value is None:
        return None
    if isinstance(value, bytes):
        return value
    if isinstance(value, str):
        return value.encode("utf-8")
    raise TypeError(f"{name} must be str, bytes, or None")


def _buffer_input(value: object, *, writable: bool) -> tuple[object, int | None]:
    if isinstance(value, bytes):
        data = (ctypes.c_ubyte * len(value)).from_buffer_copy(value)
        return data, len(value)
    if isinstance(value, bytearray):
        array_type = ctypes.c_ubyte * len(value)
        data = (
            array_type.from_buffer(value)
            if writable
            else array_type.from_buffer_copy(value)
        )
        return data, len(value)
    if isinstance(value, memoryview):
        contiguous = value.cast("B")
        array_type = ctypes.c_ubyte * contiguous.nbytes
        if writable and not contiguous.readonly:
            data = array_type.from_buffer(contiguous)
        else:
            data = array_type.from_buffer_copy(contiguous)
        return data, contiguous.nbytes
    return value, None


def _prepare_input(
    parameter: WrapperParameterSpec, value: object
) -> tuple[object, int | None]:
    base, pointer_depth, _is_const = _c_type_parts(parameter.c_type)
    value = _object_identifier(value)
    if pointer_depth == 0:
        if base in {"ALboolean", "ALCboolean"}:
            return (b"\x01" if bool(value) else b"\x00"), None
        return value, None

    if base in {"ALCdevice", "ALCcontext"} and pointer_depth == 1:
        return value, None
    if _is_character(base):
        encoded = _encode_string(value, parameter.python_name)
        return encoded, len(encoded) if encoded is not None else 0
    if _is_void(base) or base.startswith("struct "):
        return _buffer_input(value, writable=parameter.direction == "inout")
    if value is None:
        return None, 0
    if isinstance(value, (ctypes.Array, ctypes._Pointer)):
        return value, None
    if not isinstance(value, Sequence):
        raise TypeError(f"{parameter.python_name} must be a sequence")
    values = list(value)
    if parameter.name in {"attrlist", "attribs"} and (
        not values or int(values[-1]) != 0
    ):
        values.append(0)
    ctype = _ctype_for(base)
    array = (ctype * len(values))(*values)
    return array, len(values)


def _output_length(
    parameter: WrapperParameterSpec,
    values: Mapping[str, object],
    derived_lengths: Mapping[str, int],
    result_size: int,
) -> int:
    if parameter.length is None:
        return result_size
    if parameter.length.isdigit():
        return int(parameter.length)
    raw = values.get(parameter.length, derived_lengths.get(parameter.length))
    if raw is None:
        raise TypeError(
            f"cannot determine output length {parameter.length!r} for "
            f"{parameter.name!r}"
        )
    return int(cast(Any, raw))


def _allocate_output(
    parameter: WrapperParameterSpec,
    values: Mapping[str, object],
    derived_lengths: Mapping[str, int],
    result_size: int,
) -> ctypes.Array[Any]:
    base, _pointer_depth, _is_const = _c_type_parts(parameter.c_type)
    length = _output_length(parameter, values, derived_lengths, result_size)
    if length < 0:
        raise ValueError(
            f"output length for {parameter.python_name} cannot be negative"
        )
    return cast(ctypes.Array[Any], (_ctype_for(base) * length)())


def _enum_result(namespace: str, group: str | None, value: int) -> object:
    if group is None:
        return value
    spec = ENUM_GROUPS_BY_NAME.get((namespace, group))
    if spec is None:
        return value
    enum_type = cast(type[Any], getattr(_enums, spec.python_name))
    try:
        return enum_type(value)
    except ValueError:
        return value


def _convert_scalar(base: str, value: object) -> object:
    raw = getattr(value, "value", value)
    if base in {"ALboolean", "ALCboolean"}:
        if isinstance(raw, bytes):
            return raw != b"\x00"
        return bool(raw)
    if _is_character(base):
        if raw is None:
            return None
        if isinstance(raw, bytes):
            return raw.decode("utf-8")
    return raw


def _convert_output(
    parameter: WrapperParameterSpec,
    wrapper: CommandWrapperSpec,
    allocation: ctypes.Array[Any],
) -> object:
    base, _pointer_depth, _is_const = _c_type_parts(parameter.c_type)
    if _is_character(base):
        return bytes(allocation).split(b"\x00", 1)[0].decode("utf-8")
    converted = [
        _enum_result(
            wrapper.namespace,
            parameter.group,
            cast(int, _convert_scalar(base, item)),
        )
        if parameter.group is not None
        else _convert_scalar(base, item)
        for item in allocation
    ]
    is_vector = (parameter.length is not None and parameter.length != "1") or (
        parameter.length is None and wrapper.result_size
    )
    return tuple(converted) if is_vector else converted[0]


def _convert_return(wrapper: CommandWrapperSpec, value: object) -> object:
    base, pointer_depth, _is_const = _c_type_parts(wrapper.return_type)
    if base == "void" and pointer_depth == 0:
        return None
    if pointer_depth and _is_character(base):
        if value is None:
            return None
        return cast(bytes, value).decode("utf-8")
    converted = _convert_scalar(base, value)
    if wrapper.return_group is not None:
        return _enum_result(
            wrapper.namespace, wrapper.return_group, cast(int, converted)
        )
    return converted


class CommandNamespace:
    """Base class used by generated AL and ALC Python command namespaces."""

    def __init__(self, library: OpenALLibrary) -> None:
        self._library = library

    @property
    def library(self) -> OpenALLibrary:
        """The raw library underlying this namespace."""

        return self._library

    def _invoke(
        self,
        name: str,
        values: Mapping[str, object],
        *,
        result_size: int = 1,
        resolution_device: object | None = None,
    ) -> object:
        if result_size < 1:
            raise ValueError("result_size must be at least one")
        wrapper = COMMAND_WRAPPERS_BY_NAME[name]
        prepared: dict[str, object] = {}
        derived_lengths: dict[str, int] = {}
        for parameter in wrapper.parameters:
            if parameter.direction == "out" or not parameter.visible:
                continue
            argument, length = _prepare_input(parameter, values[parameter.name])
            prepared[parameter.name] = argument
            if parameter.length is not None and not parameter.length.isdigit():
                if length is None:
                    continue
                previous = derived_lengths.get(parameter.length)
                if previous is not None and previous != length:
                    raise ValueError(
                        f"inputs controlled by {parameter.length!r} have "
                        "different lengths"
                    )
                derived_lengths[parameter.length] = length

        arguments: list[object] = []
        outputs: list[tuple[WrapperParameterSpec, ctypes.Array[Any]]] = []
        for parameter in wrapper.parameters:
            if parameter.direction == "out":
                allocation = _allocate_output(
                    parameter, values, derived_lengths, result_size
                )
                outputs.append((parameter, allocation))
                arguments.append(allocation)
            elif parameter.visible:
                arguments.append(prepared[parameter.name])
            else:
                try:
                    arguments.append(derived_lengths[parameter.name])
                except KeyError as error:
                    raise TypeError(
                        f"cannot infer hidden parameter {parameter.name!r}"
                    ) from error

        device = (
            resolution_device if resolution_device is not None else values.get("device")
        )
        function = self._library.get_function(name, device=device)
        native_result = function(*arguments)
        results: list[object] = []
        base, pointer_depth, _is_const = _c_type_parts(wrapper.return_type)
        if not (base == "void" and pointer_depth == 0):
            results.append(_convert_return(wrapper, native_result))
        results.extend(
            _convert_output(parameter, wrapper, allocation)
            for parameter, allocation in outputs
        )
        if not results:
            return None
        if len(results) == 1:
            return results[0]
        return tuple(results)


__all__ = ["CommandNamespace", "ReadableBuffer"]
