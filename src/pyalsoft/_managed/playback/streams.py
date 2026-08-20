"""Bounded-buffer streaming playback operations."""

from __future__ import annotations

import math
from collections import deque
from collections.abc import Buffer
from contextlib import suppress
from dataclasses import dataclass

from pyalsoft import bindings
from pyalsoft._managed._backend import (
    _buffer_format_for_pcm,
    _prepare_buffer_data,
)
from pyalsoft._managed.audio import (
    _BLOCK_FORMATS,
    _BUFFER_FORMAT_SPECS,
    _IMA4_FORMATS,
    AmbisonicLayout,
    AmbisonicScaling,
    BufferData,
    BufferFormat,
    SampleType,
    _validate_pcm_layout,
)
from pyalsoft._managed.errors import (
    AudioBackendError,
    InvalidHandleError,
    InvalidVoiceStateError,
)
from pyalsoft._managed.playback.effects import (
    _EMPTY_EFX_RESOURCES,
    _apply_voice_config,
    _delete_efx_resources,
    _EfxResources,
    _install_efx_resources,
)
from pyalsoft._managed.playback.session import (
    Playback,
    _check_al_error,
    _clear_al_errors,
    _get_voice_state,
    _prepare_al,
    _require_playback,
    _serialized_playback,
)
from pyalsoft._managed.playback.source_controls import (
    _apply_start_delay,
    _start_source,
    _validate_playback_timing,
    _validate_source_layout,
)
from pyalsoft._managed.resources import (
    Stream,
    StreamState,
    StreamStatus,
    VoiceState,
)
from pyalsoft._managed.spatial import _DEFAULT_VOICE_CONFIG, VoiceConfig


@dataclass(frozen=True, slots=True)
class _StreamChunk:
    buffer: int
    frame_count: int
    duration: float


@dataclass(slots=True)
class _StreamRecord:
    identifier: int
    buffers: tuple[int, ...]
    free_buffers: deque[int]
    queued_chunks: deque[_StreamChunk]
    channels: int
    sample_rate: int
    sample_type: SampleType | None
    format: BufferFormat
    block_alignment: int | None
    ambisonic_order: int
    ambisonic_layout: AmbisonicLayout | None
    ambisonic_scaling: AmbisonicScaling | None
    config: VoiceConfig
    efx: _EfxResources = _EMPTY_EFX_RESOURCES
    state: StreamState = StreamState.INITIAL
    input_finished: bool = False
    underrun_count: int = 0
    underrun_active: bool = False


def _stream_record(playback: Playback, stream: Stream) -> _StreamRecord:
    _require_playback(playback)
    if not isinstance(stream, Stream) or stream._owner is not playback._token:
        raise InvalidHandleError("stream does not belong to this playback session")
    record = playback._streams.get(stream._token)
    if record is None or record.identifier != stream._identifier:
        raise InvalidHandleError("stream has been released")
    return record


