"""Runtime loading and function resolution for OpenAL."""

from __future__ import annotations

import ctypes
import ctypes.util
import os
import sys
from collections.abc import Callable
from importlib.metadata import PackageNotFoundError, distribution
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

from pyalsoft.bindings._generated.functions import (
    COMMAND_EXPORTS,
    COMMAND_EXTENSIONS,
    COMMAND_NAMESPACES,
    EXTENSION_APIS,
    PROTOTYPES,
)

if TYPE_CHECKING:
    from pyalsoft.bindings._generated.commands import ALCCommands, ALCommands
    from pyalsoft.bindings._generated.extensions import ExtensionCapabilities

type ForeignFunction = Callable[..., Any]
type LibraryPath = str | os.PathLike[str]


class OpenALError(Exception):
    """Base exception for loading or resolving OpenAL functions."""


class LibraryNotFoundError(OpenALError):
    """Raised when no usable OpenAL shared library can be loaded."""


class FunctionUnavailableError(OpenALError):
    """Raised when an OpenAL function has no usable entry point."""


class ExtensionUnavailableError(FunctionUnavailableError):
    """Raised when the current implementation does not expose an extension."""


class ContextRequiredError(FunctionUnavailableError):
    """Raised when resolving a function requires a current AL context."""


def _bundled_library_path() -> str | None:
    if sys.platform == "win32":
        library_name = "soft_oal.dll"
    elif sys.platform == "darwin":
        library_name = "libopenal.1.dylib"
    elif sys.platform.startswith("linux"):
        library_name = "libopenal.so.1"
    else:
        return None
    package_path = Path(__file__).parent.parent / "_native" / library_name
    if package_path.is_file():
        return os.fspath(package_path.resolve())

    # Editable builds can put forced-included files in site-packages while the
    # importable Python package remains in the source tree. Resolve the native
    # payload through the distribution metadata so both locations are covered.
    try:
        installed_path = Path(
            str(
                distribution("pyalsoft").locate_file(
                    Path("pyalsoft") / "_native" / library_name
                )
            )
        )
    except PackageNotFoundError:
        return None
    if not installed_path.is_file():
        return None
    return os.fspath(installed_path.resolve())


def _library_candidates() -> tuple[str, ...]:
    discovered: list[str | None]
    fallbacks: tuple[str, ...]
    if sys.platform == "win32":
        discovered = [
            _bundled_library_path(),
            ctypes.util.find_library("OpenAL32"),
        ]
        fallbacks = ("OpenAL32.dll",)
    elif sys.platform == "darwin":
        discovered = [
            _bundled_library_path(),
            ctypes.util.find_library("openal"),
            ctypes.util.find_library("OpenAL"),
        ]
        fallbacks = ("libopenal.1.dylib", "libopenal.dylib")
    else:
        discovered = [_bundled_library_path(), ctypes.util.find_library("openal")]
        fallbacks = ("libopenal.so.1", "libopenal.so")

    candidates: list[str] = []
    for candidate in (*discovered, *fallbacks):
        if candidate and candidate not in candidates:
            candidates.append(candidate)
    return tuple(candidates)


def _load_shared_library(path: LibraryPath | None) -> tuple[Any, str]:
    if path is not None:
        candidate = os.fspath(path)
        try:
            return ctypes.CDLL(candidate), candidate
        except OSError as error:
            raise LibraryNotFoundError(
                f"could not load OpenAL library {candidate!r}: {error}"
            ) from error

    errors: list[str] = []
    for candidate in _library_candidates():
        try:
            return ctypes.CDLL(candidate), candidate
        except OSError as error:
            errors.append(f"{candidate}: {error}")

    detail = "; ".join(errors) if errors else "no candidate library names"
    raise LibraryNotFoundError(f"could not locate an OpenAL library ({detail})")


def _pointer_address(value: object | None) -> int | None:
    if value is None:
        return None
    if isinstance(value, int):
        return value or None
    if isinstance(value, ctypes.c_void_p):
        return value.value
    try:
        return ctypes.cast(cast(Any, value), ctypes.c_void_p).value
    except (ctypes.ArgumentError, TypeError) as error:
        raise TypeError(f"expected a C pointer, got {value!r}") from error


def _c_boolean(value: object) -> bool:
    raw_value = getattr(value, "value", value)
    if isinstance(raw_value, bytes):
        return raw_value != b"\0"
    return bool(raw_value)


def _encoded_name(value: str, *, label: str) -> bytes:
    try:
        return value.encode("ascii")
    except UnicodeEncodeError as error:
        raise ValueError(f"{label} must contain only ASCII characters") from error


