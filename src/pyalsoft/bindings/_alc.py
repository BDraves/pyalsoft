"""Owned ALC device and context handles over the generated command API."""

from __future__ import annotations

import ctypes
import threading
from collections.abc import Callable, Iterator, Sequence
from contextlib import AbstractContextManager, ExitStack, contextmanager, suppress
from typing import TYPE_CHECKING, Self, cast

from pyalsoft.bindings._generated import constants as _constants
from pyalsoft.bindings._generated import enums as _enums
from pyalsoft.bindings._generated import types as _types
from pyalsoft.bindings._generated.objects import Buffer
from pyalsoft.bindings._library import (
    LibraryNotFoundError,
    LibraryPath,
    OpenALError,
    OpenALLibrary,
    _pointer_address,
    load,
)

if TYPE_CHECKING:
    from pyalsoft.bindings._generated.objects import (
        AuxiliaryEffectSlot,
        Effect,
        Filter,
        Listener,
        Source,
    )

type EventCallback = Callable[[int, int, int, str], None]
type DebugCallback = Callable[[int, int, int, int, str], None]
type SystemEventCallback = Callable[[int, int, object | None, str], None]
type BufferCallback = Callable[[memoryview], int]
type FoldbackCallback = Callable[[int, int], None]


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


class NativeCallError(ALCHandleError):
    """Raised when a lifetime-sensitive native operation reports an error."""


class CallbackControlError(NativeCallError):
    """Raised when native callback state cannot be enabled or removed safely."""


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


def _message_text(message: bytes | None, length: int) -> str:
    if not message:
        return ""
    encoded = message[: max(0, length)].rstrip(b"\0")
    return encoded.decode("utf-8", errors="replace")


def _buffer_identifier(value: Buffer | int, context: Context) -> int:
    if isinstance(value, Buffer):
        if value.context is not context:
            raise ValueError("buffer belongs to a different OpenAL context")
        identifier = value.identifier
    else:
        identifier = value
    if isinstance(identifier, bool) or not isinstance(identifier, int):
        raise TypeError("buffer must be an integer or Buffer")
    if identifier <= 0:
        raise ValueError("buffer must be positive")
    return identifier


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


def _retained_byte_buffer(data: object) -> tuple[object, int, tuple[object, ...]]:
    if isinstance(data, bytes):
        backing = (ctypes.c_ubyte * len(data)).from_buffer_copy(data)
        return backing, len(data), (data, backing)
    if isinstance(data, bytearray):
        if not data:
            backing = (ctypes.c_ubyte * 0)()
        else:
            backing = (ctypes.c_ubyte * len(data)).from_buffer(data)
        return backing, len(data), (data, backing)
    if isinstance(data, memoryview):
        try:
            view = data.cast("B")
        except (TypeError, ValueError) as error:
            raise TypeError("data must be a contiguous byte buffer") from error
        array_type = ctypes.c_ubyte * view.nbytes
        if not view.nbytes:
            backing = array_type()
        elif view.readonly:
            backing = array_type.from_buffer_copy(view)
        else:
            backing = array_type.from_buffer(view)
        return backing, view.nbytes, (data, view, backing)
    raise TypeError("data must be bytes, bytearray, or memoryview")


