"""Owned OpenAL context state, activation, and context-scoped resources."""

from __future__ import annotations

import threading
from collections.abc import Callable, Iterator, Sequence
from contextlib import contextmanager, suppress
from typing import TYPE_CHECKING, Self, cast

from pyalsoft.bindings._alc.callbacks import (
    BufferCallback,
    CallbackRegistration,
    DebugCallback,
    EventCallback,
    FoldbackCallback,
    FoldbackRegistration,
    _callback_buffer,
    _message_text,
    _retained_byte_buffer,
    _retained_float_buffer,
)
from pyalsoft.bindings._alc.devices import PlaybackDevice
from pyalsoft.bindings._alc.errors import (
    CallbackControlError,
    ContextActivationError,
    HandleClosedError,
    NativeCallError,
)
from pyalsoft.bindings._alc.native import (
    _enum_or_int,
    _integer_value,
    _positive_integer,
    _require_no_al_error,
    _same_pointer,
)
from pyalsoft.bindings._generated import constants as _constants
from pyalsoft.bindings._generated import enums as _enums
from pyalsoft.bindings._generated import types as _types
from pyalsoft.bindings._generated.objects import Buffer

if TYPE_CHECKING:
    from pyalsoft.bindings._generated.objects import (
        AuxiliaryEffectSlot,
        Effect,
        Filter,
        Listener,
        Source,
    )


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


