"""Owned ALC device and context handles over the generated command API."""

from __future__ import annotations

import threading
from collections.abc import Callable, Iterator, Sequence
from contextlib import contextmanager, suppress
from typing import Self, cast

from pyalsoft.bindings._generated import constants as _constants
from pyalsoft.bindings._generated import enums as _enums
from pyalsoft.bindings._generated import types as _types
from pyalsoft.bindings._library import (
    LibraryNotFoundError,
    LibraryPath,
    OpenALError,
    OpenALLibrary,
    _pointer_address,
    load,
)

type EventCallback = Callable[[int, int, int, str], None]
type DebugCallback = Callable[[int, int, int, int, str], None]


class ALCHandleError(OpenALError):
    """Base exception for owned ALC device and context handles."""


class DeviceOpenError(ALCHandleError):
    """Raised when an ALC device cannot be opened."""


class DeviceCloseError(ALCHandleError):
    """Raised when an ALC device refuses to close."""


class ContextCreateError(ALCHandleError):
    """Raised when an ALC context cannot be created."""


class ContextActivationError(ALCHandleError):
    """Raised when an ALC context cannot be made current or restored."""


class HandleClosedError(ALCHandleError):
    """Raised when an operation requires an open device or context."""


def _same_pointer(left: object | None, right: object | None) -> bool:
    """Compare native handles while remaining friendly to test doubles."""

    if left is right:
        return True
    try:
        return _pointer_address(left) == _pointer_address(right)
    except TypeError:
        return False


def _enum_or_int[T](enum_type: type[T], value: int) -> T | int:
    try:
        return enum_type(value)
    except ValueError:
        return value


def _message_text(message: bytes | None, length: int) -> str:
    if not message:
        return ""
    encoded = message[: max(0, length)].rstrip(b"\0")
    return encoded.decode("utf-8", errors="replace")


class CallbackRegistration:
    """Own a native callback and unregister it deterministically.

    Native audio callbacks must never allow a Python exception to cross the C
    boundary. Exceptions are retained and can be observed through :attr:`errors`
    or re-raised in the registering thread with :meth:`raise_if_failed`.
    """

    def __init__(
        self,
        callback: object,
        close: Callable[[CallbackRegistration], None],
        errors: list[BaseException],
    ) -> None:
        self._callback = callback
        self._close = close
        self._errors = errors
        self._lock = threading.RLock()
        self._closed = False

    @property
    def closed(self) -> bool:
        """Whether the callback has been unregistered."""

        with self._lock:
            return self._closed

    @property
    def errors(self) -> tuple[BaseException, ...]:
        """Exceptions raised by the Python callback, in arrival order."""

        with self._lock:
            return tuple(self._errors)

    def raise_if_failed(self) -> None:
        """Raise and clear exceptions retained from native callback threads."""

        with self._lock:
            errors = tuple(self._errors)
            self._errors.clear()
        if errors:
            raise BaseExceptionGroup("OpenAL callback failed", errors)

    def _record_error(self, error: BaseException) -> None:
        with self._lock:
            self._errors.append(error)

    def close(self) -> None:
        """Unregister the callback. Calling this more than once is harmless."""

        with self._lock:
            if self._closed:
                return
            self._close(self)
            self._closed = True
            self._callback = None

    def __enter__(self) -> CallbackRegistration:
        if self.closed:
            raise HandleClosedError("callback registration is closed")
        return self

    def __exit__(
        self,
        exception_type: type[BaseException] | None,
        exception: BaseException | None,
        traceback: object | None,
    ) -> None:
        self.close()


