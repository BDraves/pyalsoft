"""Managed, chunk-free audio capture built on the low-level ALC handles."""

from __future__ import annotations

import atexit
import math
from contextlib import suppress
from dataclasses import dataclass
from threading import Event, RLock, Thread

from pyalsoft import bindings
from pyalsoft._managed.models import (
    PCM,
    AudioBackendError,
    AudioError,
    SampleType,
)


class CaptureOpenError(AudioError):
    """Raised when an audio capture device cannot be opened."""


@dataclass(frozen=True, slots=True)
class CaptureDevice:
    """A named capture device reported by the current OpenAL runtime."""

    name: str
    is_default: bool = False

    def __post_init__(self) -> None:
        if not isinstance(self.name, str):
            raise TypeError("name must be a string")
        if not self.name:
            raise ValueError("name cannot be empty")
        if not isinstance(self.is_default, bool):
            raise TypeError("is_default must be a boolean")


class Recording:
    """Opaque handle for a recording that is being collected in memory."""

    __slots__ = (
        "_channels",
        "_chunks",
        "_device",
        "_error",
        "_lock",
        "_pcm",
        "_sample_rate",
        "_sample_type",
        "_stop_event",
        "_thread",
    )

    def __init__(
        self,
        device: bindings.CaptureDevice,
        *,
        channels: int,
        sample_rate: int,
        sample_type: SampleType,
    ) -> None:
        self._device = device
        self._channels = channels
        self._sample_rate = sample_rate
        self._sample_type = sample_type
        self._chunks: list[bytes] = []
        self._stop_event = Event()
        self._thread: Thread | None = None
        self._error: Exception | None = None
        self._pcm: PCM | None = None
        self._lock = RLock()

    def __repr__(self) -> str:
        return "Recording(<opaque>)"


_CAPTURE_FORMAT_BY_LAYOUT = {
    (1, SampleType.UINT8): bindings.enums.ALFormat.FORMAT_MONO8,
    (1, SampleType.INT16): bindings.enums.ALFormat.FORMAT_MONO16,
    (2, SampleType.UINT8): bindings.enums.ALFormat.FORMAT_STEREO8,
    (2, SampleType.INT16): bindings.enums.ALFormat.FORMAT_STEREO16,
}

_active_recordings: set[Recording] = set()
_active_recordings_lock = RLock()


def _load_capture_library(
    library: bindings.OpenALLibrary | None,
) -> bindings.OpenALLibrary:
    if library is not None:
        return library
    try:
        return bindings.load()
    except bindings.LibraryNotFoundError as error:
        raise CaptureOpenError("could not load an OpenAL library") from error


def _clear_alc_errors(
    library: bindings.OpenALLibrary, device: object | None = None
) -> None:
    library.alc.get_error(device)


def _check_alc_error(
    library: bindings.OpenALLibrary,
    device: object | None,
    operation: str,
) -> None:
    error = int(library.alc.get_error(device))
    if error != bindings.ALC_NO_ERROR:
        raise AudioBackendError(
            f"OpenAL failed to {operation} (ALC error 0x{error:04x})"
        )


def _capture_layout(
    channels: int,
    sample_rate: int,
    sample_type: SampleType,
) -> tuple[bindings.enums.ALFormat, int]:
    if isinstance(channels, bool) or not isinstance(channels, int):
        raise TypeError("channels must be an integer")
    if channels not in (1, 2):
        raise ValueError("channels must be 1 or 2")
    if isinstance(sample_rate, bool) or not isinstance(sample_rate, int):
        raise TypeError("sample_rate must be an integer")
    if sample_rate <= 0:
        raise ValueError("sample_rate must be positive")
    if not isinstance(sample_type, SampleType):
        raise TypeError("sample_type must be a SampleType")
    return (
        _CAPTURE_FORMAT_BY_LAYOUT[(channels, sample_type)],
        channels * sample_type.byte_width,
    )


def list_capture_devices(
    *, library: bindings.OpenALLibrary | None = None
) -> tuple[CaptureDevice, ...]:
    """Return capture devices known to the selected OpenAL runtime."""

    library = _load_capture_library(library)
    _clear_alc_errors(library)
    names = library.alc.get_strings(None, bindings.ALC_CAPTURE_DEVICE_SPECIFIER)
    default_name = library.alc.get_string(
        None, bindings.ALC_CAPTURE_DEFAULT_DEVICE_SPECIFIER
    )
    _check_alc_error(library, None, "enumerate capture devices")
    return tuple(
        CaptureDevice(name, is_default=name == default_name)
        for name in dict.fromkeys(names)
    )


def _drain_recording(recording: Recording) -> None:
    library = recording._device.library
    handle = recording._device.handle
    _clear_alc_errors(library, handle)
    available_frames = recording._device.available_samples
    _check_alc_error(library, handle, "query available capture frames")
    if available_frames <= 0:
        return
    frame_width = recording._channels * recording._sample_type.byte_width
    samples = bytearray(available_frames * frame_width)
    recording._device.read_samples(samples, available_frames)
    _check_alc_error(library, handle, "read captured audio")
    recording._chunks.append(bytes(samples))


