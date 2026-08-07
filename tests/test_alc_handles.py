"""Tests for owned backend device, context, and callback handles."""

from __future__ import annotations

import ctypes
import threading
from collections.abc import Callable, Sequence
from typing import Any, cast

import pytest

from pyalsoft import bindings
from pyalsoft.bindings._alc import _register_system_event_callback
from pyalsoft.bindings._generated import types as native_types


def _device_pointer() -> object:
    return ctypes.pointer(native_types.ALCdevice())


def _context_pointer() -> object:
    return ctypes.pointer(native_types.ALCcontext())


class FakeExtension:
    def __init__(self, name: str, calls: list[tuple[str, object | None]]) -> None:
        self.name = name
        self.calls = calls

    def require(self, device: object | None = None) -> None:
        self.calls.append((self.name, device))


class FakeExtensions:
    def __init__(self) -> None:
        self.calls: list[tuple[str, object | None]] = []

    def __getitem__(self, name: str) -> FakeExtension:
        return FakeExtension(name, self.calls)


class FakeAL:
    def __init__(self) -> None:
        self.strings = {
            bindings.AL_VENDOR: "PyALSoft Test Vendor",
            bindings.AL_VERSION: "1.1 test",
            bindings.AL_RENDERER: "Loopback Test Renderer",
            bindings.AL_EXTENSIONS: "AL_SOFT_events AL_EXT_debug",
        }
        self.floats = {
            bindings.AL_DOPPLER_FACTOR: 1.0,
            bindings.AL_DOPPLER_VELOCITY: 1.0,
            bindings.AL_SPEED_OF_SOUND: 343.3,
        }
        self.integers = {
            bindings.AL_DISTANCE_MODEL: bindings.AL_INVERSE_DISTANCE_CLAMPED,
            bindings.ALC_DEFAULT_FILTER_ORDER: 2,
        }
        self.event_callback: Callable[..., None] | None = None
        self.debug_callback: Callable[..., None] | None = None
        self.buffer_callbacks: dict[int, Callable[..., int]] = {}
        self.buffer_resets: list[tuple[int, int, int]] = []
        self.foldback_callback: Callable[..., None] | None = None
        self.foldback_calls: list[tuple[object, ...]] = []
        self.foldback_auto_stop = True
        self.foldback_stop_requested = threading.Event()
        self.static_calls: list[tuple[int, int, bytes, int]] = []
        self.static_error = bindings.AL_NO_ERROR
        self.event_controls: list[tuple[tuple[int, ...], bool]] = []
        self.enabled: set[int] = set()
        self.error = bindings.AL_NO_ERROR

    def get_error(self) -> int:
        error = self.error
        self.error = bindings.AL_NO_ERROR
        return error

    def get_string(self, parameter: int) -> str | None:
        return self.strings.get(parameter)

    def get_float(self, parameter: int) -> float:
        return self.floats[parameter]

    def get_integer(self, parameter: int) -> int:
        return self.integers[parameter]

    def doppler_factor(self, value: float) -> None:
        self.floats[bindings.AL_DOPPLER_FACTOR] = value

    def doppler_velocity(self, value: float) -> None:
        self.floats[bindings.AL_DOPPLER_VELOCITY] = value

    def speed_of_sound(self, value: float) -> None:
        self.floats[bindings.AL_SPEED_OF_SOUND] = value

    def distance_model(self, value: int) -> None:
        self.integers[bindings.AL_DISTANCE_MODEL] = int(value)

    def event_callback_soft(
        self,
        callback: Callable[..., None],
        _user_parameter: object | None,
    ) -> None:
        self.event_callback = callback if bool(callback) else None

    def event_control_soft(self, event_types: Sequence[int], enabled: bool) -> None:
        self.event_controls.append((tuple(event_types), enabled))

    def debug_message_callback_ext(
        self,
        callback: Callable[..., None],
        _user_parameter: object | None,
    ) -> None:
        self.debug_callback = callback if bool(callback) else None

    def is_enabled(self, capability: int) -> bool:
        return capability in self.enabled

    def enable(self, capability: int) -> None:
        self.enabled.add(capability)

    def disable(self, capability: int) -> None:
        self.enabled.discard(capability)

    def buffer_callback_soft(
        self,
        buffer: int,
        _format: int,
        _frequency: int,
        callback: Callable[..., int],
        _user_pointer: object | None,
    ) -> None:
        self.buffer_callbacks[buffer] = callback

    def get_buffer_ptr_soft(self, buffer: int, _parameter: int) -> object | None:
        return self.buffer_callbacks.get(buffer)

    def buffer_data(
        self,
        buffer: int,
        format: int,
        _data: bytes,
        frequency: int,
    ) -> None:
        self.buffer_callbacks.pop(buffer, None)
        self.buffer_resets.append((buffer, format, frequency))

    def request_foldback_start(
        self,
        mode: int,
        count: int,
        length: int,
        memory: object,
        callback: Callable[..., None],
    ) -> None:
        self.foldback_callback = callback
        self.foldback_calls.append(("start", mode, count, length, memory))

    def request_foldback_stop(self) -> None:
        self.foldback_calls.append(("stop",))
        self.foldback_stop_requested.set()
        callback = self.foldback_callback
        if self.foldback_auto_stop and callback is not None:
            callback(bindings.AL_FOLDBACK_EVENT_STOP, 0)
            self.foldback_callback = None

    def buffer_data_static(
        self,
        buffer: int,
        format: int,
        data: object,
        size: int,
        frequency: int,
    ) -> None:
        self.static_calls.append(
            (buffer, format, ctypes.string_at(cast(Any, data), size), frequency)
        )
        self.error = self.static_error


