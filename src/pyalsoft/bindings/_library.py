"""Runtime loading and function resolution for OpenAL."""

from __future__ import annotations

import ctypes
import ctypes.util
import os
import sys
import threading
from collections.abc import Callable, Sequence
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
    from pyalsoft.bindings._alc import CallbackRegistration
    from pyalsoft.bindings._generated.commands import ALCCommands, ALCommands
    from pyalsoft.bindings._generated.extensions import ExtensionCapabilities

type ForeignFunction = Callable[..., Any]
"""A generated ``ctypes`` callable for one raw OpenAL command."""

type LibraryPath = str | os.PathLike[str]
"""A string or path-like location of an OpenAL shared library."""

type _FunctionScope = tuple[str, int] | None
type _ExtensionFunctionKey = tuple[str, _FunctionScope, str, bool]


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


def _function_scope(value: object | None) -> _FunctionScope:
    """Return a stable cache scope for a native handle or test double."""

    if value is None:
        return None
    try:
        address = _pointer_address(value)
    except TypeError:
        return ("object", id(value))
    return None if address is None else ("pointer", address)


class OpenALLibrary:
    """A loaded OpenAL library with typed core and extension functions.

    Core functions and their generated signatures are bound lazily. AL extension
    functions are resolved for the current context; ALC extension functions are
    resolved for the device supplied to ``get_function`` or ``get_alc_extension``.
    Resolved entry points are cached by native context or device scope.

    Prefer [`load`][pyalsoft.bindings.load] unless direct construction is useful.
    A loaded library does not itself own devices or contexts and has no close
    operation.

    Args:
        path: Explicit shared-library path. ``None`` searches the bundled runtime,
            platform discovery results, and conventional OpenAL library names.

    Attributes:
        library_name: Path or loader name used to open the native library.
        native_library: Underlying ``ctypes.CDLL`` instance.
        al: Generated Python-value wrappers for AL commands and objects.
        alc: Generated Python-value wrappers for ALC commands.
        extensions: Generated mapping and attributes for registry extensions.

    Raises:
        TypeError: ``path`` is not string or path-like.
        LibraryNotFoundError: No usable library could be loaded.
    """

    def __init__(self, path: LibraryPath | None = None) -> None:
        self._handle, self.library_name = _load_shared_library(path)
        self._exports: dict[str, ForeignFunction] = {}
        self._extension_functions: dict[_ExtensionFunctionKey, ForeignFunction] = {}
        self._al_commands: ALCommands | None = None
        self._alc_commands: ALCCommands | None = None
        self._extension_capabilities: ExtensionCapabilities | None = None
        self._system_event_callback: object | None = None
        self._context_lock = threading.RLock()

    @property
    def native_library(self) -> Any:
        """The underlying ``ctypes.CDLL`` instance."""

        return self._handle

    @property
    def al(self) -> ALCommands:
        """Python-value wrappers for AL commands and typed AL objects.

        Core commands are available immediately. Calling extension commands may
        require a current context and raises an extension-resolution error when
        the entry point is unavailable.
        """

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
        """Generated capability objects for all registry extensions.

        Capabilities support mapping lookup by registry name and generated
        snake-case attributes such as ``alc_ext_efx``.
        """

        if self._extension_capabilities is None:
            from pyalsoft.bindings._generated.extensions import ExtensionCapabilities

            self._extension_capabilities = ExtensionCapabilities(self)
        return self._extension_capabilities

    def get_export(self, name: str) -> ForeignFunction:
        """Return a directly exported command without checking extensions.

        Args:
            name: Exact C command name present in the generated registry.

        Returns:
            A cached ``ctypes`` callable with the generated prototype.

        Raises:
            FunctionUnavailableError: ``name`` is unknown, is extension-only, or
                is not exported by the loaded library.
        """

        if name not in PROTOTYPES:
            raise FunctionUnavailableError(f"unknown OpenAL command {name!r}")
        if COMMAND_EXPORTS[name] is None:
            raise FunctionUnavailableError(
                f"OpenAL command {name!r} is not a direct library export"
            )

        with self._context_lock:
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

    def clear_extension_cache(self) -> None:
        """Clear all cached extension entry points.

        Owned devices and contexts invalidate their own scopes automatically.
        Applications that destroy or reconfigure raw native handles should
        clear this cache before a handle address can be reused.
        """

        with self._context_lock:
            self._extension_functions.clear()

    def register_system_event_callback(
        self,
        callback: Callable[[int, int, object | None, str], None],
        *,
        event_types: Sequence[int] = (),
    ) -> CallbackRegistration:
        """Register and retain the global ``ALC_SOFT_system_events`` callback.

        Only one registration may be active for the same loaded native library;
        registering another closes the previous one. The callback can run on a
        background system thread and receives ``(event_type, device_type,
        device_handle, message)``. It must return promptly and must not call AL or
        ALC functions. Python exceptions are retained by the returned registration.

        Args:
            callback: Function invoked for each enabled system event. A null native
                device pointer is delivered as ``None``.
            event_types: Registry event-type values to enable. An empty sequence
                installs the callback without explicitly enabling event types.

        Returns:
            Owned registration that keeps the native trampoline alive.

        Raises:
            TypeError: ``callback`` is not callable or an event type is not integer-like.
            ExtensionUnavailableError: ``ALC_SOFT_system_events`` is unavailable.
            CallbackControlError: OpenAL cannot enable the requested event types.
        """

        from pyalsoft.bindings._alc import _register_system_event_callback

        return _register_system_event_callback(
            self,
            callback,
            event_types=event_types,
        )

    def clear_system_event_callback(self) -> None:
        """Disable events and remove this native library's global callback.

        Calling this without an active registration is harmless. Callback errors
        retained during unregistration remain available on a registration held by
        the caller.

        Raises:
            CallbackControlError: Called from the active callback or while its
                registration close is already in progress on this thread.
        """

        from pyalsoft.bindings._alc import _clear_system_event_callback

        _clear_system_event_callback(self)

    def _invalidate_extension_scope(
        self,
        resolver: str,
        handle: object | None,
    ) -> None:
        scope = _function_scope(handle)
        with self._context_lock:
            stale = tuple(
                key
                for key in self._extension_functions
                if key[0] == resolver and key[1] == scope
            )
            for key in stale:
                self._extension_functions.pop(key, None)

    def _invalidate_context_extensions(self, context: object) -> None:
        self._invalidate_extension_scope("al", context)

    def _invalidate_device_extensions(self, device: object) -> None:
        self._invalidate_extension_scope("alc", device)

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
        """Check an AL extension against the current context.

        Args:
            extension: ASCII registry extension name.

        Returns:
            Whether the current context reports the extension.

        Raises:
            ValueError: ``extension`` contains non-ASCII characters.
            ContextRequiredError: No AL context is current.
            FunctionUnavailableError: A required core query is unavailable.
        """

        self._require_current_context()
        function = self.get_export("alIsExtensionPresent")
        result = cast(object, function(_encoded_name(extension, label="extension")))
        return _c_boolean(result)

    def is_alc_extension_present(
        self, extension: str, device: object | None = None
    ) -> bool:
        """Check an ALC extension for a device or the null-device scope.

        Args:
            extension: ASCII registry extension name.
            device: Native ALC device handle. ``None`` queries null-device
                extensions.

        Returns:
            Whether the selected ALC scope reports the extension.

        Raises:
            ValueError: ``extension`` contains non-ASCII characters.
            FunctionUnavailableError: The required core query is unavailable.
        """

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
        """Resolve and cache an AL extension command for the effective scope.

        AL-only commands use the current context. Commands from extensions that
        also expose ALC entry points can instead use an explicit device, which is
        required by direct-context APIs.

        Args:
            name: Exact C name of an AL extension command.
            device: Optional native ALC device used by dual-API extensions.
            check_extension: Verify that the effective context or device reports
                the declaring extension before resolving the entry point.

        Returns:
            A cached ``ctypes`` callable with the generated prototype.

        Raises:
            ValueError: ``name`` contains non-ASCII characters.
            ContextRequiredError: Resolution requires a context or device that was
                not supplied.
            ExtensionUnavailableError: The declaring extension is not present.
            FunctionUnavailableError: The name, namespace, or native entry point
                is invalid or unavailable.
        """

        with self._context_lock:
            return self._get_al_extension(
                name,
                device=device,
                check_extension=check_extension,
            )

    def _get_al_extension(
        self,
        name: str,
        *,
        device: object | None = None,
        check_extension: bool = True,
    ) -> ForeignFunction:
        """Resolve an AL extension command for the current context.

        Extensions that also support ALC are resolved through an explicitly
        supplied *device*. This keeps direct-context APIs independent of an
        unrelated context that may be current on the calling thread.
        """

        extension = self._extension_for(name, "AL")
        extension_apis = EXTENSION_APIS[extension]
        context = self._current_context()
        has_context = _pointer_address(context) is not None
        use_alc = "alc" in extension_apis and device is not None

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
            elif use_alc:
                present = self.is_alc_extension_present(extension, device)
            elif has_context and "al" in extension_apis:
                present = self.is_al_extension_present(extension)
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
            resolver = "alc"
            scope = _function_scope(device)
            cache_key = (resolver, scope, name, check_extension)
            cached = self._extension_functions.get(cache_key)
            if cached is not None:
                return cached
            get_proc_address = self.get_export("alcGetProcAddress")
            address_value = cast(object, get_proc_address(device, encoded_name))
        else:
            resolver = "al"
            scope = _function_scope(context)
            cache_key = (resolver, scope, name, check_extension)
            cached = self._extension_functions.get(cache_key)
            if cached is not None:
                return cached
            get_proc_address = self.get_export("alGetProcAddress")
            address_value = cast(object, get_proc_address(encoded_name))
        function = self._function_from_address(name, address_value)
        self._extension_functions[cache_key] = function
        return function

    def get_alc_extension(
        self,
        name: str,
        device: object | None = None,
        *,
        check_extension: bool = True,
    ) -> ForeignFunction:
        """Resolve and cache an ALC extension command for a device scope.

        Args:
            name: Exact C name of an ALC extension command.
            device: Native ALC device handle, or ``None`` for the null-device scope.
            check_extension: Verify that the effective scope reports the declaring
                extension before resolving the entry point.

        Returns:
            A cached ``ctypes`` callable with the generated prototype.

        Raises:
            ValueError: ``name`` contains non-ASCII characters.
            ContextRequiredError: An AL-side extension check requires a current
                context.
            ExtensionUnavailableError: The declaring extension is not present.
            FunctionUnavailableError: The name, namespace, or native entry point
                is invalid or unavailable.
        """

        with self._context_lock:
            return self._get_alc_extension(
                name,
                device,
                check_extension=check_extension,
            )

    def _get_alc_extension(
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

        cache_key = ("alc", _function_scope(device), name, check_extension)
        cached = self._extension_functions.get(cache_key)
        if cached is not None:
            return cached

        get_proc_address = self.get_export("alcGetProcAddress")
        address_value = cast(
            object,
            get_proc_address(device, _encoded_name(name, label="command name")),
        )
        function = self._function_from_address(name, address_value)
        self._extension_functions[cache_key] = function
        return function

    def get_function(
        self,
        name: str,
        *,
        device: object | None = None,
        check_extension: bool = True,
    ) -> ForeignFunction:
        """Return a typed core or extension function by its C command name.

        Direct exports use ``get_export``. Extension commands are routed to the
        AL or ALC resolver according to generated registry metadata.

        Args:
            name: Exact C command name.
            device: Native ALC device handle used for device-scoped resolution.
            check_extension: Verify extension availability before resolving an
                extension entry point.

        Returns:
            A cached ``ctypes`` callable with the generated prototype.

        Raises:
            ContextRequiredError: Resolution requires a current AL context.
            ExtensionUnavailableError: The command's extension is not present.
            FunctionUnavailableError: ``name`` is unknown or its entry point is
                unavailable.
            ValueError: A command or extension name cannot be ASCII encoded.
        """

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

    def _get_pointer_return_function(
        self,
        name: str,
        *,
        device: object | None = None,
    ) -> ForeignFunction:
        """Return *name* without ctypes converting its pointer result."""

        function = self.get_function(name, device=device)
        address = _pointer_address(function)
        if address is None:
            raise FunctionUnavailableError(
                f"OpenAL returned a null entry point for {name!r}"
            )
        argument_types = getattr(PROTOTYPES[name], "_argtypes_", None)
        if argument_types is None:
            raise FunctionUnavailableError(
                f"generated prototype for {name!r} has no argument types"
            )
        prototype = ctypes.CFUNCTYPE(ctypes.c_void_p, *argument_types)
        return cast(ForeignFunction, prototype(address))

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
    """Load OpenAL and return its typed low-level binding object.

    Each call creates an independent Python binding object and native-library
    handle. It does not cache a process-wide singleton.

    Args:
        path: Explicit shared-library path. ``None`` searches the bundled runtime,
            platform discovery results, and conventional OpenAL library names.

    Returns:
        Loaded low-level library with lazy command namespaces.

    Raises:
        TypeError: ``path`` is not string or path-like.
        LibraryNotFoundError: No usable library could be loaded.
    """

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
