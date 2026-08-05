"""Typed low-level bindings and runtime loading for OpenAL Soft."""

from pyalsoft.bindings import registry
from pyalsoft.bindings._extensions import Extension
from pyalsoft.bindings._generated import constants, enums, types
from pyalsoft.bindings._generated.constants import *  # noqa: F403
from pyalsoft.bindings._generated.enums import *  # noqa: F403
from pyalsoft.bindings._generated.extensions import ExtensionCapabilities
from pyalsoft.bindings._generated.objects import (
    AuxiliaryEffectSlot,
    Buffer,
    Effect,
    Filter,
    Listener,
    Source,
)
from pyalsoft.bindings._generated.types import *  # noqa: F403
from pyalsoft.bindings._library import (
    ContextRequiredError,
    ExtensionUnavailableError,
    ForeignFunction,
    FunctionUnavailableError,
    LibraryNotFoundError,
    LibraryPath,
    OpenALError,
    OpenALLibrary,
    load,
)

__all__ = [
    *constants.__all__,
    *enums.__all__,
    *types.__all__,
    "constants",
    "enums",
    "registry",
    "types",
    "AuxiliaryEffectSlot",
    "Buffer",
    "ContextRequiredError",
    "Effect",
    "Extension",
    "ExtensionCapabilities",
    "ExtensionUnavailableError",
    "ForeignFunction",
    "Filter",
    "FunctionUnavailableError",
    "LibraryNotFoundError",
    "LibraryPath",
    "Listener",
    "OpenALError",
    "OpenALLibrary",
    "Source",
    "load",
]