def _validate_stream_layout(
    channels: int | None,
    sample_rate: int,
    sample_type: SampleType | None,
    format: BufferFormat | None,
    buffer_count: int,
    block_alignment: int | None,
    ambisonic_order: int,
    ambisonic_layout: AmbisonicLayout | None,
    ambisonic_scaling: AmbisonicScaling | None,
) -> tuple[int, SampleType | None, BufferFormat, BufferData]:
    if isinstance(buffer_count, bool) or not isinstance(buffer_count, int):
        raise TypeError("buffer_count must be an integer")
    if buffer_count <= 0:
        raise ValueError("buffer_count must be positive")
    resolved_sample_type: SampleType | None
    if format is None:
        if channels is None:
            raise TypeError("channels is required when format is not provided")
        resolved_sample_type = sample_type or SampleType.INT16
        _validate_pcm_layout(channels, sample_rate, resolved_sample_type)
        resolved_format = _buffer_format_for_pcm(channels, resolved_sample_type)
    else:
        if not isinstance(format, BufferFormat):
            raise TypeError("format must be a BufferFormat or None")
        if sample_type is not None:
            raise ValueError("sample_type and format cannot both be provided")
        resolved_format = format
        resolved_sample_type = format.sample_type

    spec = _BUFFER_FORMAT_SPECS[resolved_format]
    inferred_channels = spec.channels
    if spec.ambisonic_dimensions == 2:
        inferred_channels = ambisonic_order * 2 + 1
    elif spec.ambisonic_dimensions == 3:
        inferred_channels = (ambisonic_order + 1) ** 2
    template_channels = channels if channels is not None else inferred_channels
    template_frame_count = 1
    if resolved_format in _BLOCK_FORMATS:
        assert template_channels is not None
        alignment = block_alignment or (65 if resolved_format in _IMA4_FORMATS else 64)
        template_frame_count = alignment
        bytes_per_channel = (
            (alignment - 1) // 2 + 4
            if resolved_format in _IMA4_FORMATS
            else (alignment - 2) // 2 + 7
        )
        sample_bytes = bytes(template_channels * bytes_per_channel)
    elif resolved_format.sample_width_bytes is None or template_channels is None:
        sample_bytes = b"\0"
    else:
        sample_bytes = bytes(template_channels * resolved_format.sample_width_bytes)
    template = BufferData(
        samples=sample_bytes,
        format=resolved_format,
        sample_rate=sample_rate,
        frame_count=template_frame_count,
        channels=channels,
        block_alignment=block_alignment,
        ambisonic_order=ambisonic_order,
        ambisonic_layout=ambisonic_layout,
        ambisonic_scaling=ambisonic_scaling,
    )
    assert template.channels is not None
    return template.channels, resolved_sample_type, resolved_format, template


@_serialized_playback
def open_stream(
    playback: Playback,
    *,
    channels: int | None = None,
    sample_rate: int,
    sample_type: SampleType | None = None,
    format: BufferFormat | None = None,
    buffer_count: int = 4,
    block_alignment: int | None = None,
    ambisonic_order: int = 1,
    ambisonic_layout: AmbisonicLayout | None = None,
    ambisonic_scaling: AmbisonicScaling | None = None,
    config: VoiceConfig = _DEFAULT_VOICE_CONFIG,
) -> Stream:
    """Create an unstarted source with a bounded pool of streaming buffers.

    Queue at least one chunk with
    [`try_write_stream`][pyalsoft.try_write_stream] before calling
    [`start_stream`][pyalsoft.start_stream]. All chunks must use the format
    declared here. Streams cannot use ``VoiceConfig(looping=True)``.

    Args:
        playback: Open session that will own the stream.
        channels: Interleaved channel count. Required for ordinary PCM and for
            WAVE or Vorbis data; otherwise inferred from ``format``.
        sample_rate: Positive number of sample frames per second.
        sample_type: PCM representation. Defaults to signed 16-bit when
            ``format`` is omitted and cannot be combined with ``format``.
        format: Exact extension format, or ``None`` for ordinary PCM.
        buffer_count: Positive maximum number of chunks that may be queued before
            backpressure is reported.
        block_alignment: Optional compressed-format alignment in sample frames.
        ambisonic_order: B-format ambisonic order from 1 through 14.
        ambisonic_layout: Optional B-format channel ordering.
        ambisonic_scaling: Optional B-format coefficient normalization.
        config: Initial voice configuration. ``looping`` must be false.

    Returns:
        An opaque stream in the ``StreamState.INITIAL`` state.

    Raises:
        TypeError: A format or configuration argument has the wrong type.
        ValueError: The format or buffer count is invalid, or looping is enabled.
        PlaybackClosedError: ``playback`` is closed.
        AudioBackendError: OpenAL cannot allocate or configure stream resources.
    """

    channels, sample_type, format, template = _validate_stream_layout(
        channels,
        sample_rate,
        sample_type,
        format,
        buffer_count,
        block_alignment,
        ambisonic_order,
        ambisonic_layout,
        ambisonic_scaling,
    )
    if not isinstance(config, VoiceConfig):
        raise TypeError("config must be a VoiceConfig")
    if config.looping:
        raise ValueError("streaming voices cannot loop")
    _validate_source_layout(config, channels, format)

    _prepare_al(playback)
    source_ids: tuple[int, ...] = ()
    buffer_ids: tuple[int, ...] = ()
    efx = _EMPTY_EFX_RESOURCES
    try:
        source_ids = playback._library.al.gen_sources()
        if len(source_ids) != 1:
            raise AudioBackendError("OpenAL did not create exactly one source")
        _check_al_error(playback, "create stream source")
        buffer_ids = playback._library.al.gen_buffers(buffer_count)
        if len(buffer_ids) != buffer_count:
            raise AudioBackendError(
                f"OpenAL did not create exactly {buffer_count} stream buffers"
            )
        _check_al_error(playback, "create stream buffers")
        for buffer_id in buffer_ids:
            _prepare_buffer_data(playback._library, buffer_id, template)
        _check_al_error(playback, "configure stream buffers")
        _apply_voice_config(playback, source_ids[0], config)
        efx = _install_efx_resources(
            playback,
            source_ids[0],
            config,
        )
        _check_al_error(playback, "configure stream")
    except Exception:
        _clear_al_errors(playback)
        if source_ids:
            playback._library.al.source_stopv(source_ids)
            playback._library.al.delete_sources(source_ids)
            for identifier in source_ids:
                playback._super_stereo_width_defaults.pop(identifier, None)
        with suppress(Exception):
            _delete_efx_resources(
                playback,
                efx,
                operation="clean up incomplete stream EFX resources",
            )
        if buffer_ids:
            playback._library.al.delete_buffers(buffer_ids)
        playback._library.al.get_error()
        raise

    token = object()
    identifier = source_ids[0]
    playback._streams[token] = _StreamRecord(
        identifier=identifier,
        buffers=buffer_ids,
        free_buffers=deque(buffer_ids),
        queued_chunks=deque(),
        channels=channels,
        sample_rate=sample_rate,
        sample_type=sample_type,
        format=format,
        block_alignment=block_alignment,
        ambisonic_order=ambisonic_order,
        ambisonic_layout=ambisonic_layout,
        ambisonic_scaling=ambisonic_scaling,
        config=config,
        efx=efx,
    )
    return Stream(playback._token, token, identifier)


