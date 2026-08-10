"""Reusable fake backend for owned ALC handle tests."""

from __future__ import annotations

import ctypes
import threading
from collections.abc import Callable, Sequence
from typing import Any, cast

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