class FakeALC:
    def __init__(self) -> None:
        self.device = _device_pointer()
        self.context = _context_pointer()
        self.current: object | None = None
        self.thread_current: object | None = None
        self.destroyed: list[object] = []
        self.closed: list[object] = []
        self.capture_closed: list[object] = []
        self.capture_calls: list[tuple[str, object]] = []
        self.render_calls: list[tuple[object, object, int]] = []
        self.system_event_callback: Callable[..., None] | None = None
        self.system_event_removed = threading.Event()
        self.system_event_controls: list[tuple[tuple[int, ...], bool]] = []
        self.system_event_control_result = True
        self.strings = {
            bindings.ALC_DEVICE_SPECIFIER: "Test Device",
            bindings.ALC_CAPTURE_DEVICE_SPECIFIER: "Test Capture Device",
            bindings.ALC_EXTENSIONS: "ALC_SOFT_HRTF ALC_SOFT_device_clock",
            bindings.ALC_HRTF_SPECIFIER_SOFT: "Test HRTF",
        }
        self.integers = {
            bindings.ALC_MAJOR_VERSION: 1,
            bindings.ALC_MINOR_VERSION: 1,
            bindings.ALC_HRTF_SOFT: 1,
            bindings.ALC_HRTF_STATUS_SOFT: bindings.ALC_HRTF_ENABLED_SOFT,
            bindings.ALC_NUM_HRTF_SPECIFIERS_SOFT: 1,
            bindings.ALC_CAPTURE_SAMPLES: 12,
        }

    def open_device(self, _name: str | bytes | None) -> object:
        return self.device

    def close_device(self, device: object) -> bool:
        self.closed.append(device)
        return True

    def create_context(
        self,
        _device: object,
        _attributes: Sequence[int] | None,
    ) -> object:
        return self.context

    def destroy_context(self, context: object) -> None:
        self.destroyed.append(context)

    def get_current_context(self) -> object | None:
        return self.current

    def make_context_current(self, context: object | None) -> bool:
        self.current = context
        return True

    def get_thread_context(self) -> object | None:
        return self.thread_current

    def set_thread_context(self, context: object | None) -> bool:
        self.thread_current = context
        return True

    def get_string(self, _device: object, parameter: int) -> str | None:
        return self.strings.get(parameter)

    def get_integerv(
        self,
        _device: object,
        parameter: int,
        count: int,
    ) -> tuple[int, ...]:
        return (self.integers[parameter],) * count

    def get_stringi_soft(
        self,
        _device: object,
        _parameter: int,
        index: int,
    ) -> str:
        return f"HRTF {index}"

    def get_integer64v_soft(
        self,
        _device: object,
        parameter: int,
        count: int,
    ) -> tuple[int, ...]:
        if parameter == bindings.ALC_DEVICE_CLOCK_LATENCY_SOFT:
            assert count == 2
            return (1_000, 25)
        return (1_000,) * count

    def loopback_open_device_soft(self, _name: str | bytes | None) -> object:
        return self.device

    def is_render_format_supported_soft(
        self,
        _device: object,
        _frequency: int,
        _channels: int,
        _sample_type: int,
    ) -> bool:
        return True

    def render_samples_soft(
        self,
        device: object,
        buffer: object,
        samples: int,
    ) -> None:
        self.render_calls.append((device, buffer, samples))

    def capture_open_device(
        self,
        _name: str | bytes | None,
        _frequency: int,
        _format: int,
        _buffer_size: int,
    ) -> object:
        return self.device

    def capture_start(self, device: object) -> None:
        self.capture_calls.append(("start", device))

    def capture_stop(self, device: object) -> None:
        self.capture_calls.append(("stop", device))

    def capture_samples(
        self,
        device: object,
        _buffer: object,
        samples: int,
    ) -> None:
        self.capture_calls.append((f"read:{samples}", device))

    def capture_close_device(self, device: object) -> bool:
        self.capture_closed.append(device)
        return True

    def event_callback_soft(
        self,
        callback: Callable[..., None],
        _user_parameter: object | None,
    ) -> None:
        self.system_event_callback = callback if bool(callback) else None
        if bool(callback):
            self.system_event_removed.clear()
        else:
            self.system_event_removed.set()

    def event_control_soft(
        self,
        event_types: Sequence[int],
        enabled: bool,
    ) -> bool:
        self.system_event_controls.append((tuple(event_types), enabled))
        return self.system_event_control_result


