"""Tests for owned backend device, context, and callback handles."""

from __future__ import annotations

import ctypes
import threading
from collections.abc import Callable, Sequence
from typing import cast

import pytest

from pyalsoft import bindings
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
        self.event_controls: list[tuple[tuple[int, ...], bool]] = []
        self.enabled: set[int] = set()

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


class FakeLibrary:
    def __init__(self) -> None:
        self.al = FakeAL()
        self.alc = FakeALC()
        self.extensions = FakeExtensions()
        self.alc_extensions = {"ALC_EXT_thread_local_context"}
        self._context_lock = threading.RLock()

    def is_alc_extension_present(
        self,
        extension: str,
        _device: object | None = None,
    ) -> bool:
        return extension in self.alc_extensions


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