class Device:
    """An owned ALC device handle.

    Use the more specific :class:`PlaybackDevice`, :class:`LoopbackDevice`, or
    :class:`CaptureDevice` subclasses returned by the module-level open helpers.
    Closing a device first closes contexts created through that device.
    """

    def __init__(self, library: OpenALLibrary, handle: object) -> None:
        self.library = library
        self._handle: object | None = handle
        self._contexts: list[Context] = []
        self._lock = threading.RLock()

    @property
    def closed(self) -> bool:
        """Whether the native device has been closed."""

        return self._handle is None

    @property
    def handle(self) -> object:
        """The underlying ALC device pointer for raw generated calls."""

        if self._handle is None:
            raise HandleClosedError("ALC device is closed")
        return self._handle

    def require_extension(self, name: str) -> None:
        """Require an ALC extension on this device."""

        self.library.extensions[name].require(self.handle)

    def is_extension_present(self, name: str) -> bool:
        """Return whether an ALC extension is present on this device."""

        return self.library.is_alc_extension_present(name, self.handle)

    def get_string(self, parameter: _enums.ALCContextString | int) -> str | None:
        """Query one device string through ``alcGetString``."""

        return self.library.alc.get_string(self.handle, parameter)

    def get_integers(
        self,
        parameter: _enums.ALCContextInteger | int,
        count: int = 1,
    ) -> tuple[int, ...]:
        """Query one or more device integers through ``alcGetIntegerv``."""

        if isinstance(count, bool) or not isinstance(count, int):
            raise TypeError("count must be an integer")
        if count < 1:
            raise ValueError("count must be at least one")
        return self.library.alc.get_integerv(self.handle, parameter, count)

    def get_integer(self, parameter: _enums.ALCContextInteger | int) -> int:
        """Query one device integer through ``alcGetIntegerv``."""

        return self.get_integers(parameter)[0]

    def get_integer64s(self, parameter: int, count: int = 1) -> tuple[int, ...]:
        """Query device clock values through ``ALC_SOFT_device_clock``."""

        if isinstance(count, bool) or not isinstance(count, int):
            raise TypeError("count must be an integer")
        if count < 1:
            raise ValueError("count must be at least one")
        self.require_extension("ALC_SOFT_device_clock")
        return self.library.alc.get_integer64v_soft(self.handle, parameter, count)

    @property
    def name(self) -> str | None:
        """The implementation-provided device name."""

        return self.get_string(_constants.ALC_DEVICE_SPECIFIER)

    @property
    def extensions(self) -> frozenset[str]:
        """Extensions reported for this device."""

        value = self.get_string(_constants.ALC_EXTENSIONS)
        return frozenset(value.split()) if value else frozenset()

    @property
    def version(self) -> tuple[int, int]:
        """The device's ALC major and minor version."""

        return (
            self.get_integer(_constants.ALC_MAJOR_VERSION),
            self.get_integer(_constants.ALC_MINOR_VERSION),
        )

    @property
    def connected(self) -> bool:
        """Whether the device remains connected (``ALC_EXT_disconnect``)."""

        self.require_extension("ALC_EXT_disconnect")
        return bool(self.get_integer(_constants.ALC_CONNECTED))

    @property
    def hrtf_enabled(self) -> bool:
        """Whether HRTF rendering is currently enabled."""

        self.require_extension("ALC_SOFT_HRTF")
        return bool(self.get_integer(_constants.ALC_HRTF_SOFT))

    @property
    def hrtf_status(self) -> _enums.ALCHrtfStatusSOFT | int:
        """The device's detailed HRTF status."""

        self.require_extension("ALC_SOFT_HRTF")
        value = self.get_integer(_constants.ALC_HRTF_STATUS_SOFT)
        return _enum_or_int(_enums.ALCHrtfStatusSOFT, value)

    @property
    def hrtf_name(self) -> str | None:
        """The active HRTF specifier, if any."""

        self.require_extension("ALC_SOFT_HRTF")
        return self.get_string(_constants.ALC_HRTF_SPECIFIER_SOFT)

    @property
    def hrtf_specifier_count(self) -> int:
        """The number of HRTF specifiers available to the device."""

        self.require_extension("ALC_SOFT_HRTF")
        return self.get_integer(_constants.ALC_NUM_HRTF_SPECIFIERS_SOFT)

    def get_hrtf_specifier(self, index: int) -> str | None:
        """Return one available HRTF specifier by index."""

        if isinstance(index, bool) or not isinstance(index, int):
            raise TypeError("index must be an integer")
        if index < 0:
            raise ValueError("index cannot be negative")
        self.require_extension("ALC_SOFT_HRTF")
        return self.library.alc.get_stringi_soft(
            self.handle,
            _constants.ALC_HRTF_SPECIFIER_SOFT,
            index,
        )

    @property
    def output_limiter_enabled(self) -> bool:
        """Whether the output limiter is currently enabled."""

        self.require_extension("ALC_SOFT_output_limiter")
        return bool(self.get_integer(_constants.ALC_OUTPUT_LIMITER_SOFT))

    @property
    def device_clock(self) -> int:
        """The device clock in nanoseconds."""

        return self.get_integer64s(_constants.ALC_DEVICE_CLOCK_SOFT)[0]

    @property
    def device_latency(self) -> int:
        """The device latency in nanoseconds."""

        return self.get_integer64s(_constants.ALC_DEVICE_LATENCY_SOFT)[0]

    @property
    def clock_latency(self) -> tuple[int, int]:
        """Atomically query device clock and latency in nanoseconds."""

        clock, latency = self.get_integer64s(
            _constants.ALC_DEVICE_CLOCK_LATENCY_SOFT,
            2,
        )
        return clock, latency

    @property
    def output_mode(self) -> _enums.ALCOutputModeSOFT | int:
        """The active output mode (``ALC_SOFT_output_mode``)."""

        self.require_extension("ALC_SOFT_output_mode")
        value = self.get_integer(_constants.ALC_OUTPUT_MODE_SOFT)
        return _enum_or_int(_enums.ALCOutputModeSOFT, value)

    @property
    def max_ambisonic_order(self) -> int:
        """The highest loopback ambisonic order supported by the device."""

        self.require_extension("ALC_SOFT_loopback_bformat")
        return self.get_integer(_constants.ALC_MAX_AMBISONIC_ORDER_SOFT)

    @property
    def context_flags(self) -> _enums.ALCContextFlagsEXT:
        """The active context flags (``ALC_EXT_debug``)."""

        self.require_extension("ALC_EXT_debug")
        return _enums.ALCContextFlagsEXT(
            self.get_integer(_constants.ALC_CONTEXT_FLAGS_EXT)
        )

    def close(self) -> None:
        """Close owned contexts and then the native device."""

        with self._lock:
            if self._handle is None:
                return
            for context in reversed(tuple(self._contexts)):
                context.close()
            handle = self._handle
            if not self._close_native(handle):
                raise DeviceCloseError("OpenAL refused to close the ALC device")
            self._handle = None

    def _close_native(self, handle: object) -> bool:
        return self.library.alc.close_device(handle)

    def _forget_context(self, context: Context) -> None:
        with suppress(ValueError):
            self._contexts.remove(context)

    def __enter__(self) -> Self:
        if self.closed:
            raise HandleClosedError("ALC device is closed")
        return self

    def __exit__(
        self,
        exception_type: type[BaseException] | None,
        exception: BaseException | None,
        traceback: object | None,
    ) -> None:
        self.close()

    def __repr__(self) -> str:
        state = "closed" if self.closed else f"handle={self._handle!r}"
        return f"{type(self).__name__}(library={self.library!r}, {state})"