class FakeLibrary:
    def __init__(self) -> None:
        self.al = FakeAL()
        self.alc = FakeALC()
        self.extensions = FakeExtensions()
        self.alc_extensions = {"ALC_EXT_thread_local_context"}
        self._context_lock = threading.RLock()
        self._system_event_callback: object | None = None
        self.invalidated_contexts: list[object] = []
        self.invalidated_devices: list[object] = []

    def is_alc_extension_present(
        self,
        extension: str,
        _device: object | None = None,
    ) -> bool:
        return extension in self.alc_extensions

    def register_system_event_callback(
        self,
        callback: bindings.SystemEventCallback,
        *,
        event_types: Sequence[int] = (),
    ) -> bindings.CallbackRegistration:
        return _register_system_event_callback(
            cast(bindings.OpenALLibrary, self),
            callback,
            event_types=event_types,
        )

    def clear_system_event_callback(self) -> None:
        from pyalsoft.bindings._alc import _clear_system_event_callback

        _clear_system_event_callback(cast(bindings.OpenALLibrary, self))

    def get_function(self, name: str) -> Callable[..., object]:
        assert name == "alBufferDataStatic"
        return self.al.buffer_data_static

    def _invalidate_context_extensions(self, context: object) -> None:
        self.invalidated_contexts.append(context)

    def _invalidate_device_extensions(self, device: object) -> None:
        self.invalidated_devices.append(device)


def _library() -> tuple[bindings.OpenALLibrary, FakeLibrary]:
    fake = FakeLibrary()
    return cast(bindings.OpenALLibrary, fake), fake


