"""Owned playback, loopback, and capture device handles."""

from __future__ import annotations

import threading
from collections.abc import Sequence
from contextlib import suppress
from typing import TYPE_CHECKING, Self

from pyalsoft.bindings._alc.errors import (
    ContextCreateError,
    DeviceCloseError,
    DeviceOpenError,
    HandleClosedError,
)
from pyalsoft.bindings._alc.native import _enum_or_int
from pyalsoft.bindings._generated import constants as _constants
from pyalsoft.bindings._generated import enums as _enums
from pyalsoft.bindings._library import (
    LibraryNotFoundError,
    LibraryPath,
    OpenALLibrary,
    _pointer_address,
    load,
)

if TYPE_CHECKING:
    from pyalsoft.bindings._alc.context import Context


class Device:
    """An owned ALC device handle.

    Do not construct instances directly. Use the more specific ``PlaybackDevice``,
    ``LoopbackDevice``, or ``CaptureDevice`` returned by the module-level open
    helpers. Closing a playback device first closes every context created through
    it. Context-manager exit calls ``close``.

    Extension-backed properties raise ``ExtensionUnavailableError`` when the
    device does not expose their named extension. Access requiring a native handle
    raises ``HandleClosedError`` after closure. Query helpers forward directly to
    generated commands; callers remain responsible for the native ALC error state.

    Attributes:
        library: Loaded OpenAL library that owns the command wrappers.
        closed: Whether the native device has been closed.
        handle: Native ALC device pointer for generated raw calls.
        name: Implementation-provided device specifier, when available.
        extensions: Extension names reported for this device.
        version: Reported ALC major and minor version.
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
        """The underlying ALC device pointer for raw generated calls.

        Raises:
            HandleClosedError: This device has been closed.
        """

        if self._handle is None:
            raise HandleClosedError("ALC device is closed")
        return self._handle

    def require_extension(self, name: str) -> None:
        """Require an ALC extension on this device.

        Args:
            name: Registry extension name.

        Raises:
            KeyError: ``name`` is not a known registry extension.
            HandleClosedError: This device is closed.
            ExtensionUnavailableError: The device does not report ``name``.
        """

        self.library.extensions[name].require(self.handle)

    def is_extension_present(self, name: str) -> bool:
        """Return whether an ALC extension is present on this device.

        Args:
            name: ASCII registry extension name.

        Returns:
            Whether the device reports the extension.

        Raises:
            HandleClosedError: This device is closed.
            ValueError: ``name`` contains non-ASCII characters.
        """

        return self.library.is_alc_extension_present(name, self.handle)

    def get_string(self, parameter: _enums.ALCContextString | int) -> str | None:
        """Query one device string through ``alcGetString``.

        Args:
            parameter: ALC string selector.

        Returns:
            Decoded implementation string, or ``None`` for a null result.

        Raises:
            HandleClosedError: This device is closed.
        """

        return self.library.alc.get_string(self.handle, parameter)

    def get_integers(
        self,
        parameter: _enums.ALCContextInteger | int,
        count: int = 1,
    ) -> tuple[int, ...]:
        """Query one or more device integers through ``alcGetIntegerv``.

        Args:
            parameter: ALC integer selector.
            count: Positive number of integers to return.

        Returns:
            Exactly ``count`` integer values.

        Raises:
            TypeError: ``count`` is not an integer.
            ValueError: ``count`` is less than one.
            HandleClosedError: This device is closed.
        """

        if isinstance(count, bool) or not isinstance(count, int):
            raise TypeError("count must be an integer")
        if count < 1:
            raise ValueError("count must be at least one")
        return self.library.alc.get_integerv(self.handle, parameter, count)

    def get_integer(self, parameter: _enums.ALCContextInteger | int) -> int:
        """Query one device integer through ``alcGetIntegerv``.

        Args:
            parameter: ALC integer selector.

        Returns:
            The queried integer value.

        Raises:
            HandleClosedError: This device is closed.
        """

        return self.get_integers(parameter)[0]

    def get_integer64s(self, parameter: int, count: int = 1) -> tuple[int, ...]:
        """Query device clock values through ``ALC_SOFT_device_clock``.

        Args:
            parameter: Extension integer selector.
            count: Positive number of 64-bit integers to return.

        Returns:
            Exactly ``count`` integer values.

        Raises:
            TypeError: ``count`` is not an integer.
            ValueError: ``count`` is less than one.
            HandleClosedError: This device is closed.
            ExtensionUnavailableError: ``ALC_SOFT_device_clock`` is unavailable.
        """

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
        """Return one available HRTF specifier by index.

        Args:
            index: Non-negative index less than ``hrtf_specifier_count``.

        Returns:
            Implementation-provided HRTF name, or ``None`` for a null result.

        Raises:
            TypeError: ``index`` is not an integer.
            ValueError: ``index`` is negative.
            HandleClosedError: This device is closed.
            ExtensionUnavailableError: ``ALC_SOFT_HRTF`` is unavailable.
        """

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
        """Close owned contexts and then the native device.

        Contexts are closed in reverse creation order. Calling this again after a
        successful close is harmless. Exceptions raised while closing an owned
        context propagate and leave the device open.

        Raises:
            DeviceCloseError: OpenAL refuses to close the native device.
        """

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
    """An owned playback device that can create and own AL contexts."""

    def create_context(self, attributes: Sequence[int] | None = None) -> Context:
        """Create an owned context attached to this device.

        Args:
            attributes: Flat ALC attribute/value sequence terminated by the
                generated wrapper. ``None`` requests backend defaults.

        Returns:
            Open context owned by this device.

        Raises:
            HandleClosedError: This device is closed.
            ContextCreateError: OpenAL cannot create the requested context.
        """

        from pyalsoft.bindings._alc.context import Context

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
    """An ``ALC_SOFT_loopback`` device for deterministic offline rendering.

    Create a context with explicit loopback format attributes, start AL sources,
    then render frames into caller-owned writable storage with ``render_samples``.
    """

    def is_render_format_supported(
        self,
        frequency: int,
        channels: _enums.ALCRenderFormatChannelSOFT | int,
        sample_type: _enums.ALCRenderFormatTypeSOFT | int,
    ) -> bool:
        """Return whether a loopback render format is supported.

        Args:
            frequency: Requested sample rate in frames per second.
            channels: ``ALC_SOFT_loopback`` channel-layout value.
            sample_type: ``ALC_SOFT_loopback`` sample representation.

        Returns:
            Whether the device accepts this exact render format.

        Raises:
            HandleClosedError: This device is closed.
        """

        return self.library.alc.is_render_format_supported_soft(
            self.handle,
            frequency,
            channels,
            sample_type,
        )

    def render_samples(self, buffer: object, samples: int) -> None:
        """Render frames into caller-owned writable storage.

        The caller must size ``buffer`` for ``samples`` complete frames in the
        format selected when creating the active loopback context.

        Args:
            buffer: Writable buffer accepted by the generated command wrapper.
            samples: Non-negative number of sample frames to render.

        Raises:
            TypeError: ``samples`` is not an integer or ``buffer`` is incompatible.
            ValueError: ``samples`` is negative.
            HandleClosedError: This device is closed.
        """

        if isinstance(samples, bool) or not isinstance(samples, int):
            raise TypeError("samples must be an integer")
        if samples < 0:
            raise ValueError("samples cannot be negative")
        self.library.alc.render_samples_soft(self.handle, buffer, samples)


class CaptureDevice(Device):
    """An owned input device opened through the core ALC capture API.

    Do not construct instances directly. Use ``open_capture_device``. The handle
    remembers the requested format for callers but does not convert captured
    samples.

    Attributes:
        library: Loaded OpenAL library used by this device.
        frequency: Capture sample rate in frames per second.
        format: OpenAL sample-format value requested at open time.
        available_samples: Number of complete frames currently ready to read.
        name: Implementation-provided capture device specifier.
        capturing: Whether ``start`` has been called without a matching ``stop``.
        closed: Whether the native capture device has been closed.
        handle: Native ALC capture-device pointer for generated raw calls.
    """

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
        """Start input capture.

        Calling this while capture is already started is harmless.

        Raises:
            HandleClosedError: This capture device is closed.
        """

        if self._capturing:
            return
        self.library.alc.capture_start(self.handle)
        self._capturing = True

    def stop(self) -> None:
        """Stop input capture while preserving buffered samples.

        Calling this while capture is not started is harmless.

        """

        if not self._capturing:
            return
        self.library.alc.capture_stop(self.handle)
        self._capturing = False

    def read_samples(self, buffer: object, samples: int) -> None:
        """Read capture frames into caller-owned writable storage.

        The caller must provide storage for ``samples`` complete frames in this
        device's ``format`` and should not request more than ``available_samples``.

        Args:
            buffer: Writable buffer accepted by the generated command wrapper.
            samples: Non-negative number of sample frames to read.

        Raises:
            TypeError: ``samples`` is not an integer or ``buffer`` is incompatible.
            ValueError: ``samples`` is negative.
            HandleClosedError: This capture device is closed.
        """

        if isinstance(samples, bool) or not isinstance(samples, int):
            raise TypeError("samples must be an integer")
        if samples < 0:
            raise ValueError("samples cannot be negative")
        self.library.alc.capture_samples(self.handle, buffer, samples)

    def close(self) -> None:
        """Stop capture and close the native capture device.

        Calling this again after a successful close is harmless.

        Raises:
            DeviceCloseError: OpenAL refuses to close the native capture device.
        """

        if not self.closed:
            self.stop()
        super().close()

    def _close_native(self, handle: object) -> bool:
        return self.library.alc.capture_close_device(handle)


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
    """Open an owned playback device.

    Args:
        name: Device specifier as text or encoded bytes. ``None`` selects the
            implementation's default playback device.
        library: Existing loaded library to use.
        path: Shared-library path to load when ``library`` is omitted.

    Returns:
        Open playback device that owns contexts created through it.

    Raises:
        TypeError: ``name`` or ``path`` has an unsupported type.
        ValueError: ``library`` and ``path`` are both supplied.
        DeviceOpenError: OpenAL cannot be loaded or the requested device cannot
            be opened.
    """

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
    """Open an owned ``ALC_SOFT_loopback`` device.

    Args:
        name: Device specifier as text or encoded bytes. ``None`` selects the
            implementation's default loopback device.
        library: Existing loaded library to use.
        path: Shared-library path to load when ``library`` is omitted.

    Returns:
        Open loopback device for context creation and offline rendering.

    Raises:
        TypeError: ``name`` or ``path`` has an unsupported type.
        ValueError: ``library`` and ``path`` are both supplied.
        DeviceOpenError: OpenAL cannot be loaded or a loopback device cannot open.
        ExtensionUnavailableError: ``ALC_SOFT_loopback`` is unavailable.
    """

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
    """Open an owned core ALC capture device.

    ``buffer_size`` controls the native capture ring capacity; it is measured in
    sample frames, not bytes.

    Args:
        frequency: Positive capture sample rate in frames per second.
        format: Core OpenAL mono or stereo sample-format value.
        buffer_size: Positive native capture-buffer capacity in sample frames.
        name: Device specifier as text or encoded bytes. ``None`` selects the
            implementation's default capture device.
        library: Existing loaded library to use.
        path: Shared-library path to load when ``library`` is omitted.

    Returns:
        Open capture device with explicit start, stop, and read operations.

    Raises:
        TypeError: A name, path, format, frequency, or buffer size has an
            unsupported type.
        ValueError: A size is non-positive or ``library`` and ``path`` are both
            supplied.
        DeviceOpenError: OpenAL cannot be loaded or the requested capture device
            cannot be opened.
    """

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
