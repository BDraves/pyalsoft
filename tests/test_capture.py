"""Tests for managed, in-memory audio capture."""

from __future__ import annotations

import ctypes
from collections.abc import Buffer
from threading import Event, RLock
from typing import cast

import pytest

import pyalsoft._capture as capture
from pyalsoft import (
    CaptureDevice,
    CaptureOpenError,
    Recording,
    SampleType,
    bindings,
    list_capture_devices,
    record,
    start_recording,
    stop_recording,
)


class FakeCaptureALC:
    def __init__(self, samples: bytes = b"\x01\x00\x02\x00") -> None:
        self.device = ctypes.c_void_p(1234)
        self.device_names = ("Microphone", "USB Input", "Microphone")
        self.default_device_name = "Microphone"
        self.samples = bytearray(samples)
        self.frame_width = 2
        self.opened_name: str | bytes | None = None
        self.opened_frequency = 0
        self.opened_format = 0
        self.buffer_size = 0
        self.started = 0
        self.stopped = 0
        self.closed = 0
        self.error = bindings.ALC_NO_ERROR
        self.open_succeeds = True

    def get_error(self, device: object | None) -> int:
        del device
        error, self.error = self.error, bindings.ALC_NO_ERROR
        return error

    def get_strings(self, device: object | None, parameter: int) -> tuple[str, ...]:
        assert device is None
        assert parameter == bindings.ALC_CAPTURE_DEVICE_SPECIFIER
        return self.device_names

    def get_string(self, device: object | None, parameter: int) -> str | None:
        if device is None:
            assert parameter == bindings.ALC_CAPTURE_DEFAULT_DEVICE_SPECIFIER
            return self.default_device_name
        assert device is self.device
        assert parameter == bindings.ALC_CAPTURE_DEVICE_SPECIFIER
        return self.default_device_name

    def capture_open_device(
        self,
        name: str | bytes | None,
        frequency: int,
        format_name: int,
        buffer_size: int,
    ) -> object | None:
        self.opened_name = name
        self.opened_frequency = frequency
        self.opened_format = int(format_name)
        self.buffer_size = buffer_size
        self.frame_width = {
            int(bindings.AL_FORMAT_MONO8): 1,
            int(bindings.AL_FORMAT_MONO16): 2,
            int(bindings.AL_FORMAT_STEREO8): 2,
            int(bindings.AL_FORMAT_STEREO16): 4,
        }[self.opened_format]
        return self.device if self.open_succeeds else None

    def capture_start(self, device: object) -> None:
        assert device is self.device
        self.started += 1

    def capture_stop(self, device: object) -> None:
        assert device is self.device
        self.stopped += 1

    def get_integerv(
        self, device: object, parameter: int, size: int
    ) -> tuple[int, ...]:
        assert device is self.device
        assert parameter == bindings.ALC_CAPTURE_SAMPLES
        assert size == 1
        return (len(self.samples) // self.frame_width,)

    def capture_samples(self, device: object, destination: object, frames: int) -> None:
        assert device is self.device
        byte_count = frames * self.frame_width
        view = memoryview(cast(Buffer, destination)).cast("B")
        view[:byte_count] = self.samples[:byte_count]
        del self.samples[:byte_count]

    def capture_close_device(self, device: object) -> bool:
        assert device is self.device
        self.closed += 1
        return True


class FakeCaptureLibrary:
    def __init__(self, samples: bytes = b"\x01\x00\x02\x00") -> None:
        self.alc = FakeCaptureALC(samples)
        self._context_lock = RLock()
        self.invalidated: list[object] = []

    def _invalidate_device_extensions(self, device: object) -> None:
        self.invalidated.append(device)


def as_library(library: FakeCaptureLibrary) -> bindings.OpenALLibrary:
    return cast(bindings.OpenALLibrary, library)


def test_capture_devices_are_enumerated_and_deduplicated() -> None:
    library = FakeCaptureLibrary()

    devices = list_capture_devices(library=as_library(library))

    assert devices == (
        CaptureDevice("Microphone", is_default=True),
        CaptureDevice("USB Input"),
    )


def test_recording_collects_pcm_and_owns_native_lifecycle() -> None:
    library = FakeCaptureLibrary(b"\x01\x00\x02\x00\x03\x00")

    recording = start_recording(
        CaptureDevice("USB Input"),
        sample_rate=8_000,
        library=as_library(library),
    )
    captured = stop_recording(recording)

    assert isinstance(recording, Recording)
    assert repr(recording) == "Recording(<opaque>)"
    assert captured.samples == b"\x01\x00\x02\x00\x03\x00"
    assert captured.channels == 1
    assert captured.sample_rate == 8_000
    assert captured.sample_type is SampleType.INT16
    assert captured.frame_count == 3
    assert library.alc.opened_name == "USB Input"
    assert library.alc.opened_frequency == 8_000
    assert library.alc.buffer_size == 8_000
    assert library.alc.started == 1
    assert library.alc.stopped == 1
    assert library.alc.closed == 1
    assert recording._chunks == []
    assert stop_recording(recording) is captured


def test_capture_layout_selects_stereo_uint8() -> None:
    library = FakeCaptureLibrary(b"\x01\x02\x03\x04")

    captured = stop_recording(
        start_recording(
            channels=2,
            sample_rate=22_050,
            sample_type=SampleType.UINT8,
            library=as_library(library),
        )
    )

    assert captured.frame_count == 2
    assert library.alc.opened_format == bindings.AL_FORMAT_STEREO8


def test_fixed_duration_recording_uses_the_same_managed_result() -> None:
    library = FakeCaptureLibrary(b"\x01\x00\x02\x00")

    captured = record(0.001, sample_rate=8_000, library=as_library(library))

    assert captured.frame_count == 2
    assert library.alc.closed == 1


def test_fixed_duration_recording_cleans_up_when_interrupted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    library = FakeCaptureLibrary(b"\x01\x00\x02\x00")
    original_wait = Event.wait

    def interrupt_duration_wait(event: Event, timeout: float | None = None) -> bool:
        if timeout == 1.0:
            raise KeyboardInterrupt
        return original_wait(event, timeout)

    monkeypatch.setattr(Event, "wait", interrupt_duration_wait)

    with pytest.raises(KeyboardInterrupt):
        record(1.0, sample_rate=8_000, library=as_library(library))

    assert library.alc.stopped == 1
    assert library.alc.closed == 1
    assert not capture._active_recordings


@pytest.mark.parametrize("channels", [0, 3])
def test_recording_rejects_unsupported_channel_counts(channels: int) -> None:
    with pytest.raises(ValueError, match="channels must be 1 or 2"):
        start_recording(channels=channels)


@pytest.mark.parametrize("duration", [0.0, -1.0, float("inf")])
def test_record_rejects_invalid_durations(duration: float) -> None:
    with pytest.raises(ValueError, match="duration_seconds"):
        record(duration)


def test_capture_open_failure_uses_managed_exception() -> None:
    library = FakeCaptureLibrary()
    library.alc.open_succeeds = False

    with pytest.raises(CaptureOpenError, match="requested capture device"):
        start_recording(library=as_library(library))