def test_owned_device_closes_its_context_before_the_device() -> None:
    library, fake = _library()
    device = bindings.open_device(library=library)
    context = device.create_context()
    context.make_current()

    device.close()

    assert context.closed
    assert device.closed
    assert fake.alc.current is None
    assert fake.alc.thread_current is None
    assert fake.alc.destroyed == [fake.alc.context]
    assert fake.alc.closed == [fake.alc.device]
    assert fake.invalidated_contexts == [fake.alc.context]
    assert fake.invalidated_devices == [fake.alc.device]
    with pytest.raises(bindings.HandleClosedError):
        _ = context.handle


def test_context_activation_restores_the_previous_context() -> None:
    library, fake = _library()
    previous = _context_pointer()
    fake.alc.current = previous
    with (
        bindings.open_device(library=library) as device,
        device.create_context() as context,
        context.activate(),
    ):
        assert fake.alc.current is fake.alc.context
        assert context.renderer == "Loopback Test Renderer"
        context.speed_of_sound = 300.0
    assert fake.alc.current is previous

    assert fake.al.floats[bindings.AL_SPEED_OF_SOUND] == 300.0


def test_device_and_context_expose_typed_backend_state() -> None:
    library, fake = _library()
    with bindings.open_device(library=library) as device:
        assert device.name == "Test Device"
        assert device.version == (1, 1)
        assert device.hrtf_enabled
        assert device.hrtf_status is bindings.enums.ALCHrtfStatusSOFT.HRTF_ENABLED_SOFT
        assert device.hrtf_name == "Test HRTF"
        assert device.get_hrtf_specifier(0) == "HRTF 0"
        assert device.clock_latency == (1_000, 25)

        with device.create_context() as context:
            assert context.vendor == "PyALSoft Test Vendor"
            assert (
                context.distance_model
                is bindings.enums.ALDistanceModel.INVERSE_DISTANCE_CLAMPED
            )
            assert context.default_filter_order == 2

    assert ("ALC_SOFT_HRTF", fake.alc.device) in fake.extensions.calls
    assert ("ALC_SOFT_device_clock", fake.alc.device) in fake.extensions.calls
    assert ("ALC_EXT_DEFAULT_FILTER_ORDER", fake.alc.device) in fake.extensions.calls


def test_event_registration_retains_callback_and_captures_errors() -> None:
    library, fake = _library()
    messages: list[str] = []
    with (
        bindings.open_device(library=library) as device,
        device.create_context() as context,
    ):

        def receive(
            _event_type: int,
            _object_id: int,
            _parameter: int,
            message: str,
        ) -> None:
            messages.append(message)
            raise ValueError("callback failed")

        registration = context.register_event_callback(
            receive,
            event_types=(bindings.AL_EVENT_TYPE_BUFFER_COMPLETED_SOFT,),
        )
        native_callback = fake.al.event_callback
        assert native_callback is not None
        native_callback(1, 2, 3, 5, b"hello", None)

        assert messages == ["hello"]
        assert len(registration.errors) == 1
        with pytest.raises(BaseExceptionGroup, match="OpenAL callback failed"):
            registration.raise_if_failed()
        assert not registration.errors
        registration.close()

    assert fake.al.event_callback is None
    assert fake.al.event_controls == [
        ((bindings.AL_EVENT_TYPE_BUFFER_COMPLETED_SOFT,), True),
        ((bindings.AL_EVENT_TYPE_BUFFER_COMPLETED_SOFT,), False),
    ]