class PlaybackDevice(Device):
    """An owned playback device that can create AL contexts."""

    def create_context(self, attributes: Sequence[int] | None = None) -> Context:
        """Create an owned context attached to this device."""

        with self._lock:
            handle = self.library.alc.create_context(self.handle, attributes)
            if handle is None or _pointer_address(handle) is None:
                raise ContextCreateError(
                    "OpenAL could not create the requested context"
                )
            context = Context(self, handle)
            self._contexts.append(context)
            return context


class LoopbackDevice(PlaybackDevice):
    """An ``ALC_SOFT_loopback`` device for deterministic offline rendering."""

    def is_render_format_supported(
        self,
        frequency: int,
        channels: _enums.ALCRenderFormatChannelSOFT | int,
        sample_type: _enums.ALCRenderFormatTypeSOFT | int,
    ) -> bool:
        """Return whether a loopback render format is supported."""

        return self.library.alc.is_render_format_supported_soft(
            self.handle,
            frequency,
            channels,
            sample_type,
        )

    def render_samples(self, buffer: object, samples: int) -> None:
        """Render *samples* frames into caller-owned writable storage."""

        if isinstance(samples, bool) or not isinstance(samples, int):
            raise TypeError("samples must be an integer")
        if samples < 0:
            raise ValueError("samples cannot be negative")
        self.library.alc.render_samples_soft(self.handle, buffer, samples)


