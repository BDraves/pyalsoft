"""Tests for explicit managed playback sessions."""

from __future__ import annotations

from dataclasses import replace

import pytest

from pyalsoft import (
    PCM,
    Acoustics,
    DistanceModel,
    InvalidHandleError,
    InvalidVoiceStateError,
    Listener,
    LowPassFilter,
    ResourceInUseError,
    VoiceConfig,
    VoiceState,
    VoiceStatus,
    bindings,
    get_acoustics,
    get_listener,
    get_voice_status,
    open_playback,
    pause,
    play,
    release,
    release_finished,
    restart,
    resume,
    rewind,
    seek,
    seek_frames,
    set_acoustics,
    set_listener,
    set_voice_config,
    stop,
    upload,
)
from tests._support.managed_backend import FakeLibrary, as_library


def test_managed_playback_applies_data_and_controls_lifecycle() -> None:
    library = FakeLibrary()
    pcm = PCM(b"\0\0" * 10, channels=1, sample_rate=10)
    config = VoiceConfig(position=(1.0, 2.0, 3.0), gain=0.5)
    listener = Listener(position=(4.0, 5.0, 6.0))

    with open_playback(library=as_library(library)) as playback:
        clip = upload(playback, pcm)
        voice = play(playback, clip, config)

        set_listener(playback, listener)
        acoustics = Acoustics(
            distance_model=DistanceModel.LINEAR_CLAMPED,
            doppler_factor=0.5,
            speed_of_sound=300.0,
        )
        set_acoustics(playback, acoustics)
        status = get_voice_status(playback, voice)
        assert status.state is VoiceState.PLAYING
        assert status.offset_seconds == 0.25
        assert library.al.sources[100][bindings.AL_POSITION] == (
            1.0,
            2.0,
            3.0,
        )
        assert library.al.sources[100][bindings.AL_GAIN] == 0.5
        assert clip.info == pcm.info
        assert clip.duration_seconds == 1.0
        assert clip.frame_count == 10
        assert get_listener(playback) == listener
        assert get_acoustics(playback) == acoustics
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


def test_failed_clip_upload_releases_the_native_buffer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    library = FakeLibrary()
    failure = RuntimeError("upload failed")

    def fail_upload(
        _identifier: int,
        _format_name: int,
        _data: bytes,
        _sample_rate: int,
    ) -> None:
        raise failure

    with open_playback(library=as_library(library)) as playback:
        monkeypatch.setattr(library.al, "buffer_data", fail_upload)

        with pytest.raises(RuntimeError, match="upload failed") as caught:
            upload(playback, PCM(b"\0\0", channels=1, sample_rate=1))

        assert caught.value is failure
        assert library.al.allocated_buffers == set()
        assert playback._clips == {}


def test_static_voice_can_seek_rewind_and_restart() -> None:
    library = FakeLibrary()
    with open_playback(library=as_library(library)) as playback:
        clip = upload(playback, PCM(b"\0\0" * 10, channels=1, sample_rate=10))
        voice = play(playback, clip)

        pause(playback, voice)
        seek(playback, voice, 0.5)
        assert get_voice_status(playback, voice) == VoiceStatus(
            state=VoiceState.PAUSED,
            offset_seconds=0.5,
            offset_frames=5,
        )

        seek_frames(playback, voice, 7)
        assert get_voice_status(playback, voice) == VoiceStatus(
            state=VoiceState.PAUSED,
            offset_seconds=0.7,
            offset_frames=7,
        )

        rewind(playback, voice)
        assert get_voice_status(playback, voice) == VoiceStatus(
            state=VoiceState.INITIAL,
            offset_seconds=0.0,
            offset_frames=0,
        )

        restart(playback, voice)
        assert get_voice_status(playback, voice).state is VoiceState.PLAYING

        release(playback, voice)
        release(playback, clip)


def test_release_finished_collects_only_stopped_voices() -> None:
    library = FakeLibrary()
    with open_playback(library=as_library(library)) as playback:
        clip = upload(playback, PCM(b"\0\0", channels=1, sample_rate=1))
        finished = play(
            playback,
            clip,
            filter=LowPassFilter(high_frequency_gain=0.5),
        )
        paused = play(playback, clip)
        library.al.states[100] = bindings.AL_STOPPED
        pause(playback, paused)

        assert release_finished(playback) == 1
        assert release_finished(playback) == 0
        with pytest.raises(InvalidHandleError, match="released"):
            get_voice_status(playback, finished)
        assert library.al.allocated_filters == set()
        assert get_voice_status(playback, paused).state is VoiceState.PAUSED

        release(playback, paused)
        release(playback, clip)