def test_system_event_registration_is_library_owned_and_safe() -> None:
    library, fake = _library()
    messages: list[tuple[int, int, object | None, str]] = []

    def receive(
        event_type: int,
        device_type: int,
        device: object | None,
        message: str,
    ) -> None:
        messages.append((event_type, device_type, device, message))
        raise RuntimeError("system event failed")

    registration = library.register_system_event_callback(
        receive,
        event_types=(bindings.ALC_EVENT_TYPE_DEVICE_ADDED_SOFT,),
    )
    native_callback = fake.alc.system_event_callback
    assert native_callback is not None
    native_callback(1, 2, None, 6, b"device", None)

    assert messages == [(1, 2, None, "device")]
    assert len(registration.errors) == 1
    registration.close()

    assert fake.alc.system_event_callback is None
    assert fake.alc.system_event_controls == [
        ((bindings.ALC_EVENT_TYPE_DEVICE_ADDED_SOFT,), True),
        ((bindings.ALC_EVENT_TYPE_DEVICE_ADDED_SOFT,), False),
    ]
    with pytest.raises(BaseExceptionGroup, match="OpenAL callback failed"):
        registration.raise_if_failed()


def test_system_event_registration_rolls_back_failed_enable() -> None:
    library, fake = _library()
    fake.alc.system_event_control_result = False

    with pytest.raises(bindings.CallbackControlError, match="could not enable"):
        library.register_system_event_callback(
            lambda _event, _type, _device, _message: None,
            event_types=(bindings.ALC_EVENT_TYPE_DEVICE_REMOVED_SOFT,),
        )

    assert fake.alc.system_event_callback is None
    assert fake.alc.system_event_controls == [
        ((bindings.ALC_EVENT_TYPE_DEVICE_REMOVED_SOFT,), True),
        ((bindings.ALC_EVENT_TYPE_DEVICE_REMOVED_SOFT,), False),
    ]


def test_system_event_callback_can_be_cleared_without_saved_registration() -> None:
    library, fake = _library()
    registration = library.register_system_event_callback(
        lambda _event, _type, _device, _message: None,
        event_types=(bindings.ALC_EVENT_TYPE_DEVICE_ADDED_SOFT,),
    )

    library.clear_system_event_callback()

    assert registration.closed
    assert fake.alc.system_event_callback is None
    library.clear_system_event_callback()


def test_system_event_close_waits_for_an_inflight_callback() -> None:
    library, fake = _library()
    entered = threading.Event()
    release = threading.Event()

    def receive(
        _event: int,
        _type: int,
        _device: object | None,
        _message: str,
    ) -> None:
        entered.set()
        assert release.wait(5)

    registration = library.register_system_event_callback(receive)
    native_callback = fake.alc.system_event_callback
    assert native_callback is not None
    callback_thread = threading.Thread(
        target=native_callback,
        args=(1, 2, None, 0, None, None),
    )
    callback_thread.start()
    assert entered.wait(2)

    close_thread = threading.Thread(target=registration.close)
    close_thread.start()
    assert fake.alc.system_event_removed.wait(2)
    assert close_thread.is_alive()
    assert registration._callback is native_callback

    release.set()
    callback_thread.join(2)
    close_thread.join(2)
    assert not callback_thread.is_alive()
    assert not close_thread.is_alive()
    assert registration.closed


def test_callback_registration_rejects_self_close() -> None:
    library, fake = _library()
    registrations: list[bindings.CallbackRegistration] = []

    def receive(
        _event: int,
        _type: int,
        _device: object | None,
        _message: str,
    ) -> None:
        registrations[0].close()

    registration = library.register_system_event_callback(receive)
    registrations.append(registration)
    native_callback = fake.alc.system_event_callback
    assert native_callback is not None

    native_callback(1, 2, None, 0, None, None)

    assert not registration.closed
    assert len(registration.errors) == 1
    assert isinstance(registration.errors[0], bindings.CallbackControlError)
    registration.close()