class CaptureDevice(Device):
    """An owned input device opened through the core ALC capture API."""

    def __init__(
        self,
        library: OpenALLibrary,
        handle: object,
        *,
        frequency: int,
        format: _enums.ALFormat | int,
    ) -> None:
        super().__init__(library, handle)
        self.frequency = frequency
        self.format = format
        self._capturing = False

    @property
    def available_samples(self) -> int:
        """The number of capture frames currently ready to read."""

        return self.get_integer(_constants.ALC_CAPTURE_SAMPLES)

    @property
    def name(self) -> str | None:
        """The implementation-provided capture device name."""

        return self.get_string(_constants.ALC_CAPTURE_DEVICE_SPECIFIER)

    @property
    def capturing(self) -> bool:
        """Whether capture has been started through this handle."""

        return self._capturing

    def start(self) -> None:
        """Start input capture. Calling this while started is harmless."""

        if self._capturing:
            return
        self.library.alc.capture_start(self.handle)
        self._capturing = True

    def stop(self) -> None:
        """Stop input capture. Buffered samples remain available."""

        if not self._capturing:
            return
        self.library.alc.capture_stop(self.handle)
        self._capturing = False

    def read_samples(self, buffer: object, samples: int) -> None:
        """Read capture frames into caller-owned writable storage."""

        if isinstance(samples, bool) or not isinstance(samples, int):
            raise TypeError("samples must be an integer")
        if samples < 0:
            raise ValueError("samples cannot be negative")
        self.library.alc.capture_samples(self.handle, buffer, samples)

    def close(self) -> None:
        if not self.closed:
            self.stop()
        super().close()

    def _close_native(self, handle: object) -> bool:
        return self.library.alc.capture_close_device(handle)