def _copy_stream_samples(samples: Buffer) -> bytes:
    try:
        view = memoryview(samples)
    except TypeError as error:
        raise TypeError("samples must be bytes-like") from error
    try:
        return view.tobytes()
    finally:
        view.release()


@_serialized_playback
def try_write_stream(
    playback: Playback,
    stream: Stream,
    samples: Buffer,
    *,
    frame_count: int | None = None,
) -> bool:
    """Queue one complete PCM chunk, or report bounded-buffer backpressure.

    This function copies ``samples`` before returning. Call
    [`update_stream`][pyalsoft.update_stream] regularly to reclaim processed
    buffers, then retry when this function returns ``False``.

    Args:
        playback: Session that owns ``stream``.
        stream: Live stream that has not reached end-of-input.
        samples: Non-empty bytes-like object in the format declared by
            [`open_stream`][pyalsoft.open_stream].
        frame_count: Decoded frame count for an encoded chunk. Fixed-width
            formats infer it and only accept a matching explicit value.

    Returns:
        ``True`` when the chunk was queued, or ``False`` when every stream buffer
        is still in use. ``False`` does not consume or validate ``samples``.

    Raises:
        TypeError: ``samples`` is not bytes-like or a handle has the wrong type.
        ValueError: The sample bytes or decoded frame count are invalid.
        InvalidHandleError: ``stream`` is released or belongs to another session.
        InvalidVoiceStateError: The stream is terminal or input is already finished.
        PlaybackClosedError: ``playback`` is closed.
        AudioBackendError: OpenAL cannot upload or queue the chunk.
    """

    record = _stream_record(playback, stream)
    if record.state in (StreamState.FINISHED, StreamState.STOPPED):
        raise InvalidVoiceStateError(
            f"cannot write to a stream in the {record.state.value} state"
        )
    if record.input_finished:
        raise InvalidVoiceStateError("cannot write to a stream after end-of-input")
    if not record.free_buffers:
        return False

    data = _copy_stream_samples(samples)
    if not data:
        raise ValueError("samples cannot be empty")
    sample_width = record.format.sample_width_bytes
    if sample_width is None:
        if frame_count is None:
            raise ValueError("frame_count is required for encoded stream chunks")
        resolved_frame_count = frame_count
    else:
        frame_width = record.channels * sample_width
        if len(data) % frame_width:
            raise ValueError("samples must contain a whole number of frames")
        resolved_frame_count = len(data) // frame_width
        if frame_count is not None and frame_count != resolved_frame_count:
            raise ValueError("frame_count does not match the fixed-width sample data")

    chunk = BufferData(
        samples=data,
        format=record.format,
        sample_rate=record.sample_rate,
        frame_count=resolved_frame_count,
        channels=record.channels,
        block_alignment=record.block_alignment,
        ambisonic_order=record.ambisonic_order,
        ambisonic_layout=record.ambisonic_layout,
        ambisonic_scaling=record.ambisonic_scaling,
    )

    _prepare_al(playback)
    buffer = record.free_buffers[0]
    playback._library.al.buffer_data(
        buffer,
        record.format.native_format,
        chunk.samples,
        record.sample_rate,
    )
    _check_al_error(playback, "upload stream chunk")
    playback._library.al.source_queue_buffers(record.identifier, (buffer,))
    _check_al_error(playback, "queue stream chunk")

    record.free_buffers.popleft()
    record.queued_chunks.append(
        _StreamChunk(
            buffer=buffer,
            frame_count=resolved_frame_count,
            duration=resolved_frame_count / record.sample_rate,
        )
    )
    record.underrun_active = False

    if record.state is StreamState.PLAYING:
        native_state = _get_voice_state(
            playback, record.identifier, "query stream after write"
        )
        if native_state is VoiceState.STOPPED:
            playback._library.al.source_play(record.identifier)
            _check_al_error(playback, "restart stream after write")
            processed_frames = sum(
                chunk.frame_count for chunk in tuple(record.queued_chunks)[:-1]
            )
            if processed_frames:
                playback._library.al.sourcei(
                    record.identifier, bindings.AL_SAMPLE_OFFSET, processed_frames
                )
                _check_al_error(playback, "skip processed stream chunks")
    return True