def test_buffer_callback_owns_trampoline_and_contains_failures() -> None:
    library, fake = _library()
    with (
        bindings.open_device(library=library) as device,
        device.create_context() as context,
    ):
        calls = 0

        def fill(view: memoryview) -> int:
            nonlocal calls
            calls += 1
            if calls == 2:
                raise RuntimeError("render failed")
            view[:] = b"tone"
            return len(view)

        registration = context.register_buffer_callback(
            7,
            bindings.AL_FORMAT_MONO8,
            48_000,
            fill,
        )
        native_callback = fake.al.buffer_callbacks[7]
        output = ctypes.create_string_buffer(4)

        assert native_callback(None, ctypes.addressof(output), 4) == 4
        assert output.raw == b"tone"
        assert native_callback(None, ctypes.addressof(output), 4) == 0
        assert len(registration.errors) == 1

        registration.close()
        assert 7 not in fake.al.buffer_callbacks

    assert fake.al.buffer_resets == [(7, bindings.AL_FORMAT_MONO8, 48_000)]


def test_buffer_callback_close_does_not_overwrite_external_replacement() -> None:
    library, fake = _library()
    with (
        bindings.open_device(library=library) as device,
        device.create_context() as context,
    ):
        registration = context.register_buffer_callback(
            3,
            bindings.AL_FORMAT_MONO8,
            8_000,
            lambda view: len(view),
        )
        replacement = native_types.ALBUFFERCALLBACKTYPESOFT(
            lambda _user, _samples, _size: 0
        )
        fake.al.buffer_callbacks[3] = replacement

        registration.close()

        assert fake.al.buffer_callbacks[3] is replacement
        assert fake.al.buffer_resets == []


def test_context_close_finalizes_attached_buffer_callback_after_destroy() -> None:
    library, fake = _library()
    device = bindings.open_device(library=library)
    context = device.create_context()
    registration = context.register_buffer_callback(
        5,
        bindings.AL_FORMAT_MONO8,
        8_000,
        lambda view: len(view),
    )

    context.close()

    assert registration.closed
    assert fake.al.buffer_resets == []
    assert fake.alc.destroyed == [fake.alc.context]
    device.close()


def test_buffer_callback_requires_a_buffer_from_the_same_library() -> None:
    library, _fake = _library()
    other_library, _other_fake = _library()
    with (
        bindings.open_device(library=library) as device,
        device.create_context() as context,
    ):
        buffer = bindings.Buffer(library, 3)
        registration = context.register_buffer_callback(
            buffer,
            bindings.AL_FORMAT_MONO8,
            8_000,
            lambda view: len(view),
        )
        registration.close()

        with pytest.raises(TypeError, match="integer or Buffer"):
            context.register_buffer_callback(
                cast(Any, bindings.Source(library, 4)),
                bindings.AL_FORMAT_MONO8,
                8_000,
                lambda view: len(view),
            )
        with pytest.raises(ValueError, match="different OpenAL library"):
            context.set_static_buffer_data(
                bindings.Buffer(other_library, 5),
                bindings.AL_FORMAT_MONO8,
                b"samples",
                8_000,
            )


def test_buffer_callback_registration_is_serialized_across_threads() -> None:
    library, fake = _library()
    device = bindings.open_device(library=library)
    context = device.create_context()
    entered = threading.Event()
    release = threading.Event()
    call_lock = threading.Lock()
    native_calls = 0
    original = fake.al.buffer_callback_soft

    def blocking_install(
        buffer: int,
        format: int,
        frequency: int,
        callback: Callable[..., int],
        user_pointer: object | None,
    ) -> None:
        nonlocal native_calls
        with call_lock:
            native_calls += 1
            call_number = native_calls
        if call_number == 1:
            entered.set()
            assert release.wait(2)
        original(buffer, format, frequency, callback, user_pointer)

    fake.al.buffer_callback_soft = blocking_install  # type: ignore[assignment]
    registrations: dict[str, bindings.CallbackRegistration] = {}
    failures: list[BaseException] = []

    def install(name: str) -> None:
        try:
            registrations[name] = context.register_buffer_callback(
                6,
                bindings.AL_FORMAT_MONO8,
                8_000,
                lambda view: len(view),
            )
        except BaseException as error:
            failures.append(error)

    first = threading.Thread(target=install, args=("first",))
    second = threading.Thread(target=install, args=("second",))
    first.start()
    assert entered.wait(2)
    second.start()
    release.set()
    first.join(2)
    second.join(2)

    assert not first.is_alive() and not second.is_alive()
    assert failures == []
    assert registrations["first"].closed
    assert not registrations["second"].closed
    assert context._buffer_callbacks[6] is registrations["second"]
    assert fake.al.buffer_callbacks[6] is registrations["second"]._callback
    context.close()
    device.close()


