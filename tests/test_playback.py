"""Tests for the functional managed playback API."""

from __future__ import annotations

from dataclasses import FrozenInstanceError, replace
from typing import cast

import pytest

from pyalsoft import (
    PCM,
    InvalidHandleError,
    Listener,
    PlaybackClosedError,
    ResourceInUseError,
    SampleType,
    VoiceConfig,
    VoiceState,
    bindings,
    close_playback,
    configure_voice,
    get_voice_status,
    open_playback,
    pause,
    play,
    release,
    resume,
    set_listener,
    stop,
    upload,
)


class FakeAL:
    def __init__(self) -> None:
        self.next_buffer = 1
        self.next_source = 100
        self.buffers: dict[int, tuple[int, bytes, int]] = {}
        self.sources: dict[int, dict[int, object]] = {}
        self.states: dict[int, int] = {}
        self.listener: dict[int, object] = {}
        self.error = bindings.AL_NO_ERROR

    def get_error(self) -> int:
        error, self.error = self.error, bindings.AL_NO_ERROR
        return error

    def gen_buffers(self, count: int = 1) -> tuple[int, ...]:
        identifiers = tuple(range(self.next_buffer, self.next_buffer + count))
        self.next_buffer += count
        return identifiers

    def delete_buffers(self, buffers: tuple[int, ...]) -> None:
        for identifier in buffers:
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

    def source3f(
        self, identifier: int, parameter: int, x: float, y: float, z: float
    ) -> None:
        self.sources[identifier][parameter] = (x, y, z)

    def sourcef(self, identifier: int, parameter: int, value: float) -> None:
        self.sources[identifier][parameter] = value

    def sourcei(self, identifier: int, parameter: int, value: int) -> None:
        self.sources[identifier][parameter] = value

    def source_play(self, identifier: int) -> None:
        self.states[identifier] = bindings.AL_PLAYING

    def source_pause(self, identifier: int) -> None:
        self.states[identifier] = bindings.AL_PAUSED

    def source_stop(self, identifier: int) -> None:
        self.states[identifier] = bindings.AL_STOPPED

    def source_stopv(self, sources: tuple[int, ...]) -> None:
        for identifier in sources:
            self.source_stop(identifier)

    def get_sourcei(self, identifier: int, parameter: int) -> int:
        assert parameter == bindings.AL_SOURCE_STATE
        return self.states[identifier]

    def get_sourcef(self, identifier: int, parameter: int) -> float:
        assert parameter == bindings.AL_SEC_OFFSET
        return 0.25

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

        configure_voice(playback, voice, replace(config, position=(7.0, 8.0, 9.0)))
        assert library.al.sources[100][bindings.AL_POSITION] == (
            7.0,
            8.0,
            9.0,
        )

        pause(playback, voice)
        assert get_voice_status(playback, voice).state is VoiceState.PAUSED
        resume(playback, voice)
        assert get_voice_status(playback, voice).state is VoiceState.PLAYING
        stop(playback, voice)
        assert get_voice_status(playback, voice).state is VoiceState.STOPPED

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