@_serialized_playback
def start_stream(
    playback: Playback,
    stream: Stream,
    *,
    delay_seconds: float = 0.0,
    delay_frames: int | None = None,
    start_time_ns: int | None = None,
) -> None:
    """Start a primed stream immediately, after silence, or at a device time.

    Args:
        playback: Session that owns ``stream``.
        stream: Initial stream with at least one queued chunk.
        delay_seconds: Initial silence in source-audio seconds. Pitch and Doppler
            affect its real-time duration.
        delay_frames: Exact number of silent sample frames. When provided,
            ``delay_seconds`` must remain 0.0.
        start_time_ns: Absolute audio-device clock time in nanoseconds. ``None``
            starts as soon as possible.

    Raises:
        TypeError: A timing argument has the wrong type.
        ValueError: A delay or device-clock time is invalid.
        InvalidHandleError: ``stream`` is released or belongs to another session.
        InvalidVoiceStateError: The stream was already started or has no queued
            audio.
        PlaybackClosedError: ``playback`` is closed.
        AudioBackendError: OpenAL cannot start playback or the requested timing
            feature is unavailable.
    """

    record = _stream_record(playback, stream)
    if record.state is not StreamState.INITIAL:
        raise InvalidVoiceStateError(
            f"cannot start a stream in the {record.state.value} state"
        )
    if not record.queued_chunks:
        raise InvalidVoiceStateError("cannot start a stream without a queued chunk")
    delay_seconds, delay_frames, start_time_ns = _validate_playback_timing(
        delay_seconds, delay_frames, start_time_ns
    )
    _prepare_al(playback)
    _apply_start_delay(playback, record.identifier, delay_seconds, delay_frames)
    _start_source(playback, record.identifier, start_time_ns)
    _check_al_error(playback, "start stream")
    record.state = StreamState.PLAYING