def test_buffer_callback_failed_verification_is_rolled_back(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    library, fake = _library()
    original = fake.al.get_buffer_ptr_soft
    query_count = 0

    def fail_first_query(buffer: int, parameter: int) -> object | None:
        nonlocal query_count
        query_count += 1
        if query_count == 1:
            return None
        return original(buffer, parameter)

    monkeypatch.setattr(fake.al, "get_buffer_ptr_soft", fail_first_query)
    with (
        bindings.open_device(library=library) as device,
        device.create_context() as context,
    ):
        with pytest.raises(bindings.CallbackControlError, match="did not install"):
            context.register_buffer_callback(
                8,
                bindings.AL_FORMAT_MONO8,
                8_000,
                lambda view: len(view),
            )

        assert 8 not in fake.al.buffer_callbacks
        assert 8 not in context._buffer_callbacks


def test_foldback_registration_retains_memory_until_stopped() -> None:
    library, fake = _library()
    memory = bytearray(16)
    events: list[tuple[int, int]] = []
    with (
        bindings.open_device(library=library) as device,
        device.create_context() as context,
    ):
        registration = context.start_foldback(
            bindings.AL_FOLDBACK_MODE_MONO,
            4,
            1,
            memory,
            lambda event, index: events.append((event, index)),
        )
        native_callback = fake.al.foldback_callback
        assert native_callback is not None
        native_callback(bindings.AL_FOLDBACK_EVENT_BLOCK, 4)
        assert events == [(bindings.AL_FOLDBACK_EVENT_BLOCK, 4)]
        assert ctypes.sizeof(cast(Any, registration.memory)) == len(memory)

    assert events[-1] == (bindings.AL_FOLDBACK_EVENT_STOP, 0)
    assert registration.closed
    assert ctypes.sizeof(cast(Any, registration.memory)) == len(memory)
    assert fake.al.foldback_callback is None
    assert [call[0] for call in fake.al.foldback_calls] == ["start", "stop"]


def test_foldback_close_waits_for_native_stop_event() -> None:
    library, fake = _library()
    fake.al.foldback_auto_stop = False
    device = bindings.open_device(library=library)
    context = device.create_context()
    registration = context.start_foldback(
        bindings.AL_FOLDBACK_MODE_STEREO,
        2,
        2,
        bytearray(2 * 2 * 2 * ctypes.sizeof(native_types.ALfloat)),
        lambda _event, _index: None,
    )
    native_callback = fake.al.foldback_callback
    assert native_callback is not None
    close_thread = threading.Thread(target=registration.close)
    close_thread.start()
    assert fake.al.foldback_stop_requested.wait(2)

    assert close_thread.is_alive()
    assert registration.stopping
    assert registration._callback is not None
    native_callback(bindings.AL_FOLDBACK_EVENT_STOP, 0)
    close_thread.join(2)

    assert not close_thread.is_alive()
    assert registration.closed
    context.close()
    device.close()


def test_foldback_stop_event_is_finalized_by_the_owner() -> None:
    library, fake = _library()
    device = bindings.open_device(library=library)
    context = device.create_context()
    registration = context.start_foldback(
        bindings.AL_FOLDBACK_MODE_MONO,
        2,
        2,
        (native_types.ALfloat * 4)(),
        lambda _event, _index: None,
    )
    native_callback = fake.al.foldback_callback
    assert native_callback is not None

    native_callback(bindings.AL_FOLDBACK_EVENT_STOP, 0)

    assert not registration.closed
    assert registration._callback is native_callback
    registration.close()
    assert registration.closed
    assert [call[0] for call in fake.al.foldback_calls] == ["start"]
    context.close()
    device.close()


@pytest.mark.parametrize(
    ("mode", "count", "length", "float_count", "message"),
    [
        (bindings.AL_FOLDBACK_MODE_MONO, 1, 1, 1, "at least two"),
        (bindings.AL_FOLDBACK_MODE_MONO, 2, 2, 3, "at least 4"),
        (bindings.AL_FOLDBACK_MODE_STEREO, 2, 2, 7, "at least 8"),
        (0xDEAD, 2, 2, 8, "MONO or STEREO"),
    ],
)
def test_foldback_validates_mode_count_and_storage_capacity(
    mode: int,
    count: int,
    length: int,
    float_count: int,
    message: str,
) -> None:
    library, _fake = _library()
    memory = (native_types.ALfloat * float_count)()
    with (
        bindings.open_device(library=library) as device,
        device.create_context() as context,
        pytest.raises(ValueError, match=message),
    ):
        context.start_foldback(
            mode,
            count,
            length,
            memory,
            lambda _event, _index: None,
        )


def test_foldback_rejects_a_preexisting_al_error_before_start() -> None:
    library, fake = _library()
    fake.al.error = bindings.AL_INVALID_OPERATION
    with (
        bindings.open_device(library=library) as device,
        device.create_context() as context,
        pytest.raises(bindings.NativeCallError, match="pre-existing"),
    ):
        context.start_foldback(
            bindings.AL_FOLDBACK_MODE_MONO,
            2,
            2,
            (native_types.ALfloat * 4)(),
            lambda _event, _index: None,
        )

    assert fake.al.foldback_calls == []


def test_static_buffer_data_retains_exact_native_backing() -> None:
    library, fake = _library()
    data = bytearray(b"static audio")
    with bindings.open_device(library=library) as device:
        context = device.create_context()
        context.set_static_buffer_data(
            9,
            bindings.AL_FORMAT_MONO8,
            data,
            22_050,
        )

        assert fake.al.static_calls == [
            (9, bindings.AL_FORMAT_MONO8, b"static audio", 22_050)
        ]
        assert 9 in context._static_buffers
        context.close()
        assert not context._static_buffers


def test_static_buffer_data_does_not_retain_a_failed_update() -> None:
    library, fake = _library()
    fake.al.static_error = bindings.AL_INVALID_VALUE
    with bindings.open_device(library=library) as device:
        context = device.create_context()
        with pytest.raises(bindings.NativeCallError, match="static buffer update"):
            context.set_static_buffer_data(
                9,
                bindings.AL_FORMAT_MONO8,
                b"static audio",
                22_050,
            )

        assert 9 not in context._static_buffers
        context.close()


def test_capture_and_loopback_devices_use_their_matching_close_paths() -> None:
    library, fake = _library()
    with bindings.open_loopback_device(library=library) as loopback:
        assert loopback.is_render_format_supported(
            48_000,
            bindings.ALC_STEREO_SOFT,
            bindings.ALC_SHORT_SOFT,
        )
        target = bytearray(16)
        loopback.render_samples(target, 4)

    with bindings.open_capture_device(
        48_000,
        bindings.AL_FORMAT_MONO16,
        1_024,
        library=library,
    ) as capture:
        assert capture.name == "Test Capture Device"
        capture.start()
        capture.start()
        assert capture.available_samples == 12
        capture.read_samples(bytearray(8), 4)

    assert fake.alc.render_calls == [(fake.alc.device, target, 4)]
    assert fake.alc.capture_calls == [
        ("start", fake.alc.device),
        ("read:4", fake.alc.device),
        ("stop", fake.alc.device),
    ]
    assert fake.alc.capture_closed == [fake.alc.device]
