"""Runtime support for generated OpenAL object property descriptors."""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Any, overload

from pyalsoft._generated import constants as _constants
from pyalsoft._generated import enums as _enums
from pyalsoft._generated import types as _types
from pyalsoft._generated.semantics import OBJECT_PROPERTIES_BY_KEY
from pyalsoft._specs import ObjectPropertySpec

if TYPE_CHECKING:
    from pyalsoft._library import OpenALLibrary

_RANGE = re.compile(
    r"(?P<minimum>-?(?:\d+(?:\.\d*)?|\.\d+))?\.\.(?P<inclusive>=)?"
    r"(?P<maximum>-?(?:\d+(?:\.\d*)?|\.\d+))?\Z"
)


def _preferred_type(spec: ObjectPropertySpec) -> tuple[str, int | None]:
    choices = sorted(
        spec.value_types,
        key=lambda item: (
            not item.startswith("ALfloat"),
            not item.startswith("ALdouble"),
            item,
        ),
    )
    if not choices:
        raise TypeError(f"property {spec.enum_name} has no value type")
    match = re.fullmatch(
        r"(?P<base>[A-Za-z_][A-Za-z0-9_]*)(?:\[(?P<arity>\d+)\])?",
        choices[0],
    )
    if match is None:
        raise TypeError(f"unsupported property type {choices[0]!r}")
    arity = match.group("arity")
    return match.group("base"), int(arity) if arity is not None else None


def _property_ctype(base: str) -> Any:
    if base == "ALuint64SOFT":
        return _types.ALint64SOFT
    if base in {"ALboolean", "ALenum", "ALint", "ALuint"}:
        return _types.ALint
    return getattr(_types, base)


def _unwrap(value: object) -> object:
    identifier = getattr(value, "identifier", None)
    return identifier if isinstance(identifier, int) else value


def _convert_boolean(value: object) -> bytes:
    return b"\x01" if bool(value) else b"\x00"


def _convert_value(base: str, value: object) -> object:
    value = _unwrap(value)
    if base == "ALboolean":
        return int(bool(value))
    return value


def _enum_value(spec: ObjectPropertySpec, value: int) -> object:
    if spec.enum_type is None:
        return value
    enum_type = getattr(_enums, spec.enum_type)
    try:
        return enum_type(value)
    except ValueError:
        return value


def _wrap_object(library: OpenALLibrary, object_name: str, value: int) -> object:
    if value == 0:
        return None
    from pyalsoft._generated import objects

    classes = {
        "source": objects.Source,
        "buffer": objects.Buffer,
        "effect": objects.Effect,
        "filter": objects.Filter,
        "auxiliary effect slot": objects.AuxiliaryEffectSlot,
    }
    return classes[object_name](library, value)


def _convert_result(
    library: OpenALLibrary,
    spec: ObjectPropertySpec,
    base: str,
    value: object,
) -> object:
    raw = getattr(value, "value", value)
    if base == "ALboolean":
        return raw != b"\x00" if isinstance(raw, bytes) else bool(raw)
    integer = int(raw) if isinstance(raw, (int, bool)) else raw
    if spec.object_class is not None:
        if not isinstance(integer, int):
            raise TypeError(f"{spec.enum_name} did not return an integer object ID")
        return _wrap_object(library, spec.object_class, integer)
    if spec.enum_type is not None:
        if not isinstance(integer, int):
            raise TypeError(f"{spec.enum_name} did not return an enum integer")
        return _enum_value(spec, integer)
    return integer


def _validate_range(spec: ObjectPropertySpec, value: object) -> None:
    if spec.range is None:
        return
    match = _RANGE.fullmatch(spec.range)
    if match is None:
        return
    values = value if isinstance(value, tuple) else (value,)
    minimum = match.group("minimum")
    maximum = match.group("maximum")
    for item in values:
        if not isinstance(item, (int, float)):
            continue
        if minimum is not None and item < float(minimum):
            raise ValueError(f"{spec.python_name} must be at least {minimum}")
        if maximum is not None:
            limit = float(maximum)
            invalid = item > limit if match.group("inclusive") else item >= limit
            if invalid:
                qualifier = "at most" if match.group("inclusive") else "less than"
                raise ValueError(f"{spec.python_name} must be {qualifier} {maximum}")


class ALObject:
    """Base for a generated integer OpenAL object handle."""

    object_name: str

    def __init__(self, library: OpenALLibrary, identifier: int) -> None:
        if identifier < 0:
            raise ValueError("OpenAL object identifiers cannot be negative")
        self.library = library
        self.identifier = identifier

    def __int__(self) -> int:
        return self.identifier

    def __repr__(self) -> str:
        return (
            f"{type(self).__name__}(library={self.library!r}, "
            f"identifier={self.identifier})"
        )


class ALSingletonObject:
    """Base for generated context-scoped singleton objects such as Listener."""

    object_name: str

    def __init__(self, library: OpenALLibrary) -> None:
        self.library = library


class ALProperty[T]:
    """Descriptor that dispatches a normalized property to generated raw calls."""

    def __init__(self, object_name: str, enum_name: str) -> None:
        self.spec = OBJECT_PROPERTIES_BY_KEY[(object_name, enum_name)]

    @overload
    def __get__(self, instance: None, owner: type[object]) -> ALProperty[T]: ...

    @overload
    def __get__(
        self, instance: ALObject | ALSingletonObject, owner: type[object]
    ) -> T: ...

    def __get__(
        self,
        instance: ALObject | ALSingletonObject | None,
        owner: type[object],
    ) -> ALProperty[T] | T:
        if instance is None:
            return self
        if self.spec.getter is None:
            raise AttributeError(f"{self.spec.python_name} is not readable")
        base, arity = _preferred_type(self.spec)
        ctype = _property_ctype(base)
        count = arity or 1
        output = (ctype * count)()
        arguments: list[object] = []
        if isinstance(instance, ALObject):
            arguments.append(instance.identifier)
        arguments.extend((getattr(_constants, self.spec.enum_name), output))
        instance.library.get_function(self.spec.getter)(*arguments)
        converted = tuple(
            _convert_result(instance.library, self.spec, base, item) for item in output
        )
        return converted if arity is not None else converted[0]  # type: ignore[return-value]

    def __set__(self, instance: ALObject | ALSingletonObject, value: T) -> None:
        if self.spec.setter is None:
            raise AttributeError(f"{self.spec.python_name} is read-only")
        _validate_range(self.spec, value)
        base, arity = _preferred_type(self.spec)
        arguments: list[object] = []
        if isinstance(instance, ALObject):
            arguments.append(instance.identifier)
        arguments.append(getattr(_constants, self.spec.enum_name))
        if arity is None:
            arguments.append(_convert_value(base, value))
        else:
            if not isinstance(value, tuple) or len(value) != arity:
                raise TypeError(
                    f"{self.spec.python_name} requires a {arity}-item tuple"
                )
            ctype = _property_ctype(base)
            arguments.append(
                (ctype * arity)(*(_convert_value(base, item) for item in value))
            )
        instance.library.get_function(self.spec.setter)(*arguments)


__all__ = ["ALObject", "ALProperty", "ALSingletonObject"]