class OpenALLibrary:
    """A loaded OpenAL library with typed core and extension functions.

    Core functions and their generated signatures are bound lazily. AL extension
    functions are resolved for the current context. ALC extension functions are
    resolved for the device supplied to :meth:`get_function` or
    :meth:`get_alc_extension`.
    """

    def __init__(self, path: LibraryPath | None = None) -> None:
        self._handle, self.library_name = _load_shared_library(path)
        self._exports: dict[str, ForeignFunction] = {}
        self._al_commands: ALCommands | None = None
        self._alc_commands: ALCCommands | None = None
        self._extension_capabilities: ExtensionCapabilities | None = None

    @property
    def native_library(self) -> Any:
        """The underlying :class:`ctypes.CDLL` instance."""

        return self._handle

    @property
    def al(self) -> ALCommands:
        """Python-value wrappers for AL commands and typed AL objects."""

        if self._al_commands is None:
            from pyalsoft.bindings._generated.commands import ALCommands

            self._al_commands = ALCommands(self)
        return self._al_commands

    @property
    def alc(self) -> ALCCommands:
        """Python-value wrappers for ALC device and context commands."""

        if self._alc_commands is None:
            from pyalsoft.bindings._generated.commands import ALCCommands

            self._alc_commands = ALCCommands(self)
        return self._alc_commands

    @property
    def extensions(self) -> ExtensionCapabilities:
        """Generated capability objects for all registry extensions."""

        if self._extension_capabilities is None:
            from pyalsoft.bindings._generated.extensions import ExtensionCapabilities

            self._extension_capabilities = ExtensionCapabilities(self)
        return self._extension_capabilities

    def get_export(self, name: str) -> ForeignFunction:
        """Return a directly exported command without checking extensions."""

        if name not in PROTOTYPES:
            raise FunctionUnavailableError(f"unknown OpenAL command {name!r}")
        if COMMAND_EXPORTS[name] is None:
            raise FunctionUnavailableError(
                f"OpenAL command {name!r} is not a direct library export"
            )

        cached = self._exports.get(name)
        if cached is not None:
            return cached

        prototype = PROTOTYPES[name]
        try:
            function = cast(ForeignFunction, prototype((name, self._handle)))
        except (AttributeError, OSError, TypeError, ValueError) as error:
            raise FunctionUnavailableError(
                f"OpenAL library {self.library_name!r} does not export {name!r}"
            ) from error
        self._exports[name] = function
        return function

    def _current_context(self) -> object | None:
        function = self.get_export("alcGetCurrentContext")
        return cast(object | None, function())

    def _require_current_context(self) -> object:
        context = self._current_context()
        if _pointer_address(context) is None:
            raise ContextRequiredError(
                "a current OpenAL context is required for this operation"
            )
        return context

    def _device_for_context(self, context: object) -> object:
        function = self.get_export("alcGetContextsDevice")
        device = cast(object | None, function(context))
        if _pointer_address(device) is None:
            raise ContextRequiredError(
                "the current OpenAL context is not associated with a device"
            )
        return device

    def is_al_extension_present(self, extension: str) -> bool:
        """Check an AL extension against the current context."""

        self._require_current_context()
        function = self.get_export("alIsExtensionPresent")
        result = cast(object, function(_encoded_name(extension, label="extension")))
        return _c_boolean(result)

    def is_alc_extension_present(
        self, extension: str, device: object | None = None
    ) -> bool:
        """Check an ALC extension for *device* (or the null device)."""

        function = self.get_export("alcIsExtensionPresent")
        result = cast(
            object,
            function(device, _encoded_name(extension, label="extension")),
        )
        return _c_boolean(result)

    def _extension_for(self, name: str, namespace: str) -> str:
        if name not in PROTOTYPES:
            raise FunctionUnavailableError(f"unknown OpenAL command {name!r}")
        actual_namespace = COMMAND_NAMESPACES[name]
        if actual_namespace != namespace:
            raise FunctionUnavailableError(
                f"command {name!r} belongs to {actual_namespace}, not {namespace}"
            )
        extension = COMMAND_EXTENSIONS.get(name)
        if extension is None:
            raise FunctionUnavailableError(
                f"OpenAL command {name!r} is a core command, not an extension"
            )
        return extension

    def _function_from_address(
        self, name: str, address_value: object
    ) -> ForeignFunction:
        address = _pointer_address(address_value)
        if address is None:
            raise FunctionUnavailableError(
                f"OpenAL returned a null entry point for {name!r}"
            )
        prototype = PROTOTYPES[name]
        try:
            return cast(ForeignFunction, prototype(address))
        except (TypeError, ValueError) as error:
            raise FunctionUnavailableError(
                f"could not construct the generated prototype for {name!r}"
            ) from error

    def get_al_extension(
        self,
        name: str,
        *,
        device: object | None = None,
        check_extension: bool = True,
    ) -> ForeignFunction:
        """Resolve an AL extension command for the current context.

        Extensions that also support ALC can be resolved through *device* when
        there is no current context. This is needed by direct-context APIs.
        """

        extension = self._extension_for(name, "AL")
        extension_apis = EXTENSION_APIS[extension]
        context = self._current_context()
        has_context = _pointer_address(context) is not None
        use_alc = not has_context and "alc" in extension_apis and device is not None

        if not has_context and not use_alc and check_extension:
            raise ContextRequiredError(
                f"a current OpenAL context is required to resolve {name!r}"
            )

        if check_extension:
            if extension.startswith("ALC_") and "alc" in extension_apis:
                if device is None:
                    if not has_context:
                        raise ContextRequiredError(
                            f"a device is required to check {extension!r}"
                        )
                    device = self._device_for_context(context)
                present = self.is_alc_extension_present(extension, device)
            elif has_context and "al" in extension_apis:
                present = self.is_al_extension_present(extension)
            elif "alc" in extension_apis and device is not None:
                present = self.is_alc_extension_present(extension, device)
            else:
                raise ContextRequiredError(
                    f"no usable context or device can check {extension!r}"
                )
            if not present:
                raise ExtensionUnavailableError(
                    f"OpenAL extension {extension!r} is not available"
                )

        encoded_name = _encoded_name(name, label="command name")
        if use_alc:
            get_proc_address = self.get_export("alcGetProcAddress")
            address_value = cast(object, get_proc_address(device, encoded_name))
        else:
            get_proc_address = self.get_export("alGetProcAddress")
            address_value = cast(object, get_proc_address(encoded_name))
        return self._function_from_address(name, address_value)

    def get_alc_extension(
        self,
        name: str,
        device: object | None = None,
        *,
        check_extension: bool = True,
    ) -> ForeignFunction:
        """Resolve an ALC extension command for *device*."""

        extension = self._extension_for(name, "ALC")
        extension_apis = EXTENSION_APIS[extension]

        if check_extension:
            if "alc" in extension_apis:
                present = self.is_alc_extension_present(extension, device)
            elif "al" in extension_apis:
                present = self.is_al_extension_present(extension)
            else:
                raise FunctionUnavailableError(
                    f"extension {extension!r} has no AL or ALC API declaration"
                )
            if not present:
                raise ExtensionUnavailableError(
                    f"OpenAL extension {extension!r} is not available"
                )

        get_proc_address = self.get_export("alcGetProcAddress")
        address_value = cast(
            object,
            get_proc_address(device, _encoded_name(name, label="command name")),
        )
        return self._function_from_address(name, address_value)

    def get_function(
        self,
        name: str,
        *,
        device: object | None = None,
        check_extension: bool = True,
    ) -> ForeignFunction:
        """Return a typed core or extension function by its C command name."""

        if name not in PROTOTYPES:
            raise FunctionUnavailableError(f"unknown OpenAL command {name!r}")
        if name not in COMMAND_EXTENSIONS:
            return self.get_export(name)
        if COMMAND_NAMESPACES[name] == "AL":
            return self.get_al_extension(
                name,
                device=device,
                check_extension=check_extension,
            )
        return self.get_alc_extension(
            name,
            device,
            check_extension=check_extension,
        )

    def __getattr__(self, name: str) -> ForeignFunction:
        if name not in PROTOTYPES:
            raise AttributeError(name)
        if name not in COMMAND_EXTENSIONS:
            return self.get_export(name)
        if COMMAND_NAMESPACES[name] == "AL":
            return self.get_al_extension(name)
        raise AttributeError(
            f"{name!r} is device-specific; use get_function({name!r}, device=...)"
        )

    def __dir__(self) -> list[str]:
        return sorted({*super().__dir__(), *PROTOTYPES})


def load(path: LibraryPath | None = None) -> OpenALLibrary:
    """Load OpenAL and return its typed low-level binding object."""

    return OpenALLibrary(path)


__all__ = [
    "ContextRequiredError",
    "ExtensionUnavailableError",
    "ForeignFunction",
    "FunctionUnavailableError",
    "LibraryNotFoundError",
    "LibraryPath",
    "OpenALError",
    "OpenALLibrary",
    "load",
]