class Context:
    """An owned AL context attached to a :class:`PlaybackDevice`."""

    def __init__(self, device: PlaybackDevice, handle: object) -> None:
        self.device = device
        self.library = device.library
        self._handle: object | None = handle
        self._callbacks: dict[str, CallbackRegistration] = {}
        self._lock = threading.RLock()

    @property
    def closed(self) -> bool:
        """Whether the native context has been destroyed."""

        return self._handle is None

    @property
    def handle(self) -> object:
        """The underlying ALC context pointer for raw generated calls."""

        if self._handle is None:
            raise HandleClosedError("ALC context is closed")
        return self._handle

    @property
    def current(self) -> bool:
        """Whether this is the process-wide current context."""

        with self.library._context_lock:
            return _same_pointer(
                self.library.alc.get_current_context(),
                self.handle,
            )

    def make_current(self) -> None:
        """Make this the process-wide current context."""

        with self.library._context_lock:
            if not self.library.alc.make_context_current(self.handle):
                raise ContextActivationError(
                    "OpenAL could not make the context current"
                )

    def make_thread_current(self) -> None:
        """Make this current only for this thread when the extension is present."""

        with self.library._context_lock:
            self.device.require_extension("ALC_EXT_thread_local_context")
            if not self.library.alc.set_thread_context(self.handle):
                raise ContextActivationError(
                    "OpenAL could not make the context current for this thread"
                )

    def require_extension(self, name: str) -> None:
        """Require an AL extension while this context is current."""

        with self.activate():
            self.library.extensions[name].require()

    @contextmanager
    def activate(self, *, thread_local: bool = False) -> Iterator[Context]:
        """Temporarily make this context current, restoring the prior context."""

        with self.library._context_lock, self._lock:
            handle = self.handle
            if thread_local:
                self.device.require_extension("ALC_EXT_thread_local_context")
                previous = self.library.alc.get_thread_context()
                setter = self.library.alc.set_thread_context
            else:
                previous = self.library.alc.get_current_context()
                setter = self.library.alc.make_context_current

            changed = not _same_pointer(previous, handle)
            if changed and not setter(handle):
                scope = "thread" if thread_local else "process"
                raise ContextActivationError(
                    f"OpenAL could not activate the context for this {scope}"
                )
            try:
                yield self
            finally:
                if changed and not setter(previous):
                    raise ContextActivationError(
                        "OpenAL could not restore the previous context"
                    )

    def _get_string(self, parameter: int) -> str | None:
        with self.activate():
            return self.library.al.get_string(parameter)

    def _get_float(self, parameter: int) -> float:
        with self.activate():
            return self.library.al.get_float(parameter)

    def _set_float(self, command: str, value: float) -> None:
        with self.activate():
            cast(Callable[[float], None], getattr(self.library.al, command))(value)

    @property
    def vendor(self) -> str | None:
        """The current AL implementation vendor."""

        return self._get_string(_constants.AL_VENDOR)

    @property
    def version(self) -> str | None:
        """The current AL implementation version."""

        return self._get_string(_constants.AL_VERSION)

    @property
    def renderer(self) -> str | None:
        """The current AL renderer name."""

        return self._get_string(_constants.AL_RENDERER)

    @property
    def extensions(self) -> frozenset[str]:
        """Extensions reported for this AL context."""

        value = self._get_string(_constants.AL_EXTENSIONS)
        return frozenset(value.split()) if value else frozenset()

    @property
    def doppler_factor(self) -> float:
        return self._get_float(_constants.AL_DOPPLER_FACTOR)

    @doppler_factor.setter
    def doppler_factor(self, value: float) -> None:
        self._set_float("doppler_factor", value)

    @property
    def doppler_velocity(self) -> float:
        return self._get_float(_constants.AL_DOPPLER_VELOCITY)

    @doppler_velocity.setter
    def doppler_velocity(self, value: float) -> None:
        self._set_float("doppler_velocity", value)

    @property
    def speed_of_sound(self) -> float:
        return self._get_float(_constants.AL_SPEED_OF_SOUND)

    @speed_of_sound.setter
    def speed_of_sound(self, value: float) -> None:
        self._set_float("speed_of_sound", value)

    @property
    def distance_model(self) -> _enums.ALDistanceModel | int:
        with self.activate():
            value = self.library.al.get_integer(_constants.AL_DISTANCE_MODEL)
        return _enum_or_int(_enums.ALDistanceModel, value)

    @distance_model.setter
    def distance_model(self, value: _enums.ALDistanceModel | int) -> None:
        with self.activate():
            self.library.al.distance_model(value)

    @property
    def default_filter_order(self) -> int:
        """The default resampler filter order for this context."""

        self.device.require_extension("ALC_EXT_DEFAULT_FILTER_ORDER")
        with self.activate():
            return self.library.al.get_integer(_constants.ALC_DEFAULT_FILTER_ORDER)

    def register_event_callback(
        self,
        callback: EventCallback,
        *,
        event_types: Sequence[int] = (),
    ) -> CallbackRegistration:
        """Register and retain a safe ``AL_SOFT_events`` callback."""

        if not callable(callback):
            raise TypeError("callback must be callable")
        self.require_extension("AL_SOFT_events")
        enabled_types = tuple(int(item) for item in event_types)
        previous = self._callbacks.get("event")
        if previous is not None:
            previous.close()
        errors: list[BaseException] = []

        def receive(
            event_type: int,
            object_id: int,
            parameter: int,
            length: int,
            message: bytes | None,
            _user_parameter: object | None,
        ) -> None:
            try:
                callback(
                    int(event_type),
                    int(object_id),
                    int(parameter),
                    _message_text(message, int(length)),
                )
            except BaseException as error:
                registration._record_error(error)

        native_callback = _types.ALEVENTPROCSOFT(receive)

        def unregister(registration: CallbackRegistration) -> None:
            if self._callbacks.get("event") is not registration:
                return
            with self.activate():
                self.library.al.event_callback_soft(_types.ALEVENTPROCSOFT(), None)
                if enabled_types:
                    self.library.al.event_control_soft(enabled_types, False)
            self._callbacks.pop("event", None)

        registration = CallbackRegistration(native_callback, unregister, errors)
        with self.activate():
            self.library.al.event_callback_soft(native_callback, None)
            if enabled_types:
                self.library.al.event_control_soft(enabled_types, True)
        self._callbacks["event"] = registration
        return registration

    def register_debug_callback(
        self,
        callback: DebugCallback,
        *,
        enable_output: bool = True,
    ) -> CallbackRegistration:
        """Register and retain a safe ``AL_EXT_debug`` message callback."""

        if not callable(callback):
            raise TypeError("callback must be callable")
        self.require_extension("AL_EXT_debug")
        previous = self._callbacks.get("debug")
        if previous is not None:
            previous.close()
        errors: list[BaseException] = []

        def receive(
            source: int,
            type: int,
            identifier: int,
            severity: int,
            length: int,
            message: bytes | None,
            _user_parameter: object | None,
        ) -> None:
            try:
                callback(
                    int(source),
                    int(type),
                    int(identifier),
                    int(severity),
                    _message_text(message, int(length)),
                )
            except BaseException as error:
                registration._record_error(error)

        native_callback = _types.ALDEBUGPROCEXT(receive)
        with self.activate():
            was_enabled = self.library.al.is_enabled(_constants.AL_DEBUG_OUTPUT_EXT)

        def unregister(registration: CallbackRegistration) -> None:
            if self._callbacks.get("debug") is not registration:
                return
            with self.activate():
                self.library.al.debug_message_callback_ext(
                    _types.ALDEBUGPROCEXT(), None
                )
                if enable_output and not was_enabled:
                    self.library.al.disable(_constants.AL_DEBUG_OUTPUT_EXT)
            self._callbacks.pop("debug", None)

        registration = CallbackRegistration(native_callback, unregister, errors)
        with self.activate():
            self.library.al.debug_message_callback_ext(native_callback, None)
            if enable_output and not was_enabled:
                self.library.al.enable(_constants.AL_DEBUG_OUTPUT_EXT)
        self._callbacks["debug"] = registration
        return registration

    def close(self) -> None:
        """Unregister callbacks, detach, and destroy the native context."""

        with self.library._context_lock, self._lock:
            if self._handle is None:
                return
            for registration in reversed(tuple(self._callbacks.values())):
                registration.close()
            handle = self._handle
            if _same_pointer(
                self.library.alc.get_current_context(), handle
            ) and not self.library.alc.make_context_current(None):
                raise ContextActivationError(
                    "OpenAL could not detach the context before destruction"
                )
            if self.device.is_extension_present("ALC_EXT_thread_local_context"):
                thread_context = self.library.alc.get_thread_context()
                if _same_pointer(
                    thread_context, handle
                ) and not self.library.alc.set_thread_context(None):
                    raise ContextActivationError(
                        "OpenAL could not detach the thread-local context"
                    )
            self.library.alc.destroy_context(handle)
            self._handle = None
            self.device._forget_context(self)

    def __enter__(self) -> Self:
        if self.closed:
            raise HandleClosedError("ALC context is closed")
        return self

    def __exit__(
        self,
        exception_type: type[BaseException] | None,
        exception: BaseException | None,
        traceback: object | None,
    ) -> None:
        self.close()

    def __repr__(self) -> str:
        state = "closed" if self.closed else f"handle={self._handle!r}"
        return f"Context(device={self.device!r}, {state})"