def _capture_worker(recording: Recording) -> None:
    try:
        while not recording._stop_event.wait(0.01):
            _drain_recording(recording)
    except Exception as error:
        recording._error = error
        recording._stop_event.set()


def start_recording(
    device_name: CaptureDevice | str | bytes | None = None,
    *,
    channels: int = 1,
    sample_rate: int = 48_000,
    sample_type: SampleType = SampleType.INT16,
    library: bindings.OpenALLibrary | None = None,
) -> Recording:
    """Start collecting captured audio in memory on a background thread."""

    format_name, _ = _capture_layout(channels, sample_rate, sample_type)
    if isinstance(device_name, CaptureDevice):
        device_name = device_name.name
    elif device_name is not None and not isinstance(device_name, (str, bytes)):
        raise TypeError("device_name must be a CaptureDevice, str, bytes, or None")

    library = _load_capture_library(library)
    try:
        device = bindings.open_capture_device(
            sample_rate,
            format_name,
            sample_rate,
            device_name,
            library=library,
        )
    except bindings.DeviceOpenError as error:
        raise CaptureOpenError("could not open the requested capture device") from error

    recording = Recording(
        device,
        channels=channels,
        sample_rate=sample_rate,
        sample_type=sample_type,
    )
    try:
        _clear_alc_errors(library, device.handle)
        device.start()
        _check_alc_error(library, device.handle, "start audio capture")
        thread = Thread(
            target=_capture_worker,
            args=(recording,),
            name="pyalsoft-capture",
            daemon=True,
        )
        recording._thread = thread
        with _active_recordings_lock:
            _active_recordings.add(recording)
        thread.start()
    except Exception:
        with _active_recordings_lock:
            _active_recordings.discard(recording)
        with suppress(Exception):
            device.close()
        raise
    return recording


def stop_recording(recording: Recording) -> PCM:
    """Stop a recording and return all captured audio as one PCM value."""

    if not isinstance(recording, Recording):
        raise TypeError("recording must be a Recording")
    with recording._lock:
        if recording._pcm is not None:
            return recording._pcm

        recording._stop_event.set()
        thread = recording._thread
        if thread is not None:
            thread.join()

        cleanup_error: Exception | None = None
        try:
            _clear_alc_errors(recording._device.library, recording._device.handle)
            recording._device.stop()
            _check_alc_error(
                recording._device.library,
                recording._device.handle,
                "stop audio capture",
            )
            _drain_recording(recording)
        except Exception as error:
            cleanup_error = error
        finally:
            try:
                recording._device.close()
            except Exception as error:
                if cleanup_error is None:
                    cleanup_error = error
            with _active_recordings_lock:
                _active_recordings.discard(recording)

        failure = recording._error or cleanup_error
        if failure is not None:
            raise AudioBackendError("OpenAL failed while recording audio") from failure
        samples = b"".join(recording._chunks)
        if not samples:
            raise AudioBackendError("the capture device returned no audio")
        recording._pcm = PCM(
            samples=samples,
            channels=recording._channels,
            sample_rate=recording._sample_rate,
            sample_type=recording._sample_type,
        )
        recording._chunks.clear()
        return recording._pcm


def record(
    duration_seconds: float,
    device_name: CaptureDevice | str | bytes | None = None,
    *,
    channels: int = 1,
    sample_rate: int = 48_000,
    sample_type: SampleType = SampleType.INT16,
    library: bindings.OpenALLibrary | None = None,
) -> PCM:
    """Record for a fixed duration and return the captured PCM audio."""

    if isinstance(duration_seconds, bool) or not isinstance(
        duration_seconds, (int, float)
    ):
        raise TypeError("duration_seconds must be a number")
    duration = float(duration_seconds)
    if not math.isfinite(duration):
        raise ValueError("duration_seconds must be finite")
    if duration <= 0.0:
        raise ValueError("duration_seconds must be positive")

    recording = start_recording(
        device_name,
        channels=channels,
        sample_rate=sample_rate,
        sample_type=sample_type,
        library=library,
    )
    try:
        recording._stop_event.wait(duration)
    except BaseException:
        # Do not leave the capture device and collector thread running when a
        # blocking recording is interrupted (most commonly by Ctrl+C).
        with suppress(Exception):
            stop_recording(recording)
        raise
    return stop_recording(recording)


def _shutdown_recordings_at_exit() -> None:
    with _active_recordings_lock:
        recordings = tuple(_active_recordings)
    for recording in recordings:
        with suppress(Exception):
            stop_recording(recording)


atexit.register(_shutdown_recordings_at_exit)


__all__ = [
    "CaptureDevice",
    "CaptureOpenError",
    "Recording",
    "list_capture_devices",
    "record",
    "start_recording",
    "stop_recording",
]