def _retained_float_buffer(
    memory: object,
) -> tuple[object, int, tuple[object, ...]]:
    if isinstance(memory, ctypes.Array):
        element_type = getattr(memory, "_type_", None)
        if element_type is not _types.ALfloat:
            raise TypeError("ctypes foldback memory must be an ALfloat array")
        if not len(memory):
            raise ValueError("foldback memory cannot be empty")
        return memory, len(memory), (memory,)
    if isinstance(memory, (bytearray, memoryview)):
        source = memoryview(memory)
        try:
            view = source.cast("B")
        except (TypeError, ValueError) as error:
            raise TypeError("foldback memory must be contiguous") from error
        if view.readonly:
            raise TypeError("foldback memory must be writable")
        item_size = ctypes.sizeof(_types.ALfloat)
        if view.nbytes % item_size:
            raise ValueError("foldback memory size must be a multiple of ALfloat")
        if not view.nbytes:
            raise ValueError("foldback memory cannot be empty")
        backing = (_types.ALfloat * (view.nbytes // item_size)).from_buffer(view)
        return backing, len(backing), (memory, source, view, backing)
    if isinstance(memory, Sequence) and not isinstance(memory, (str, bytes)):
        if len(memory) == 0:
            raise ValueError("foldback memory cannot be empty")
        try:
            backing = (_types.ALfloat * len(memory))(*memory)
        except (TypeError, ValueError) as error:
            raise TypeError("foldback memory must contain real numbers") from error
        return backing, len(backing), (memory, backing)
    raise TypeError(
        "foldback memory must be an ALfloat array, writable buffer, or sequence"
    )


def _callback_buffer(address_value: object, size: int) -> memoryview:
    address = _pointer_address(address_value)
    if address is None:
        raise ValueError("OpenAL supplied a null callback sample buffer")
    array = (ctypes.c_ubyte * size).from_address(address)
    return memoryview(array).cast("B")


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
        *,
        resources: Sequence[object] = (),
        owner_locks: Sequence[AbstractContextManager[object]] = (),
    ) -> None:
        self._callback = callback
        self._close = close
        self._errors = errors
        self._lock = threading.RLock()
        self._condition = threading.Condition(self._lock)
        self._closed = False
        self._closing = False
        self._closing_thread: int | None = None
        self._callback_threads: dict[int, int] = {}
        self._resources = tuple(resources)
        self._owner_locks = tuple(owner_locks)

    @contextmanager
    def _serialized(self) -> Iterator[None]:
        with ExitStack() as stack:
            for lock in self._owner_locks:
                stack.enter_context(lock)
            yield

    def _finish_close_locked(self) -> None:
        self._closed = True
        self._closing = False
        self._closing_thread = None
        self._callback = None
        self._resources = ()
        self._condition.notify_all()

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

    def _begin_callback(self) -> None:
        with self._condition:
            thread = threading.get_ident()
            self._callback_threads[thread] = self._callback_threads.get(thread, 0) + 1

    def _end_callback(self) -> None:
        with self._condition:
            thread = threading.get_ident()
            remaining = self._callback_threads[thread] - 1
            if remaining:
                self._callback_threads[thread] = remaining
            else:
                self._callback_threads.pop(thread)
            self._condition.notify_all()

    def close(self) -> None:
        """Unregister the callback. Calling this more than once is harmless."""

        thread = threading.get_ident()
        while True:
            initiated = False
            with self._serialized():
                with self._condition:
                    if thread in self._callback_threads:
                        raise CallbackControlError(
                            "callback registration cannot be closed from its callback"
                        )
                    if self._closed:
                        return
                    if self._closing_thread == thread:
                        raise CallbackControlError(
                            "callback registration close cannot be re-entered"
                        )
                    if not self._closing:
                        self._closing = True
                        self._closing_thread = thread
                        initiated = True

                if initiated:
                    try:
                        self._close(self)
                    except BaseException:
                        with self._condition:
                            self._closing = False
                            self._closing_thread = None
                            self._condition.notify_all()
                        raise

            with self._condition:
                if initiated:
                    while self._callback_threads:
                        self._condition.wait()
                    self._finish_close_locked()
                    return
                while self._closing:
                    self._condition.wait()

    def _owner_closed(self) -> None:
        """Finalize after the native owner has destroyed callback state."""

        thread = threading.get_ident()
        while True:
            initiated = False
            with self._serialized(), self._condition:
                if self._closed:
                    return
                if not self._closing:
                    self._closing = True
                    self._closing_thread = thread
                    initiated = True
            with self._condition:
                if initiated:
                    while self._callback_threads:
                        self._condition.wait()
                    self._finish_close_locked()
                    return
                while self._closing:
                    self._condition.wait()

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


class FoldbackRegistration(CallbackRegistration):
    """Own an active foldback request and its writable sample storage."""

    def __init__(
        self,
        callback: object,
        close: Callable[[CallbackRegistration], None],
        errors: list[BaseException],
        memory: object,
        *,
        resources: Sequence[object] = (),
        owner_locks: Sequence[AbstractContextManager[object]] = (),
    ) -> None:
        super().__init__(
            callback,
            close,
            errors,
            resources=(memory, *resources),
            owner_locks=owner_locks,
        )
        self.memory = memory
        self._stop_requested = False
        self._stop_received = False

    @property
    def stopping(self) -> bool:
        """Whether native foldback stop has been requested."""

        with self._lock:
            return self._stop_requested and not self._closed

    def _native_stopped(self) -> None:
        with self._condition:
            self._stop_received = True
            self._condition.notify_all()

    def close(self) -> None:
        """Request foldback stop and wait for the native STOP event."""

        with self._serialized():
            with self._condition:
                thread = threading.get_ident()
                if self._closed:
                    return
                if thread in self._callback_threads:
                    raise CallbackControlError(
                        "foldback cannot be closed from its native callback"
                    )
                if self._closing:
                    if self._closing_thread == thread:
                        raise CallbackControlError(
                            "foldback close cannot be re-entered"
                        )
                    while not self._closed:
                        self._condition.wait()
                    return
                self._closing = True
                self._closing_thread = thread
                stop_received = self._stop_received

            try:
                if not stop_received:
                    self._close(self)
            except BaseException:
                with self._condition:
                    self._closing = False
                    self._closing_thread = None
                    self._condition.notify_all()
                raise

            with self._condition:
                if not stop_received:
                    self._stop_requested = True
                while not self._stop_received or self._callback_threads:
                    self._condition.wait()
                self._finish_close_locked()


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

        with self.library._context_lock, self._lock:
            if self._handle is None:
                return
            for context in reversed(tuple(self._contexts)):
                context.close()
            handle = self._handle
            if not self._close_native(handle):
                raise DeviceCloseError("OpenAL refused to close the ALC device")
            self.library._invalidate_device_extensions(handle)
            self._handle = None

    def _close_native(self, handle: object) -> bool:
        return self.library.alc.close_device(handle)

    def _forget_context(self, context: Context) -> None:
        with self._lock, suppress(ValueError):
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

        with self.library._context_lock, self._lock:
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
        self._buffer_callbacks: dict[int, CallbackRegistration] = {}
        self._foldback: FoldbackRegistration | None = None
        self._static_buffers: dict[int, tuple[object, ...]] = {}
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

    def source(self, identifier: int) -> Source:
        """Return a typed source bound to this context."""

        from pyalsoft.bindings._generated.objects import Source

        return Source(self, identifier)

    def buffer(self, identifier: int) -> Buffer:
        """Return a typed buffer bound to this context."""

        return Buffer(self, identifier)

    def effect(self, identifier: int) -> Effect:
        """Return a typed effect bound to this context."""

        from pyalsoft.bindings._generated.objects import Effect

        return Effect(self, identifier)

    def filter(self, identifier: int) -> Filter:
        """Return a typed filter bound to this context."""

        from pyalsoft.bindings._generated.objects import Filter

        return Filter(self, identifier)

    def auxiliary_effect_slot(self, identifier: int) -> AuxiliaryEffectSlot:
        """Return a typed auxiliary effect slot bound to this context."""

        from pyalsoft.bindings._generated.objects import AuxiliaryEffectSlot

        return AuxiliaryEffectSlot(self, identifier)

    @property
    def listener(self) -> Listener:
        """Return the typed listener singleton bound to this context."""

        from pyalsoft.bindings._generated.objects import Listener

        return Listener(self)

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
        enabled_types = tuple(
            _integer_value(item, label="event type") for item in event_types
        )
        owner_locks = (self.library._context_lock, self._lock)

        with self.library._context_lock, self._lock:
            self.require_extension("AL_SOFT_events")
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
                registration._begin_callback()
                try:
                    callback(
                        int(event_type),
                        int(object_id),
                        int(parameter),
                        _message_text(message, int(length)),
                    )
                except BaseException as error:
                    registration._record_error(error)
                finally:
                    registration._end_callback()

            native_callback = _types.ALEVENTPROCSOFT(receive)

            def unregister(registration: CallbackRegistration) -> None:
                if self._callbacks.get("event") is not registration:
                    return
                with self.activate():
                    self.library.al.event_callback_soft(_types.ALEVENTPROCSOFT(), None)
                    if enabled_types:
                        self.library.al.event_control_soft(enabled_types, False)
                self._callbacks.pop("event", None)

            registration = CallbackRegistration(
                native_callback,
                unregister,
                errors,
                owner_locks=owner_locks,
            )
            try:
                with self.activate():
                    self.library.al.event_callback_soft(native_callback, None)
                    if enabled_types:
                        self.library.al.event_control_soft(enabled_types, True)
            except BaseException:
                with suppress(BaseException), self.activate():
                    if enabled_types:
                        with suppress(BaseException):
                            self.library.al.event_control_soft(enabled_types, False)
                    with suppress(BaseException):
                        self.library.al.event_callback_soft(
                            _types.ALEVENTPROCSOFT(), None
                        )
                raise
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
        owner_locks = (self.library._context_lock, self._lock)

        with self.library._context_lock, self._lock:
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
                registration._begin_callback()
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
                finally:
                    registration._end_callback()

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

            registration = CallbackRegistration(
                native_callback,
                unregister,
                errors,
                owner_locks=owner_locks,
            )
            try:
                with self.activate():
                    self.library.al.debug_message_callback_ext(native_callback, None)
                    if enable_output and not was_enabled:
                        self.library.al.enable(_constants.AL_DEBUG_OUTPUT_EXT)
            except BaseException:
                with suppress(BaseException), self.activate():
                    with suppress(BaseException):
                        self.library.al.debug_message_callback_ext(
                            _types.ALDEBUGPROCEXT(), None
                        )
                    if enable_output and not was_enabled:
                        with suppress(BaseException):
                            self.library.al.disable(_constants.AL_DEBUG_OUTPUT_EXT)
                raise
            self._callbacks["debug"] = registration
            return registration

    def register_buffer_callback(
        self,
        buffer: Buffer | int,
        format: _enums.ALFormat | int,
        frequency: int,
        callback: BufferCallback,
    ) -> CallbackRegistration:
        """Register a lifetime-safe ``AL_SOFT_callback_buffer`` callback.

        The callback receives a writable byte view valid only for that callback
        invocation and must return the number of bytes written. Python callback
        execution is not guaranteed to satisfy hard real-time constraints.
        """

        if not callable(callback):
            raise TypeError("callback must be callable")
        buffer_id = _buffer_identifier(buffer, self)
        frequency = _positive_integer(frequency, label="frequency")
        format_value = _integer_value(format, label="format")
        owner_locks = (self.library._context_lock, self._lock)

        with self.library._context_lock, self._lock:
            self.require_extension("AL_SOFT_callback_buffer")
            previous = self._buffer_callbacks.get(buffer_id)
            if previous is not None:
                previous.close()
            errors: list[BaseException] = []

            def receive(
                _user_pointer: object | None,
                sample_data: object | None,
                requested_bytes: int,
            ) -> int:
                view: memoryview | None = None
                registration._begin_callback()
                try:
                    requested = int(requested_bytes)
                    view = _callback_buffer(sample_data, requested)
                    written = callback(view)
                    if isinstance(written, bool) or not isinstance(written, int):
                        raise TypeError("buffer callback must return an integer")
                    if written < 0 or written > requested:
                        raise ValueError(
                            "buffer callback byte count must be between zero and "
                            f"{requested}"
                        )
                    return written
                except BaseException as error:
                    registration._record_error(error)
                    return 0
                finally:
                    if view is not None:
                        view.release()
                    registration._end_callback()

            native_callback = _types.ALBUFFERCALLBACKTYPESOFT(receive)

            def unregister(registration: CallbackRegistration) -> None:
                if self._buffer_callbacks.get(buffer_id) is not registration:
                    return
                with self.activate():
                    current = self.library.al.get_buffer_ptr_soft(
                        buffer_id,
                        _constants.AL_BUFFER_CALLBACK_FUNCTION_SOFT,
                    )
                    if _same_pointer(current, native_callback):
                        self.library.al.buffer_data(
                            buffer_id,
                            format_value,
                            b"",
                            frequency,
                        )
                        remaining = self.library.al.get_buffer_ptr_soft(
                            buffer_id,
                            _constants.AL_BUFFER_CALLBACK_FUNCTION_SOFT,
                        )
                        if _same_pointer(remaining, native_callback):
                            raise CallbackControlError(
                                "OpenAL did not remove the buffer callback"
                            )
                self._buffer_callbacks.pop(buffer_id, None)

            registration = CallbackRegistration(
                native_callback,
                unregister,
                errors,
                owner_locks=owner_locks,
            )
            with self.activate():
                self.library.al.buffer_callback_soft(
                    buffer_id,
                    format_value,
                    frequency,
                    native_callback,
                    None,
                )
                installed = self.library.al.get_buffer_ptr_soft(
                    buffer_id,
                    _constants.AL_BUFFER_CALLBACK_FUNCTION_SOFT,
                )
                if not _same_pointer(installed, native_callback):
                    try:
                        self.library.al.buffer_data(
                            buffer_id,
                            format_value,
                            b"",
                            frequency,
                        )
                        remaining = self.library.al.get_buffer_ptr_soft(
                            buffer_id,
                            _constants.AL_BUFFER_CALLBACK_FUNCTION_SOFT,
                        )
                    except BaseException as error:
                        self._buffer_callbacks[buffer_id] = registration
                        raise CallbackControlError(
                            "OpenAL callback installation rollback failed; "
                            "the trampoline is retained until context close"
                        ) from error
                    if _same_pointer(remaining, native_callback):
                        self._buffer_callbacks[buffer_id] = registration
                        raise CallbackControlError(
                            "OpenAL did not remove a failed callback installation; "
                            "the trampoline is retained until context close"
                        )
                    raise CallbackControlError(
                        "OpenAL did not install the buffer callback"
                    )
            self._static_buffers.pop(buffer_id, None)
            self._buffer_callbacks[buffer_id] = registration
            return registration

    def start_foldback(
        self,
        mode: _enums.ALFoldbackMode | int,
        count: int,
        length: int,
        memory: object,
        callback: FoldbackCallback,
    ) -> FoldbackRegistration:
        """Start an owned ``AL_EXT_FOLDBACK`` request."""

        if not callable(callback):
            raise TypeError("callback must be callable")
        count = _positive_integer(count, label="count")
        if count < 2:
            raise ValueError("count must be at least two")
        length = _positive_integer(length, label="length")
        mode_value = _integer_value(mode, label="mode")
        if mode_value == _constants.AL_FOLDBACK_MODE_MONO:
            channel_count = 1
        elif mode_value == _constants.AL_FOLDBACK_MODE_STEREO:
            channel_count = 2
        else:
            raise ValueError("mode must be AL_FOLDBACK_MODE_MONO or STEREO")
        backing, capacity, resources = _retained_float_buffer(memory)
        required_capacity = count * length * channel_count
        if capacity < required_capacity:
            raise ValueError(
                f"foldback memory requires at least {required_capacity} ALfloat values"
            )
        owner_locks = (self.library._context_lock, self._lock)

        with self.library._context_lock, self._lock:
            self.require_extension("AL_EXT_FOLDBACK")
            if self._foldback is not None:
                self._foldback.close()
            errors: list[BaseException] = []

            def receive(event_type: int, block_index: int) -> None:
                registration._begin_callback()
                try:
                    callback(int(event_type), int(block_index))
                except BaseException as error:
                    registration._record_error(error)
                finally:
                    registration._end_callback()
                    if int(event_type) == _constants.AL_FOLDBACK_EVENT_STOP:
                        registration._native_stopped()

            native_callback = _types.LPALFOLDBACKCALLBACK(receive)

            def unregister(registration: CallbackRegistration) -> None:
                if self._foldback is not registration:
                    return
                with self.activate():
                    prior_error = self.library.al.get_error()
                    if int(prior_error) != _constants.AL_NO_ERROR:
                        prior_value = int(prior_error)
                        prior_name = getattr(
                            prior_error, "name", f"0x{prior_value:04x}"
                        )
                        registration._record_error(
                            NativeCallError(
                                "discarded pre-existing OpenAL error before "
                                f"foldback stop: {prior_name}"
                            )
                        )
                    self.library.al.request_foldback_stop()
                    _require_no_al_error(self.library, "foldback stop")

            registration = FoldbackRegistration(
                native_callback,
                unregister,
                errors,
                backing,
                resources=resources,
                owner_locks=owner_locks,
            )
            with self.activate():
                _require_no_al_error(
                    self.library,
                    "foldback start",
                    preexisting=True,
                )
                self.library.al.request_foldback_start(
                    mode_value,
                    count,
                    length,
                    backing,
                    native_callback,
                )
                _require_no_al_error(self.library, "foldback start")
            self._foldback = registration
            return registration

    def set_static_buffer_data(
        self,
        buffer: Buffer | int,
        format: _enums.ALFormat | int,
        data: bytes | bytearray | memoryview,
        frequency: int,
    ) -> None:
        """Set ``AL_EXT_STATIC_BUFFER`` data and retain its native backing."""

        buffer_id = _buffer_identifier(buffer, self)
        frequency = _positive_integer(frequency, label="frequency")
        format_value = _integer_value(format, label="format")
        backing, size, resources = _retained_byte_buffer(data)

        with self.library._context_lock, self._lock:
            self.require_extension("AL_EXT_STATIC_BUFFER")
            previous = self._buffer_callbacks.get(buffer_id)
            if previous is not None:
                previous.close()
            with self.activate():
                _require_no_al_error(
                    self.library,
                    "static buffer update",
                    preexisting=True,
                )
                function = self.library.get_function("alBufferDataStatic")
                function(buffer_id, format_value, backing, size, frequency)
                _require_no_al_error(self.library, "static buffer update")
            self._static_buffers[buffer_id] = (backing, *resources)

    def close(self) -> None:
        """Stop foldback, detach, and destroy the native context."""

        registrations: tuple[CallbackRegistration, ...]
        with self.library._context_lock, self._lock:
            if self._handle is None:
                return
            if self._foldback is not None:
                self._foldback.close()
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
            self.library._invalidate_context_extensions(handle)
            registrations = (
                *self._buffer_callbacks.values(),
                *self._callbacks.values(),
            )
            if self._foldback is not None:
                registrations = (*registrations, self._foldback)
            self._buffer_callbacks.clear()
            self._callbacks.clear()
            self._foldback = None
            self._static_buffers.clear()
            self._handle = None
            self.device._forget_context(self)

        for registration in registrations:
            registration._owner_closed()

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


_SYSTEM_EVENT_LOCK = threading.RLock()
_SYSTEM_EVENT_CALLBACKS: dict[tuple[str, int], CallbackRegistration] = {}


def _system_event_key(library: OpenALLibrary) -> tuple[str, int]:
    native_library = getattr(library, "native_library", None)
    native_handle = getattr(native_library, "_handle", None)
    if isinstance(native_handle, int):
        return ("native", native_handle)
    return ("library", id(library))


def _register_system_event_callback(
    library: OpenALLibrary,
    callback: SystemEventCallback,
    *,
    event_types: Sequence[int] = (),
) -> CallbackRegistration:
    """Implement library-owned ``ALC_SOFT_system_events`` registration."""

    if not callable(callback):
        raise TypeError("callback must be callable")
    enabled_types = tuple(
        _integer_value(item, label="event type") for item in event_types
    )
    event_key = _system_event_key(library)
    owner_locks = (_SYSTEM_EVENT_LOCK, library._context_lock)

    with _SYSTEM_EVENT_LOCK, library._context_lock:
        library.extensions["ALC_SOFT_system_events"].require()
        previous = _SYSTEM_EVENT_CALLBACKS.get(event_key)
        if previous is not None:
            previous.close()
        errors: list[BaseException] = []

        def receive(
            event_type: int,
            device_type: int,
            device: object | None,
            length: int,
            message: bytes | None,
            _user_parameter: object | None,
        ) -> None:
            registration._begin_callback()
            try:
                callback_device = None if _pointer_address(device) is None else device
                callback(
                    int(event_type),
                    int(device_type),
                    callback_device,
                    _message_text(message, int(length)),
                )
            except BaseException as error:
                registration._record_error(error)
            finally:
                registration._end_callback()

        native_callback = _types.ALCEVENTPROCTYPESOFT(receive)

        def unregister(registration: CallbackRegistration) -> None:
            with _SYSTEM_EVENT_LOCK:
                if _SYSTEM_EVENT_CALLBACKS.get(event_key) is not registration:
                    return
                if enabled_types and not library.alc.event_control_soft(
                    enabled_types,
                    False,
                ):
                    registration._record_error(
                        CallbackControlError(
                            "OpenAL could not disable one or more system event types"
                        )
                    )
                library.alc.event_callback_soft(
                    _types.ALCEVENTPROCTYPESOFT(),
                    None,
                )
                _SYSTEM_EVENT_CALLBACKS.pop(event_key, None)
                if library._system_event_callback is registration:
                    library._system_event_callback = None

        registration = CallbackRegistration(
            native_callback,
            unregister,
            errors,
            owner_locks=owner_locks,
        )
        try:
            library.alc.event_callback_soft(native_callback, None)
            if enabled_types and not library.alc.event_control_soft(
                enabled_types,
                True,
            ):
                raise CallbackControlError(
                    "OpenAL could not enable one or more system event types"
                )
        except BaseException:
            if enabled_types:
                with suppress(BaseException):
                    library.alc.event_control_soft(enabled_types, False)
            with suppress(BaseException):
                library.alc.event_callback_soft(
                    _types.ALCEVENTPROCTYPESOFT(),
                    None,
                )
            raise
        library._system_event_callback = registration
        _SYSTEM_EVENT_CALLBACKS[event_key] = registration
        return registration


def _clear_system_event_callback(library: OpenALLibrary) -> None:
    """Clear the global system-event callback for a loaded native library."""

    event_key = _system_event_key(library)
    with _SYSTEM_EVENT_LOCK, library._context_lock:
        registration = _SYSTEM_EVENT_CALLBACKS.get(event_key)
        if registration is not None:
            registration.close()
        elif library._system_event_callback is not None:
            library._system_event_callback = None


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
    "BufferCallback",
    "CallbackControlError",
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
    "FoldbackCallback",
    "FoldbackRegistration",
    "HandleClosedError",
    "LoopbackDevice",
    "NativeCallError",
    "PlaybackDevice",
    "SystemEventCallback",
    "open_capture_device",
    "open_device",
    "open_loopback_device",
]
