"""Tests for owned ALC devices and contexts."""

from __future__ import annotations

import pytest

from pyalsoft import bindings
from tests._support.alc_backend import _context_pointer, _library


def test_owned_open_helpers_reject_null_native_handles(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    library, fake = _library()
    monkeypatch.setattr(fake.alc, "open_device", lambda _name: None)
    with pytest.raises(bindings.DeviceOpenError, match="playback device"):
        bindings.open_device(library=library)

    library, fake = _library()
    monkeypatch.setattr(fake.alc, "loopback_open_device_soft", lambda _name: None)
    with pytest.raises(bindings.DeviceOpenError, match="loopback device"):
        bindings.open_loopback_device(library=library)

    library, fake = _library()
    monkeypatch.setattr(
        fake.alc,
        "capture_open_device",
        lambda _name, _frequency, _format, _buffer_size: None,
    )
    with pytest.raises(bindings.DeviceOpenError, match="capture device"):
        bindings.open_capture_device(
            8_000,
            bindings.AL_FORMAT_MONO16,
            8_000,
            library=library,
        )


def test_owned_context_creation_rejects_a_null_native_handle(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    library, fake = _library()
    device = bindings.open_device(library=library)
    monkeypatch.setattr(
        fake.alc,
        "create_context",
        lambda _device, _attributes: None,
    )

    with pytest.raises(bindings.ContextCreateError, match="create"):
        device.create_context()

    assert device._contexts == []
    device.close()


def test_owned_context_reports_activation_and_restoration_failures(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    library, fake = _library()
    device = bindings.open_device(library=library)
    context = device.create_context()

    monkeypatch.setattr(fake.alc, "make_context_current", lambda _context: False)
    with pytest.raises(bindings.ContextActivationError, match="make.*current"):
        context.make_current()

    previous = _context_pointer()
    fake.alc.current = previous

    def fail_restoration(selected: object | None) -> bool:
        if selected is previous:
            return False
        fake.alc.current = selected
        return True

    monkeypatch.setattr(fake.alc, "make_context_current", fail_restoration)
    with (
        pytest.raises(bindings.ContextActivationError, match="restore"),
        context.activate(),
    ):
        assert fake.alc.current is fake.alc.context

    device.close()


def test_owned_device_reports_a_refused_native_close(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    library, fake = _library()
    device = bindings.open_device(library=library)
    monkeypatch.setattr(fake.alc, "close_device", lambda _device: False)

    with pytest.raises(bindings.DeviceCloseError, match="refused"):
        device.close()

    assert not device.closed
    assert fake.invalidated_devices == []


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
