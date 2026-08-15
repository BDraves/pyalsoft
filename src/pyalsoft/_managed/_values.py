"""Shared validation helpers for immutable managed API values."""

from __future__ import annotations

import math
from typing import cast

type Vector3 = tuple[float, float, float]
"""A Cartesian ``(x, y, z)`` vector used for spatial coordinates."""


def _finite_float(name: str, value: float) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TypeError(f"{name} must be a number")
    converted = float(value)
    if not math.isfinite(converted):
        raise ValueError(f"{name} must be finite")
    return converted


def _bounded_float(name: str, value: float, minimum: float, maximum: float) -> float:
    converted = _finite_float(name, value)
    if not minimum <= converted <= maximum:
        raise ValueError(f"{name} must be between {minimum:g} and {maximum:g}")
    return converted


def _vector3(name: str, value: Vector3) -> Vector3:
    if not isinstance(value, (tuple, list)) or len(value) != 3:
        raise TypeError(f"{name} must be a three-item tuple or list")
    return cast(
        Vector3,
        tuple(
            _finite_float(f"{name}[{index}]", item) for index, item in enumerate(value)
        ),
    )