def _selected_library(
    library: OpenALLibrary | None,
    path: LibraryPath | None,
) -> OpenALLibrary:
    if library is not None and path is not None:
        raise ValueError("library and path cannot both be supplied")
    if library is not None:
        return library
    try:
        return load(path)
    except LibraryNotFoundError as error:
        raise DeviceOpenError("could not load an OpenAL library") from error


def open_device(
    name: str | bytes | None = None,
    *,
    library: OpenALLibrary | None = None,
    path: LibraryPath | None = None,
) -> PlaybackDevice:
    """Open an owned playback device."""

    selected = _selected_library(library, path)
    handle = selected.alc.open_device(name)
    if handle is None or _pointer_address(handle) is None:
        raise DeviceOpenError("OpenAL could not open the requested playback device")
    return PlaybackDevice(selected, handle)


def open_loopback_device(
    name: str | bytes | None = None,
    *,
    library: OpenALLibrary | None = None,
    path: LibraryPath | None = None,
) -> LoopbackDevice:
    """Open an owned ``ALC_SOFT_loopback`` device."""

    selected = _selected_library(library, path)
    selected.extensions["ALC_SOFT_loopback"].require()
    handle = selected.alc.loopback_open_device_soft(name)
    if handle is None or _pointer_address(handle) is None:
        raise DeviceOpenError("OpenAL could not open a loopback device")
    return LoopbackDevice(selected, handle)


def open_capture_device(
    frequency: int,
    format: _enums.ALFormat | int,
    buffer_size: int,
    name: str | bytes | None = None,
    *,
    library: OpenALLibrary | None = None,
    path: LibraryPath | None = None,
) -> CaptureDevice:
    """Open an owned core ALC capture device."""

    if isinstance(frequency, bool) or not isinstance(frequency, int):
        raise TypeError("frequency must be an integer")
    if frequency <= 0:
        raise ValueError("frequency must be positive")
    if isinstance(buffer_size, bool) or not isinstance(buffer_size, int):
        raise TypeError("buffer_size must be an integer")
    if buffer_size <= 0:
        raise ValueError("buffer_size must be positive")
    selected = _selected_library(library, path)
    handle = selected.alc.capture_open_device(
        name,
        frequency,
        format,
        buffer_size,
    )
    if handle is None or _pointer_address(handle) is None:
        raise DeviceOpenError("OpenAL could not open the requested capture device")
    return CaptureDevice(
        selected,
        handle,
        frequency=frequency,
        format=format,
    )


__all__ = [
    "ALCHandleError",
    "CallbackRegistration",
    "CaptureDevice",
    "Context",
    "ContextActivationError",
    "ContextCreateError",
    "DebugCallback",
    "Device",
    "DeviceCloseError",
    "DeviceOpenError",
    "EventCallback",
    "HandleClosedError",
    "LoopbackDevice",
    "PlaybackDevice",
    "open_capture_device",
    "open_device",
    "open_loopback_device",
]
