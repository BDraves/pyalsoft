"""Tests for the functional managed playback API."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import FrozenInstanceError, replace
from typing import cast

import pytest

from pyalsoft import (
    PCM,
    InvalidHandleError,
    InvalidVoiceStateError,
    Listener,
    PlaybackClosedError,
    PlaybackOpenError,
    ResourceInUseError,
    SampleType,
    StreamState,
    VoiceConfig,
    VoiceState,
    bindings,
    close_playback,
    finish_stream,
    get_voice_status,
    open_playback,
    open_stream,
    pause,
    play,
    release,
    release_finished,
    resume,
    set_listener,
    set_voice_config,
    start_stream,
    stop,
    try_write_stream,
    update_stream,
    upload,
)


class FakeAL:
    def __init__(self) -> None:
        self.next_buffer = 1
        self.next_source = 100
        self.allocated_buffers: set[int] = set()
        self.buffers: dict[int, tuple[int, bytes, int]] = {}
        self.sources: dict[int, dict[int, object]] = {}
        self.states: dict[int, int] = {}
        self.queues: dict[int, list[int]] = {}
        self.processed: dict[int, int] = {}
        self.offsets: dict[int, float] = {}
        self.play_calls: list[int] = []
        self.sample_offset_calls: list[tuple[int, int]] = []
        self.listener: dict[int, object] = {}
        self.error = bindings.AL_NO_ERROR

    def get_error(self) -> int:
        error, self.error = self.error, bindings.AL_NO_ERROR
        return error

    def gen_buffers(self, count: int = 1) -> tuple[int, ...]:
        identifiers = tuple(range(self.next_buffer, self.next_buffer + count))
        self.next_buffer += count
        self.allocated_buffers.update(identifiers)
        return identifiers

    def delete_buffers(self, buffers: tuple[int, ...]) -> None:
        for identifier in buffers:
            self.allocated_buffers.discard(identifier)
            self.buffers.pop(identifier, None)

    def buffer_data(
        self, identifier: int, format_name: int, data: bytes, sample_rate: int
    ) -> None:
        self.buffers[identifier] = (int(format_name), bytes(data), sample_rate)

    def gen_sources(self, count: int = 1) -> tuple[int, ...]:
        identifiers = tuple(range(self.next_source, self.next_source + count))
        self.next_source += count
        for identifier in identifiers:
            self.sources[identifier] = {}
            self.states[identifier] = bindings.AL_INITIAL
        return identifiers

    def delete_sources(self, sources: tuple[int, ...]) -> None:
        for identifier in sources:
            self.sources.pop(identifier, None)
            self.states.pop(identifier, None)
            self.queues.pop(identifier, None)
            self.processed.pop(identifier, None)
            self.offsets.pop(identifier, None)

    def source3f(
        self, identifier: int, parameter: int, x: float, y: float, z: float
    ) -> None:
        self.sources[identifier][parameter] = (x, y, z)

    def sourcef(self, identifier: int, parameter: int, value: float) -> None:
        self.sources[identifier][parameter] = value

    def sourcei(self, identifier: int, parameter: int, value: int) -> None:
        self.sources[identifier][parameter] = value
        if parameter == bindings.AL_SAMPLE_OFFSET:
            self.sample_offset_calls.append((identifier, value))

    def source_play(self, identifier: int) -> None:
        self.play_calls.append(identifier)
        if self.states[identifier] == bindings.AL_STOPPED:
            self.processed[identifier] = 0
        self.states[identifier] = bindings.AL_PLAYING

    def source_pause(self, identifier: int) -> None:
        self.states[identifier] = bindings.AL_PAUSED

    def source_stop(self, identifier: int) -> None:
        self.states[identifier] = bindings.AL_STOPPED
        if identifier in self.queues:
            self.processed[identifier] = len(self.queues[identifier])

    def source_stopv(self, sources: tuple[int, ...]) -> None:
        for identifier in sources:
            self.source_stop(identifier)

    def get_sourcei(self, identifier: int, parameter: int) -> int:
        if parameter == bindings.AL_SOURCE_STATE:
            return self.states[identifier]
        if parameter == bindings.AL_BUFFERS_PROCESSED:
            return self.processed.get(identifier, 0)
        if parameter == bindings.AL_BUFFERS_QUEUED:
            return len(self.queues.get(identifier, ()))
        raise AssertionError(f"unexpected integer source parameter {parameter}")

    def get_sourcef(self, identifier: int, parameter: int) -> float:
        assert parameter == bindings.AL_SEC_OFFSET
        if identifier in self.queues:
            return self.offsets.get(identifier, 0.0)
        return 0.25

    def source_queue_buffers(self, identifier: int, buffers: tuple[int, ...]) -> None:
        self.queues.setdefault(identifier, []).extend(buffers)
        self.processed.setdefault(identifier, 0)

    def source_unqueue_buffers(self, identifier: int, count: int) -> tuple[int, ...]:
        assert count <= self.processed.get(identifier, 0)
        queue = self.queues[identifier]
        returned = tuple(queue[:count])
        del queue[:count]
        self.processed[identifier] -= count
        self.offsets[identifier] = 0.0
        return returned

    def listener3f(self, parameter: int, x: float, y: float, z: float) -> None:
        self.listener[parameter] = (x, y, z)

    def listenerfv(self, parameter: int, values: tuple[float, ...]) -> None:
        self.listener[parameter] = values

    def listenerf(self, parameter: int, value: float) -> None:
        self.listener[parameter] = value


class FakeALC:
    def __init__(self) -> None:
        self.device = object()
        self.context = object()
        self.previous_context = object()
        self.current_context: object | None = self.previous_context
        self.destroyed_contexts: list[object] = []
        self.closed_devices: list[object] = []

    def get_current_context(self) -> object | None:
        return self.current_context

    def open_device(self, device_name: str | bytes | None) -> object:
        del device_name
        return self.device

    def create_context(
        self, device: object, attributes: tuple[int, ...] | None
    ) -> object:
        assert device is self.device
        assert attributes is None
        return self.context

    def make_context_current(self, context: object | None) -> bool:
        self.current_context = context
        return True

    def destroy_context(self, context: object) -> None:
        self.destroyed_contexts.append(context)

    def close_device(self, device: object) -> bool:
        self.closed_devices.append(device)
        return True


class FakeLibrary:
    def __init__(self) -> None:
        self.al = FakeAL()
        self.alc = FakeALC()


def as_library(library: FakeLibrary) -> bindings.OpenALLibrary:
    return cast(bindings.OpenALLibrary, library)


def test_pcm_and_configuration_are_immutable_data() -> None:
    pcm = PCM(
        samples=b"\x00\x00\x01\x00",
        channels=1,
        sample_rate=2,
        sample_type=SampleType.INT16,
    )
    config = VoiceConfig(position=(1, 2, 3))

    assert pcm.frame_count == 2
    assert pcm.duration == 1.0
    assert config.position == (1.0, 2.0, 3.0)
    assert replace(config, position=(4.0, 5.0, 6.0)).position == (
        4.0,
        5.0,
        6.0,
    )
    with pytest.raises(FrozenInstanceError):
        pcm.channels = 2  # type: ignore[misc]
    with pytest.raises(TypeError):
        VoiceConfig((1.0, 2.0, 3.0))  # type: ignore[call-arg]
    with pytest.raises(TypeError):
        Listener((1.0, 2.0, 3.0))  # type: ignore[call-arg]


@pytest.mark.parametrize(
    ("arguments", "message"),
    [
        ({"samples": b"", "channels": 1, "sample_rate": 1}, "cannot be empty"),
        ({"samples": b"\0\0", "channels": 3, "sample_rate": 1}, "channels"),
        ({"samples": b"\0\0", "channels": 1, "sample_rate": 0}, "positive"),
        (
            {"samples": b"\0", "channels": 1, "sample_rate": 1},
            "whole number of frames",
        ),
    ],
)
def test_pcm_rejects_invalid_layouts(
    arguments: dict[str, object], message: str
) -> None:
    with pytest.raises(ValueError, match=message):
        PCM(**arguments)  # type: ignore[arg-type]


def test_managed_playback_applies_data_and_controls_lifecycle() -> None:
    library = FakeLibrary()
    pcm = PCM(b"\0\0" * 10, channels=1, sample_rate=10)
    config = VoiceConfig(position=(1.0, 2.0, 3.0), gain=0.5)
    listener = Listener(position=(4.0, 5.0, 6.0))

    with open_playback(library=as_library(library)) as playback:
        clip = upload(playback, pcm)
        voice = play(playback, clip, config)

        set_listener(playback, listener)
        status = get_voice_status(playback, voice)
        assert status.state is VoiceState.PLAYING
        assert status.offset_seconds == 0.25
        assert library.al.sources[100][bindings.AL_POSITION] == (
            1.0,
            2.0,
            3.0,
        )
        assert library.al.sources[100][bindings.AL_GAIN] == 0.5
        assert library.al.listener[bindings.AL_ORIENTATION] == (
            0.0,
            0.0,
            -1.0,
            0.0,
            1.0,
            0.0,
        )

        set_voice_config(playback, voice, replace(config, position=(7.0, 8.0, 9.0)))
        assert library.al.sources[100][bindings.AL_POSITION] == (
            7.0,
            8.0,
            9.0,
        )

        pause(playback, voice)
        assert get_voice_status(playback, voice).state is VoiceState.PAUSED
        resume(playback, voice)
        assert get_voice_status(playback, voice).state is VoiceState.PLAYING
        with pytest.raises(InvalidVoiceStateError, match="playing"):
            resume(playback, voice)
        stop(playback, voice)
        assert get_voice_status(playback, voice).state is VoiceState.STOPPED
        with pytest.raises(InvalidVoiceStateError, match="stopped"):
            resume(playback, voice)

        with pytest.raises(ResourceInUseError):
            release(playback, clip)
        release(playback, voice)
        with pytest.raises(InvalidHandleError, match="released"):
            get_voice_status(playback, voice)
        release(playback, clip)

    assert library.al.sources == {}
    assert library.al.buffers == {}
    assert library.alc.destroyed_contexts == [library.alc.context]
    assert library.alc.closed_devices == [library.alc.device]
    assert library.alc.current_context is library.alc.previous_context


def test_release_finished_collects_only_stopped_voices() -> None:
    library = FakeLibrary()
    with open_playback(library=as_library(library)) as playback:
        clip = upload(playback, PCM(b"\0\0", channels=1, sample_rate=1))
        finished = play(playback, clip)
        paused = play(playback, clip)
        library.al.states[100] = bindings.AL_STOPPED
        pause(playback, paused)

        assert release_finished(playback) == 1
        assert release_finished(playback) == 0
        with pytest.raises(InvalidHandleError, match="released"):
            get_voice_status(playback, finished)
        assert get_voice_status(playback, paused).state is VoiceState.PAUSED

        release(playback, paused)
        release(playback, clip)


def test_open_playback_wraps_library_loading_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    failure = bindings.LibraryNotFoundError("missing")

    def fail_to_load() -> bindings.OpenALLibrary:
        raise failure

    monkeypatch.setattr(bindings, "load", fail_to_load)

    with pytest.raises(PlaybackOpenError) as caught:
        open_playback()
    assert caught.value.__cause__ is failure


def test_close_releases_live_resources_and_is_idempotent() -> None:
    library = FakeLibrary()
    playback = open_playback(library=as_library(library))
    clip = upload(playback, PCM(b"\0\0", channels=1, sample_rate=1))
    play(playback, clip)

    close_playback(playback)
    close_playback(playback)

    assert library.al.sources == {}
    assert library.al.buffers == {}
    assert library.alc.destroyed_contexts == [library.alc.context]
    with pytest.raises(PlaybackClosedError):
        upload(playback, PCM(b"\0\0", channels=1, sample_rate=1))


def test_handles_cannot_cross_playback_sessions() -> None:
    first_library = FakeLibrary()
    second_library = FakeLibrary()
    first = open_playback(library=as_library(first_library))
    second = open_playback(library=as_library(second_library))
    try:
        clip = upload(first, PCM(b"\0\0", channels=1, sample_rate=1))
        with pytest.raises(InvalidHandleError, match="does not belong"):
            play(second, clip)
    finally:
        close_playback(second)
        close_playback(first)


def test_playback_does_not_enforce_thread_ownership() -> None:
    library = FakeLibrary()
    playback = open_playback(library=as_library(library))
    pcm = PCM(b"\0\0", channels=1, sample_rate=1)

    with ThreadPoolExecutor(max_workers=1) as executor:
        clip = executor.submit(upload, playback, pcm).result()
        voice = executor.submit(play, playback, clip).result()
        executor.submit(stop, playback, voice).result()
        executor.submit(close_playback, playback).result()

    assert library.al.sources == {}
    assert library.al.buffers == {}


def test_stream_uses_bounded_reusable_buffers_and_drains_finished_input() -> None:
    library = FakeLibrary()
    with open_playback(library=as_library(library)) as playback:
        stream = open_stream(
            playback,
            channels=1,
            sample_rate=10,
            buffer_count=2,
            config=VoiceConfig(gain=0.5),
        )
        assert len(library.al.allocated_buffers) == 2
        assert library.al.sources[100][bindings.AL_GAIN] == 0.5

        first = bytearray(b"\0\0" * 10)
        assert try_write_stream(playback, stream, first)
        first[:] = b"\xff" * len(first)
        assert library.al.buffers[1][1] == b"\0\0" * 10
        assert try_write_stream(playback, stream, b"\0\0" * 5)
        assert not try_write_stream(playback, stream, b"\0\0")

        start_stream(playback, stream)
        status = update_stream(playback, stream)
        assert status.state is StreamState.PLAYING
        assert status.queued_chunks == 2
        assert status.queued_seconds == pytest.approx(1.5)

        finish_stream(playback, stream)
        library.al.processed[100] = 2
        library.al.states[100] = bindings.AL_STOPPED
        status = update_stream(playback, stream)
        assert status.state is StreamState.FINISHED
        assert status.input_finished
        assert status.queued_chunks == 0
        assert status.queued_seconds == 0.0
        assert status.underrun_count == 0

        assert release_finished(playback) == 1
        with pytest.raises(InvalidHandleError, match="released"):
            update_stream(playback, stream)

    assert library.al.allocated_buffers == set()


def test_stream_update_reclaims_offsets_and_counts_underrun_episodes() -> None:
    library = FakeLibrary()
    with open_playback(library=as_library(library)) as playback:
        stream = open_stream(
            playback,
            channels=1,
            sample_rate=10,
            buffer_count=1,
        )
        assert try_write_stream(playback, stream, b"\0\0" * 10)
        start_stream(playback, stream)
        library.al.offsets[100] = 0.25
        assert update_stream(playback, stream).queued_seconds == pytest.approx(0.75)

        library.al.processed[100] = 1
        library.al.states[100] = bindings.AL_STOPPED
        status = update_stream(playback, stream)
        assert status.state is StreamState.PLAYING
        assert status.queued_chunks == 0
        assert status.underrun_count == 1
        assert update_stream(playback, stream).underrun_count == 1

        assert try_write_stream(playback, stream, b"\0\0" * 2)
        assert library.al.play_calls == [100, 100]
        library.al.processed[100] = 1
        library.al.states[100] = bindings.AL_STOPPED
        assert update_stream(playback, stream).underrun_count == 2

        finish_stream(playback, stream)
        status = update_stream(playback, stream)
        assert status.state is StreamState.FINISHED
        assert status.underrun_count == 2
        release(playback, stream)


def test_stream_write_restarts_without_replaying_unreclaimed_chunks() -> None:
    library = FakeLibrary()
    with open_playback(library=as_library(library)) as playback:
        stream = open_stream(
            playback,
            channels=1,
            sample_rate=10,
            buffer_count=2,
        )
        assert try_write_stream(playback, stream, b"\0\0" * 10)
        start_stream(playback, stream)

        library.al.processed[100] = 1
        library.al.states[100] = bindings.AL_STOPPED
        assert try_write_stream(playback, stream, b"\0\0" * 2)

        assert library.al.play_calls == [100, 100]
        assert library.al.sample_offset_calls == [(100, 10)]
        release(playback, stream)


def test_stream_pause_resume_stop_and_looping_rules() -> None:
    library = FakeLibrary()
    with open_playback(library=as_library(library)) as playback:
        with pytest.raises(ValueError, match="cannot loop"):
            open_stream(
                playback,
                channels=2,
                sample_rate=44_100,
                config=VoiceConfig(looping=True),
            )

        stream = open_stream(
            playback,
            channels=2,
            sample_rate=44_100,
            sample_type=SampleType.UINT8,
            buffer_count=2,
        )
        pause(playback, stream)
        assert try_write_stream(playback, stream, b"\0\0")
        start_stream(playback, stream)
        pause(playback, stream)
        assert update_stream(playback, stream).state is StreamState.PAUSED
        finish_stream(playback, stream)
        assert update_stream(playback, stream).state is StreamState.PAUSED
        with pytest.raises(ValueError, match="cannot loop"):
            set_voice_config(playback, stream, VoiceConfig(looping=True))

        resume(playback, stream)
        assert update_stream(playback, stream).state is StreamState.PLAYING
        stop(playback, stream)
        status = update_stream(playback, stream)
        assert status.state is StreamState.STOPPED
        assert status.input_finished
        assert status.queued_chunks == 0
        stop(playback, stream)
        pause(playback, stream)
        with pytest.raises(InvalidVoiceStateError, match="stopped"):
            resume(playback, stream)
        with pytest.raises(InvalidVoiceStateError, match="stopped"):
            finish_stream(playback, stream)
        with pytest.raises(InvalidVoiceStateError, match="stopped"):
            try_write_stream(playback, stream, b"\0\0")
        release(playback, stream)


@pytest.mark.parametrize(
    ("arguments", "exception", "message"),
    [
        ({"channels": 3, "sample_rate": 1}, ValueError, "channels"),
        ({"channels": 1, "sample_rate": 0}, ValueError, "positive"),
        ({"channels": 1, "sample_rate": True}, TypeError, "integer"),
        ({"channels": 1, "sample_rate": 1, "buffer_count": 0}, ValueError, "positive"),
        (
            {"channels": 1, "sample_rate": 1, "buffer_count": True},
            TypeError,
            "integer",
        ),
    ],
)
def test_open_stream_rejects_invalid_layouts(
    arguments: dict[str, object], exception: type[Exception], message: str
) -> None:
    library = FakeLibrary()
    with (
        open_playback(library=as_library(library)) as playback,
        pytest.raises(exception, match=message),
    ):
        open_stream(playback, **arguments)  # type: ignore[arg-type]


def test_stream_rejects_invalid_chunks_and_invalid_start_transitions() -> None:
    library = FakeLibrary()
    with open_playback(library=as_library(library)) as playback:
        stream = open_stream(
            playback,
            channels=2,
            sample_rate=1,
            buffer_count=1,
        )
        with pytest.raises(InvalidVoiceStateError, match="without a queued chunk"):
            start_stream(playback, stream)
        with pytest.raises(TypeError, match="bytes-like"):
            try_write_stream(playback, stream, "audio")  # type: ignore[arg-type]
        with pytest.raises(ValueError, match="empty"):
            try_write_stream(playback, stream, b"")
        with pytest.raises(ValueError, match="whole number"):
            try_write_stream(playback, stream, b"\0\0")

        assert try_write_stream(playback, stream, b"\0" * 4)
        with pytest.raises(ValueError, match="whole number"):
            try_write_stream(playback, stream, b"\0")
        start_stream(playback, stream)
        with pytest.raises(InvalidVoiceStateError, match="playing"):
            start_stream(playback, stream)
        finish_stream(playback, stream)
        finish_stream(playback, stream)
        with pytest.raises(InvalidVoiceStateError, match="end-of-input"):
            try_write_stream(playback, stream, b"\0" * 4)
        release(playback, stream)


def test_primed_finished_stream_can_start_and_close_releases_all_buffers() -> None:
    library = FakeLibrary()
    playback = open_playback(library=as_library(library))
    stream = open_stream(
        playback,
        channels=1,
        sample_rate=1,
        buffer_count=3,
    )
    assert try_write_stream(playback, stream, b"\0\0")
    finish_stream(playback, stream)
    start_stream(playback, stream)

    close_playback(playback)

    assert library.al.sources == {}
    assert library.al.allocated_buffers == set()