def _stream_status(record: _StreamRecord, offset_seconds: float = 0.0) -> StreamStatus:
    queued_seconds = sum(chunk.duration for chunk in record.queued_chunks)
    if record.queued_chunks:
        current_duration = record.queued_chunks[0].duration
        queued_seconds -= min(max(offset_seconds, 0.0), current_duration)
    return StreamStatus(
        state=record.state,
        input_finished=record.input_finished,
        queued_chunks=len(record.queued_chunks),
        queued_seconds=max(queued_seconds, 0.0),
        underrun_count=record.underrun_count,
    )


@_serialized_playback
def update_stream(playback: Playback, stream: Stream) -> StreamStatus:
    """Reclaim processed chunks, recover underruns, and return stream status.

    Call this regularly while producing audio. A logically playing stream
    restarts automatically when new audio follows an underrun. Once
    [`finish_stream`][pyalsoft.finish_stream] has declared end-of-input, the
    state changes to ``FINISHED`` after the queue drains.

    Args:
        playback: Session that owns ``stream``.
        stream: Live stream to service.

    Returns:
        Current lifecycle state, queue depth, queued duration, and underrun count.

    Raises:
        InvalidHandleError: ``stream`` is released or belongs to another session.
        PlaybackClosedError: ``playback`` is closed.
        AudioBackendError: OpenAL reports invalid queue state or a native failure.
    """

    record = _stream_record(playback, stream)
    if record.state in (StreamState.FINISHED, StreamState.STOPPED):
        return _stream_status(record)

    _prepare_al(playback)
    processed = int(
        playback._library.al.get_sourcei(
            record.identifier, bindings.AL_BUFFERS_PROCESSED
        )
    )
    _check_al_error(playback, "query processed stream buffers")
    if not 0 <= processed <= len(record.queued_chunks):
        raise AudioBackendError("OpenAL returned an invalid processed buffer count")
    if processed:
        returned = playback._library.al.source_unqueue_buffers(
            record.identifier, processed
        )
        _check_al_error(playback, "reclaim stream buffers")
        expected = tuple(
            chunk.buffer for chunk in tuple(record.queued_chunks)[:processed]
        )
        if returned != expected:
            raise AudioBackendError("OpenAL returned unexpected stream buffers")
        for _ in range(processed):
            chunk = record.queued_chunks.popleft()
            record.free_buffers.append(chunk.buffer)

    if not record.queued_chunks and record.input_finished:
        record.state = StreamState.FINISHED
        record.underrun_active = False
    elif record.state is StreamState.PLAYING:
        if not record.queued_chunks:
            if not record.underrun_active:
                record.underrun_count += 1
                record.underrun_active = True
        else:
            native_state = _get_voice_state(
                playback, record.identifier, "query stream playback state"
            )
            if native_state is VoiceState.STOPPED:
                playback._library.al.source_play(record.identifier)
                _check_al_error(playback, "restart stream after underrun")

    offset = 0.0
    if record.queued_chunks:
        offset = float(
            playback._library.al.get_sourcef(record.identifier, bindings.AL_SEC_OFFSET)
        )
        _check_al_error(playback, "query stream offset")
        if not math.isfinite(offset):
            raise AudioBackendError("OpenAL returned a non-finite stream offset")
    return _stream_status(record, offset)


@_serialized_playback
def finish_stream(playback: Playback, stream: Stream) -> None:
    """Declare end-of-input and allow already queued chunks to drain.

    Calling this again after end-of-input is harmless. Continue calling
    [`update_stream`][pyalsoft.update_stream] until it reports ``FINISHED``. If
    no chunks remain, the stream becomes finished immediately.

    Args:
        playback: Session that owns ``stream``.
        stream: Live stream that will receive no more chunks.

    Raises:
        InvalidHandleError: ``stream`` is released or belongs to another session.
        InvalidVoiceStateError: ``stream`` was explicitly stopped.
        PlaybackClosedError: ``playback`` is closed.
    """

    record = _stream_record(playback, stream)
    if record.state is StreamState.STOPPED:
        raise InvalidVoiceStateError("cannot finish a stream in the stopped state")
    if record.state is StreamState.FINISHED or record.input_finished:
        return
    record.input_finished = True
    record.underrun_active = False
    if not record.queued_chunks:
        record.state = StreamState.FINISHED
