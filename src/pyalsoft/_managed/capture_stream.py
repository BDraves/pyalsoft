"""Bounded incremental audio capture."""

from __future__ import annotations

import atexit
import math
from contextlib import suppress
from dataclasses import dataclass
from threading import Condition, Event, RLock, Thread
from types import TracebackType
from typing import Self

from pyalsoft import bindings
from pyalsoft._managed._backend import _check_alc_error, _clear_alc_errors
from pyalsoft._managed.audio import PCM, SampleType
from pyalsoft._managed.capture import (
    CaptureDevice,
    CaptureOpenError,
    _capture_layout,
    _load_capture_library,
)
from pyalsoft._managed.errors import AudioBackendError


@dataclass(frozen=True, slots=True)
class CaptureStreamStatus:
    """Current bounded capture-buffer accounting.

    Attributes:
        buffered_frames: Unread frames currently retained.
        capacity_frames: Maximum unread frames retained.
        overrun_count: Oldest frames discarded since capture started.
        closed: Whether native capture has stopped and the device is closed.
    """

    buffered_frames: int
    capacity_frames: int
    overrun_count: int
    closed: bool


class CaptureStream:
    """Opaque owner for bounded incremental capture.

    Use [`start_capture_stream`][pyalsoft.start_capture_stream] and consume data
    with [`read_capture_stream`][pyalsoft.read_capture_stream]. If the producer
    outruns the consumer, the oldest frames are discarded and ``overrun_count``
    records how many frames were lost.
    """

    __slots__ = (
        "_buffer",
        "_capacity_frames",
        "_channels",
        "_close_lock",
        "_closed",
        "_condition",
        "_device",
        "_error",
        "_frame_width",
        "_overrun_count",
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
        capacity_frames: int,
    ) -> None:
        self._device = device
        self._channels = channels
        self._sample_rate = sample_rate
        self._sample_type = sample_type
        self._frame_width = channels * sample_type.byte_width
        self._capacity_frames = capacity_frames
        self._buffer = bytearray()
        self._overrun_count = 0
        self._error: Exception | None = None
        self._condition = Condition(RLock())
        self._close_lock = RLock()
        self._stop_event = Event()
        self._thread: Thread | None = None
        self._closed = False

    def __enter__(self) -> Self:
        return self

    def __exit__(
        self,
        exception_type: type[BaseException] | None,
        exception: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        del exception_type, exception, traceback
        stop_capture_stream(self)

    def __repr__(self) -> str:
        return "CaptureStream(<opaque>)"


_active_capture_streams: set[CaptureStream] = set()
_active_capture_streams_lock = RLock()


def _append_capture_frames(stream: CaptureStream, samples: bytes) -> None:
    capacity_bytes = stream._capacity_frames * stream._frame_width
    with stream._condition:
        combined_size = len(stream._buffer) + len(samples)
        overflow_bytes = max(0, combined_size - capacity_bytes)
        if overflow_bytes:
            from_buffer = min(overflow_bytes, len(stream._buffer))
            if from_buffer:
                del stream._buffer[:from_buffer]
            from_samples = overflow_bytes - from_buffer
            if from_samples:
                samples = samples[from_samples:]
            stream._overrun_count += overflow_bytes // stream._frame_width
        stream._buffer.extend(samples)
        stream._condition.notify_all()


def _drain_capture_stream(stream: CaptureStream) -> None:
    library = stream._device.library
    handle = stream._device.handle
    _clear_alc_errors(library, handle)
    available_frames = stream._device.available_samples
    _check_alc_error(library, handle, "query available capture frames")
    if available_frames <= 0:
        return
    samples = bytearray(available_frames * stream._frame_width)
    stream._device.read_samples(samples, available_frames)
    _check_alc_error(library, handle, "read captured audio")
    _append_capture_frames(stream, bytes(samples))


def _capture_stream_worker(stream: CaptureStream) -> None:
    try:
        while not stream._stop_event.wait(0.01):
            _drain_capture_stream(stream)
    except Exception as error:
        with stream._condition:
            stream._error = error
            stream._condition.notify_all()
        stream._stop_event.set()


def start_capture_stream(
    device_name: CaptureDevice | str | bytes | None = None,
    *,
    channels: int = 1,
    sample_rate: int = 48_000,
    sample_type: SampleType = SampleType.INT16,
    capacity_frames: int = 48_000,
    library: bindings.OpenALLibrary | None = None,
) -> CaptureStream:
    """Start bounded incremental capture on a background collector thread.

    Args:
        device_name: Capture device object or device specifier. ``None`` selects
            the runtime's default capture device.
        channels: Interleaved channel count.
        sample_rate: Positive sample frames captured per second.
        sample_type: Unsigned 8-bit, signed 16-bit, or float32 representation.
        capacity_frames: Positive maximum number of unread frames retained in
            managed memory. The oldest frames are discarded after an overrun.
        library: Loaded low-level library, or ``None`` for automatic discovery.

    Returns:
        A bounded capture stream that has already started collecting frames.

    Raises:
        TypeError: A format, capacity, or device argument has the wrong type.
        ValueError: The format or capacity is invalid.
        CaptureOpenError: OpenAL cannot be loaded or the device cannot open.
        AudioBackendError: The backend cannot start capture.
    """

    if isinstance(capacity_frames, bool) or not isinstance(capacity_frames, int):
        raise TypeError("capacity_frames must be an integer")
    if capacity_frames <= 0:
        raise ValueError("capacity_frames must be positive")
    format_name, _ = _capture_layout(channels, sample_rate, sample_type)
    if isinstance(device_name, CaptureDevice):
        device_name = device_name.name
    elif device_name is not None and not isinstance(device_name, (str, bytes)):
        raise TypeError("device_name must be a CaptureDevice, str, bytes, or None")

    selected = _load_capture_library(library)
    try:
        device = bindings.open_capture_device(
            sample_rate,
            format_name,
            capacity_frames,
            device_name,
            library=selected,
        )
    except bindings.DeviceOpenError as error:
        raise CaptureOpenError("could not open the requested capture device") from error

    stream = CaptureStream(
        device,
        channels=channels,
        sample_rate=sample_rate,
        sample_type=sample_type,
        capacity_frames=capacity_frames,
    )
    try:
        _clear_alc_errors(selected, device.handle)
        device.start()
        _check_alc_error(selected, device.handle, "start incremental audio capture")
        thread = Thread(
            target=_capture_stream_worker,
            args=(stream,),
            name="pyalsoft-capture-stream",
            daemon=True,
        )
        stream._thread = thread
        with _active_capture_streams_lock:
            _active_capture_streams.add(stream)
        thread.start()
    except Exception:
        with _active_capture_streams_lock:
            _active_capture_streams.discard(stream)
        with suppress(Exception):
            device.close()
        raise
    return stream


def read_capture_stream(
    stream: CaptureStream,
    max_frames: int | None = None,
    *,
    timeout: float | None = None,
) -> PCM | None:
    """Read and consume available frames, waiting when the buffer is empty.

    Args:
        stream: Live or closed bounded capture stream.
        max_frames: Maximum frames to consume, or ``None`` for every frame that
            is currently buffered when the read completes.
        timeout: Maximum wall-clock seconds to wait for a frame, or ``None`` for
            no limit.

    Returns:
        Captured PCM, or ``None`` after timeout or when a closed stream is empty.

    Raises:
        TypeError: A stream, frame count, or timeout has the wrong type.
        ValueError: A frame count or timeout is invalid.
        AudioBackendError: Background capture failed.
    """

    if not isinstance(stream, CaptureStream):
        raise TypeError("stream must be a CaptureStream")
    if max_frames is not None:
        if isinstance(max_frames, bool) or not isinstance(max_frames, int):
            raise TypeError("max_frames must be an integer or None")
        if max_frames <= 0:
            raise ValueError("max_frames must be positive")
    if timeout is not None:
        if isinstance(timeout, bool) or not isinstance(timeout, (int, float)):
            raise TypeError("timeout must be a number or None")
        timeout = float(timeout)
        if not math.isfinite(timeout):
            raise ValueError("timeout must be finite")
        if timeout < 0.0:
            raise ValueError("timeout must be non-negative")

    with stream._condition:
        if not stream._buffer and not stream._closed and stream._error is None:
            stream._condition.wait_for(
                lambda: (
                    bool(stream._buffer) or stream._closed or stream._error is not None
                ),
                timeout,
            )
        if not stream._buffer:
            if stream._error is not None:
                raise AudioBackendError("OpenAL failed while capturing audio") from (
                    stream._error
                )
            return None
        available_frames = len(stream._buffer) // stream._frame_width
        frame_count = (
            available_frames
            if max_frames is None
            else min(max_frames, available_frames)
        )
        byte_count = frame_count * stream._frame_width
        samples = bytes(stream._buffer[:byte_count])
        del stream._buffer[:byte_count]
    return PCM(
        samples,
        channels=stream._channels,
        sample_rate=stream._sample_rate,
        sample_type=stream._sample_type,
    )


def get_capture_stream_status(stream: CaptureStream) -> CaptureStreamStatus:
    """Return bounded-buffer usage, loss accounting, and lifecycle state.

    Raises:
        TypeError: ``stream`` is not a [`CaptureStream`][pyalsoft.CaptureStream].
    """

    if not isinstance(stream, CaptureStream):
        raise TypeError("stream must be a CaptureStream")
    with stream._condition:
        return CaptureStreamStatus(
            buffered_frames=len(stream._buffer) // stream._frame_width,
            capacity_frames=stream._capacity_frames,
            overrun_count=stream._overrun_count,
            closed=stream._closed,
        )


def stop_capture_stream(stream: CaptureStream) -> None:
    """Stop bounded capture, close its device, and wake waiting readers.

    Calling this again after a successful stop is harmless. Buffered frames
    remain readable until consumed.

    Raises:
        TypeError: ``stream`` is not a [`CaptureStream`][pyalsoft.CaptureStream].
        AudioBackendError: Capture or cleanup failed.
    """

    if not isinstance(stream, CaptureStream):
        raise TypeError("stream must be a CaptureStream")
    with stream._close_lock:
        if stream._closed:
            return
        stream._stop_event.set()
        if stream._thread is not None:
            stream._thread.join()

        cleanup_error: Exception | None = None
        try:
            _clear_alc_errors(stream._device.library, stream._device.handle)
            stream._device.stop()
            _check_alc_error(
                stream._device.library,
                stream._device.handle,
                "stop incremental audio capture",
            )
            _drain_capture_stream(stream)
        except Exception as error:
            cleanup_error = error
        finally:
            try:
                stream._device.close()
            except Exception as error:
                if cleanup_error is None:
                    cleanup_error = error
            with stream._condition:
                stream._closed = True
                stream._condition.notify_all()
            with _active_capture_streams_lock:
                _active_capture_streams.discard(stream)

        failure = stream._error or cleanup_error
        if failure is not None:
            raise AudioBackendError("OpenAL failed while capturing audio") from failure


def _shutdown_capture_streams_at_exit() -> None:
    with _active_capture_streams_lock:
        streams = tuple(_active_capture_streams)
    for stream in streams:
        with suppress(Exception):
            stop_capture_stream(stream)


atexit.register(_shutdown_capture_streams_at_exit)