class Context:
    """An owned AL context attached to a playback device.

    Do not construct instances directly. Use ``PlaybackDevice.create_context``.
    The context owns callback registrations and retained static-buffer storage;
    closing it destroys that native state and invalidates every typed AL object
    bound to it. Context-manager exit calls ``close``.

    Operations that require this context temporarily activate it while holding
    the loaded library's context lock, then restore the previous context. Ordinary
    state properties forward directly to generated commands; callers remain
    responsible for querying and clearing the native AL error state.

    Attributes:
        device: Playback or loopback device that owns this context.
        library: Loaded OpenAL library shared with ``device``.
        closed: Whether the native context has been destroyed.
        handle: Native ALC context pointer for generated raw calls.
        current: Whether this is the process-wide current context.
        listener: Typed context-scoped listener singleton.
    """

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
        """The underlying ALC context pointer for raw generated calls.

        Raises:
            HandleClosedError: This context has been destroyed.
        """

        if self._handle is None:
            raise HandleClosedError("ALC context is closed")
        return self._handle

    @property
    def current(self) -> bool:
        """Whether this is the process-wide current context.

        This does not inspect the ``ALC_EXT_thread_local_context`` override.

        Raises:
            HandleClosedError: This context has been destroyed.
        """

        with self.library._context_lock:
            return _same_pointer(
                self.library.alc.get_current_context(),
                self.handle,
            )

    def make_current(self) -> None:
        """Make this the process-wide current context.

        The prior context is not restored automatically. Use ``activate`` for a
        temporary change.

        Raises:
            HandleClosedError: This context has been destroyed.
            ContextActivationError: OpenAL refuses the context change.
        """

        with self.library._context_lock:
            if not self.library.alc.make_context_current(self.handle):
                raise ContextActivationError(
                    "OpenAL could not make the context current"
                )

    def make_thread_current(self) -> None:
        """Make this current only for the calling thread.

        The prior thread-local context is not restored automatically. Use
        ``activate(thread_local=True)`` for a temporary change.

        Raises:
            HandleClosedError: This context or its device is closed.
            ExtensionUnavailableError: ``ALC_EXT_thread_local_context`` is absent.
            ContextActivationError: OpenAL refuses the context change.
        """

        with self.library._context_lock:
            self.device.require_extension("ALC_EXT_thread_local_context")
            if not self.library.alc.set_thread_context(self.handle):
                raise ContextActivationError(
                    "OpenAL could not make the context current for this thread"
                )

    def require_extension(self, name: str) -> None:
        """Require an AL extension while this context is temporarily current.

        Args:
            name: Registry extension name.

        Raises:
            HandleClosedError: This context is closed.
            ExtensionUnavailableError: The context does not report ``name``.
            ContextActivationError: The context cannot be activated or restored.
        """

        with self.activate():
            self.library.extensions[name].require()

    @contextmanager
    def activate(self, *, thread_local: bool = False) -> Iterator[Context]:
        """Temporarily make this context current and restore the prior context.

        Activation is serialized across contexts that share this loaded library.
        Nested activation of the same context is supported.

        Args:
            thread_local: Use ``ALC_EXT_thread_local_context`` instead of the
                process-wide current-context API.

        Yields:
            This context while it is current in the requested scope.

        Raises:
            HandleClosedError: This context or its device is closed.
            ExtensionUnavailableError: Thread-local activation was requested but
                the device does not expose the extension.
            ContextActivationError: OpenAL cannot activate or restore a context.
        """

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
        """Wrap an existing integer source identifier for this context.

        This does not allocate a source or verify that ``identifier`` is live.

        Args:
            identifier: Non-negative OpenAL source name.

        Returns:
            Context-affine typed source.

        Raises:
            TypeError: ``identifier`` is not an integer.
            ValueError: ``identifier`` is negative.
            HandleClosedError: This context is closed.
        """

        from pyalsoft.bindings._generated.objects import Source

        return Source(self, identifier)

    def buffer(self, identifier: int) -> Buffer:
        """Wrap an existing integer buffer identifier for this context.

        This does not allocate a buffer or verify that ``identifier`` is live.

        Args:
            identifier: Non-negative OpenAL buffer name.

        Returns:
            Context-affine typed buffer.

        Raises:
            TypeError: ``identifier`` is not an integer.
            ValueError: ``identifier`` is negative.
            HandleClosedError: This context is closed.
        """

        return Buffer(self, identifier)

    def effect(self, identifier: int) -> Effect:
        """Wrap an existing integer EFX effect identifier for this context.

        This does not allocate an effect or verify that ``identifier`` is live.

        Args:
            identifier: Non-negative OpenAL effect name.

        Returns:
            Context-affine typed effect.

        Raises:
            TypeError: ``identifier`` is not an integer.
            ValueError: ``identifier`` is negative.
            HandleClosedError: This context is closed.
        """

        from pyalsoft.bindings._generated.objects import Effect

        return Effect(self, identifier)

    def filter(self, identifier: int) -> Filter:
        """Wrap an existing integer EFX filter identifier for this context.

        This does not allocate a filter or verify that ``identifier`` is live.

        Args:
            identifier: Non-negative OpenAL filter name.

        Returns:
            Context-affine typed filter.

        Raises:
            TypeError: ``identifier`` is not an integer.
            ValueError: ``identifier`` is negative.
            HandleClosedError: This context is closed.
        """

        from pyalsoft.bindings._generated.objects import Filter

        return Filter(self, identifier)

    def auxiliary_effect_slot(self, identifier: int) -> AuxiliaryEffectSlot:
        """Wrap an existing EFX auxiliary-slot identifier for this context.

        This does not allocate a slot or verify that ``identifier`` is live.

        Args:
            identifier: Non-negative OpenAL auxiliary effect slot name.

        Returns:
            Context-affine typed auxiliary effect slot.

        Raises:
            TypeError: ``identifier`` is not an integer.
            ValueError: ``identifier`` is negative.
            HandleClosedError: This context is closed.
        """

        from pyalsoft.bindings._generated.objects import AuxiliaryEffectSlot

        return AuxiliaryEffectSlot(self, identifier)

    @property
    def listener(self) -> Listener:
        """Return the typed listener singleton bound to this context.

        Raises:
            HandleClosedError: This context is closed.
        """

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
        """The current AL implementation vendor, or ``None`` on native failure."""

        return self._get_string(_constants.AL_VENDOR)

    @property
    def version(self) -> str | None:
        """The current AL implementation version, or ``None`` on native failure."""

        return self._get_string(_constants.AL_VERSION)

    @property
    def renderer(self) -> str | None:
        """The current AL renderer name, or ``None`` on native failure."""

        return self._get_string(_constants.AL_RENDERER)

    @property
    def extensions(self) -> frozenset[str]:
        """Extensions reported for this AL context."""

        value = self._get_string(_constants.AL_EXTENSIONS)
        return frozenset(value.split()) if value else frozenset()

    @property
    def doppler_factor(self) -> float:
        """Get or set the global Doppler scale.

        OpenAL defines non-negative values and uses 1.0 by default. The low-level
        setter forwards the value without consuming the native AL error state.
        """

        return self._get_float(_constants.AL_DOPPLER_FACTOR)

    @doppler_factor.setter
    def doppler_factor(self, value: float) -> None:
        self._set_float("doppler_factor", value)

    @property
    def doppler_velocity(self) -> float:
        """Get or set the legacy Doppler reference velocity.

        This OpenAL 1.0 control is retained for compatibility; prefer
        ``speed_of_sound`` for OpenAL 1.1 behavior.
        """

        return self._get_float(_constants.AL_DOPPLER_VELOCITY)

    @doppler_velocity.setter
    def doppler_velocity(self, value: float) -> None:
        self._set_float("doppler_velocity", value)

    @property
    def speed_of_sound(self) -> float:
        """Get or set propagation speed for Doppler calculations.

        Values are in world-units per second. OpenAL requires at least 0.0001 and
        uses 343.3 by default.
        """

        return self._get_float(_constants.AL_SPEED_OF_SOUND)

    @speed_of_sound.setter
    def speed_of_sound(self, value: float) -> None:
        self._set_float("speed_of_sound", value)

    @property
    def distance_model(self) -> _enums.ALDistanceModel | int:
        """Get or set the global distance-attenuation model.

        Unknown future values are returned as integers.
        """

        with self.activate():
            value = self.library.al.get_integer(_constants.AL_DISTANCE_MODEL)
        return _enum_or_int(_enums.ALDistanceModel, value)

    @distance_model.setter
    def distance_model(self, value: _enums.ALDistanceModel | int) -> None:
        with self.activate():
            self.library.al.distance_model(value)

    @property
    def default_filter_order(self) -> int:
        """The default resampler filter order for this context.

        Raises:
            ExtensionUnavailableError: ``ALC_EXT_DEFAULT_FILTER_ORDER`` is absent.
            HandleClosedError: This context or its device is closed.
        """

        self.device.require_extension("ALC_EXT_DEFAULT_FILTER_ORDER")
        with self.activate():
            return self.library.al.get_integer(_constants.ALC_DEFAULT_FILTER_ORDER)

    def register_event_callback(
        self,
        callback: EventCallback,
        *,
        event_types: Sequence[int] = (),
    ) -> CallbackRegistration:
        """Register and retain an ``AL_SOFT_events`` callback.

        The callback receives ``(event_type, object_id, parameter, message)``.
        Registering another event callback on this context closes the previous
        registration. Python exceptions are retained instead of crossing the C
        boundary.

        Args:
            callback: Function invoked for enabled AL events.
            event_types: Registry event-type values to enable. An empty sequence
                installs the callback without explicitly enabling event types.

        Returns:
            Owned registration that keeps the native trampoline alive.

        Raises:
            TypeError: ``callback`` is not callable or an event type is not integer-like.
            HandleClosedError: This context is closed.
            ExtensionUnavailableError: ``AL_SOFT_events`` is unavailable.
            CallbackControlError: The previous callback cannot be removed safely.
            ContextActivationError: This context cannot be activated or restored.
        """

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
        """Register and retain an ``AL_EXT_debug`` message callback.

        The callback receives ``(source, type, identifier, severity, message)``.
        Registering another debug callback on this context closes the previous
        registration. Python exceptions are retained by the registration.

        Args:
            callback: Function invoked for native debug messages.
            enable_output: Enable ``AL_DEBUG_OUTPUT_EXT`` for the registration and
                restore its previous disabled state when the registration closes.

        Returns:
            Owned registration that keeps the native trampoline alive.

        Raises:
            TypeError: ``callback`` is not callable.
            HandleClosedError: This context is closed.
            ExtensionUnavailableError: ``AL_EXT_debug`` is unavailable.
            CallbackControlError: The previous callback cannot be removed safely.
            ContextActivationError: This context cannot be activated or restored.
        """

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
        invocation and returns the number of bytes written. Exceptions and invalid
        byte counts are retained and reported to OpenAL as zero bytes. Python
        callback execution is not guaranteed to satisfy hard real-time constraints.

        Registering again for the same buffer closes the prior registration.
        Successful installation replaces retained static-buffer storage. Closing
        can fail while the buffer remains attached to a source; in that case the
        registration retains its native trampoline until cleanup can be retried or
        the context closes.

        Args:
            buffer: Positive integer buffer name or typed buffer from this context.
            format: OpenAL sample-format value produced by the callback.
            frequency: Positive sample rate in frames per second.
            callback: Function that fills the temporary writable view and returns
                a byte count from zero through the view length.

        Returns:
            Owned registration that keeps the native trampoline alive.

        Raises:
            TypeError: A value has the wrong type or ``callback`` is not callable.
            ValueError: The buffer belongs to another context or an integer value
                is outside its supported range.
            HandleClosedError: This context is closed.
            ExtensionUnavailableError: ``AL_SOFT_callback_buffer`` is unavailable.
            CallbackControlError: Native callback installation, replacement, or
                rollback cannot be completed safely.
            ContextActivationError: This context cannot be activated or restored.
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
        """Start an owned ``AL_EXT_FOLDBACK`` request.

        The callback receives ``(event_type, block_index)``. Starting another
        request closes the prior foldback registration. The returned registration
        retains both the native trampoline and the exact writable sample backing.

        Args:
            mode: ``AL_FOLDBACK_MODE_MONO`` or ``AL_FOLDBACK_MODE_STEREO``.
            count: Number of sample blocks; at least two.
            length: Positive number of frames in each block.
            memory: Writable ``ALfloat`` ctypes array, writable contiguous byte
                buffer, or numeric sequence. Sequences are copied to retained
                native storage.
            callback: Function invoked for foldback start, block, and stop events.

        Returns:
            Owned foldback registration exposing the retained sample memory.

        Raises:
            TypeError: A value has the wrong type, storage is not writable and
                contiguous, or ``callback`` is not callable.
            ValueError: The mode, dimensions, or storage capacity is invalid.
            HandleClosedError: This context is closed.
            ExtensionUnavailableError: ``AL_EXT_FOLDBACK`` is unavailable.
            CallbackControlError: A previous foldback request cannot close safely.
            NativeCallError: OpenAL reports an error before or during startup.
            ContextActivationError: This context cannot be activated or restored.
        """

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
        """Set ``AL_EXT_STATIC_BUFFER`` data and retain its native backing.

        Writable ``bytearray`` and ``memoryview`` inputs are borrowed without a
        copy and must not be resized while retained. Immutable or read-only input
        is copied into native storage. The backing remains alive until this buffer
        is updated again or the context closes. An active callback on the buffer is
        closed before static storage is installed.

        Args:
            buffer: Positive integer buffer name or typed buffer from this context.
            format: OpenAL sample-format value.
            data: Contiguous PCM bytes to retain or copy.
            frequency: Positive sample rate in frames per second.

        Raises:
            TypeError: A value has the wrong type or ``data`` is not contiguous.
            ValueError: The buffer belongs to another context or an integer value
                is outside its supported range.
            HandleClosedError: This context is closed.
            ExtensionUnavailableError: ``AL_EXT_STATIC_BUFFER`` is unavailable.
            CallbackControlError: An existing buffer callback cannot be removed.
            NativeCallError: OpenAL has a pre-existing error or rejects the update.
            ContextActivationError: This context cannot be activated or restored.
        """

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
        """Stop foldback, detach, and destroy the native context.

        Closing an already closed context is harmless. Active callbacks are marked
        closed after native context destruction, and retained static-buffer storage
        is released. All typed objects bound to the context become unusable.

        Raises:
            CallbackControlError: Foldback or callback cleanup cannot complete.
            NativeCallError: OpenAL rejects an active foldback stop request.
            ContextActivationError: The context cannot be detached before
                destruction.
        """

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
