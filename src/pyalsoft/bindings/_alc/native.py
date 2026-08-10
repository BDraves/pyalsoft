"""Shared native-value validation for owned ALC handles."""

from __future__ import annotations

from collections.abc import Callable

from pyalsoft.bindings._alc.errors import NativeCallError
from pyalsoft.bindings._generated import constants as _constants
from pyalsoft.bindings._library import OpenALLibrary, _pointer_address


def _same_pointer(left: object | None, right: object | None) -> bool:
    """Compare native handles while remaining friendly to test doubles."""

    if left is right:
        return True
    try:
        return _pointer_address(left) == _pointer_address(right)
    except TypeError:
        return False


def _enum_or_int[T](enum_type: Callable[[int], T], value: int) -> T | int:
    try:
        return enum_type(value)
    except ValueError:
        return value


def _integer_value(value: object, *, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError(f"{label} must be an integer")
    return int(value)


def _positive_integer(value: object, *, label: str) -> int:
    value = _integer_value(value, label=label)
    if value <= 0:
        raise ValueError(f"{label} must be positive")
    return value


def _require_no_al_error(
    library: OpenALLibrary,
    operation: str,
    *,
    preexisting: bool = False,
) -> None:
    error = library.al.get_error()
    error_value = int(error)
    if error_value == _constants.AL_NO_ERROR:
        return
    error_name = getattr(error, "name", f"0x{error_value:04x}")
    if preexisting:
        raise NativeCallError(
            f"{operation} cannot begin with pre-existing OpenAL error {error_name}"
        )
    raise NativeCallError(f"{operation} failed with OpenAL error {error_name}")
