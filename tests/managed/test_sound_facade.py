"""Tests for convenient file playback and its implicit runtime."""

from __future__ import annotations

from pathlib import Path
from typing import Any, cast

import pytest

from pyalsoft import (
    PCM,
    AudioError,
    DirectChannelsMode,
    DistanceModel,
    LowPassFilter,
    Resampler,
    SpatializationMode,
    StereoMode,
    VoiceConfig,
    VoiceState,
    bindings,
    get_sound_cache_info,
    open_playback,
    play,
    set_sound_cache_limit,
    upload,
)
from tests._support.managed_backend import FakeLibrary, as_library
from tests.managed._sound_support import _assert_state, _write_wave


def test_play_can_disable_spatialization_across_restarts(
    default_library: FakeLibrary,
) -> None:
    pcm = PCM(b"\0\0" * 8, channels=1, sample_rate=8_000)

    sound = play(pcm, gain=0.5, spatialize=False)

    assert default_library.al.sources[100][bindings.AL_SOURCE_SPATIALIZE_SOFT] == 0
    assert default_library.al.sources[100][bindings.AL_GAIN] == 0.5
    sound.stop()
    sound.restart()
    assert default_library.al.sources[101][bindings.AL_SOURCE_SPATIALIZE_SOFT] == 0


def test_direct_channel_play_duplicates_mono_pcm_and_survives_restart(
    default_library: FakeLibrary,
) -> None:
    pcm = PCM(b"\x01\x00\x02\x00", channels=1, sample_rate=8_000)

    sound = play(pcm, direct_channels=True)

    assert sound.info == pcm.info
    assert default_library.al.buffers[1] == (
        bindings.AL_FORMAT_STEREO16,
        b"\x01\x00\x01\x00\x02\x00\x02\x00",
        8_000,
    )
    assert default_library.al.sources[100][bindings.AL_DIRECT_CHANNELS_SOFT] == 1

    sound.stop()
    sound.restart()

    assert default_library.al.buffers[2][0] == bindings.AL_FORMAT_STEREO16
    assert default_library.al.sources[101][bindings.AL_DIRECT_CHANNELS_SOFT] == 1


@pytest.mark.parametrize("file_backed", [False, True])
def test_stopped_mono_sound_can_enable_direct_channels_before_restart(
    file_backed: bool,
    tmp_path: Path,
    default_library: FakeLibrary,
) -> None:
    if file_backed:
        sound_path = tmp_path / "direct-restart.wav"
        _write_wave(sound_path)
        source: PCM | Path = sound_path
    else:
        source = PCM(b"\x01\x00\x02\x00", channels=1, sample_rate=8_000)
    sound = play(source)
    sound.stop()

    sound.direct_channels = DirectChannelsMode.DROP_UNMATCHED
    sound.restart()

    assert sound.playing
    assert default_library.al.buffers[2][0] == bindings.AL_FORMAT_STEREO16
    assert default_library.al.sources[101][bindings.AL_DIRECT_CHANNELS_SOFT] == 1


def test_active_mono_sound_rebuilds_its_clip_when_direct_routing_changes(
    default_library: FakeLibrary,
) -> None:
    sound = play(PCM(b"\x01\x00\x02\x00", channels=1, sample_rate=8_000))
    default_library.al.offsets[100] = 0.0
    default_library.al.frame_offsets[100] = 0

    sound.direct_channels = DirectChannelsMode.DROP_UNMATCHED

    _assert_state(sound, VoiceState.PLAYING)
    assert set(default_library.al.sources) == {101}
    assert default_library.al.buffers[2][0] == bindings.AL_FORMAT_STEREO16
    default_library.al.offsets[101] = 0.0
    default_library.al.frame_offsets[101] = 0

    sound.direct_channels = DirectChannelsMode.OFF

    _assert_state(sound, VoiceState.PLAYING)
    assert set(default_library.al.sources) == {102}
    assert default_library.al.buffers[3][0] == bindings.AL_FORMAT_MONO16

    sound.rewind()
    sound.direct_channels = DirectChannelsMode.DROP_UNMATCHED

    _assert_state(sound, VoiceState.INITIAL)
    assert set(default_library.al.sources) == {103}
    assert default_library.al.buffers[4][0] == bindings.AL_FORMAT_STEREO16

    sound.restart()
    sound.pause()
    sound.direct_channels = DirectChannelsMode.OFF

    _assert_state(sound, VoiceState.PAUSED)
    assert set(default_library.al.sources) == {104}
    assert default_library.al.buffers[5][0] == bindings.AL_FORMAT_MONO16


def test_failed_mono_direct_switch_preserves_the_sound_and_cache(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    default_library: FakeLibrary,
) -> None:
    path = tmp_path / "direct-failure.wav"
    _write_wave(path)
    sound = play(path)
    set_sound_cache_limit(sound.info.byte_count)
    default_library.al.offsets[100] = 0.0
    default_library.al.frame_offsets[100] = 0
    source_play = default_library.al.source_play

    def fail_replacement(identifier: int) -> None:
        source_play(identifier)
        if identifier == 101:
            default_library.al.error = bindings.AL_INVALID_OPERATION

    monkeypatch.setattr(default_library.al, "source_play", fail_replacement)
    with pytest.raises(AudioError, match="play voice"):
        sound.direct_channels = DirectChannelsMode.DROP_UNMATCHED

    _assert_state(sound, VoiceState.PLAYING)
    assert sound.direct_channels is DirectChannelsMode.OFF
    assert set(default_library.al.sources) == {100}
    assert get_sound_cache_info().current_bytes == sound.info.byte_count
    assert get_sound_cache_info().clip_count == 1


def test_play_advanced_keywords_override_and_clear_base_config(
    default_library: FakeLibrary,
) -> None:
    base = VoiceConfig(
        distance_model=DistanceModel.LINEAR,
        stereo_angles=(0.5, -0.5),
        resampler=Resampler(0, "Nearest"),
    )
    sound = play(
        PCM(bytes(32), channels=2, sample_rate=8),
        config=base,
        distance_model=None,
        radius=1.5,
        spatialization=SpatializationMode.DISABLED,
        stereo_angles=None,
        resampler=None,
        air_absorption_factor=1.0,
        room_rolloff_factor=0.5,
        stereo_mode=StereoMode.SUPER_STEREO,
        super_stereo_width=0.75,
    )

    assert sound.distance_model is None
    assert sound.radius == 1.5
    assert sound.spatialization is SpatializationMode.DISABLED
    assert sound.direct_channels is DirectChannelsMode.OFF
    assert sound.stereo_angles is None
    assert sound.resampler is None
    assert sound.air_absorption_factor == 1.0
    assert sound.room_rolloff_factor == 0.5
    assert sound.stereo_mode is StereoMode.SUPER_STEREO
    assert sound.super_stereo_width == 0.75


def test_play_rejects_conflicting_spatialization_keywords() -> None:
    with pytest.raises(ValueError, match="cannot both be set"):
        play(
            PCM(bytes(16), channels=1, sample_rate=8),
            spatialize=False,
            spatialization=SpatializationMode.DISABLED,
        )


def test_direct_channel_play_accepts_an_explicit_stereo_clip(
    default_library: FakeLibrary,
) -> None:
    stereo = PCM(b"\x01\x00\x02\x00", channels=2, sample_rate=8_000)
    mono = PCM(b"\x01\x00", channels=1, sample_rate=8_000)

    with open_playback(library=as_library(default_library)) as playback:
        stereo_clip = upload(playback, stereo)
        mono_clip = upload(playback, mono)
        play(playback, stereo_clip, direct_channels=True)

        assert default_library.al.sources[100][bindings.AL_DIRECT_CHANNELS_SOFT] == 1
        with pytest.raises(ValueError, match="requires a stereo clip"):
            play(playback, mono_clip, direct_channels=True)


def test_direct_channel_play_requires_backend_support(
    default_library: FakeLibrary,
) -> None:
    default_library.al_extensions.discard("AL_SOFT_direct_channels")
    pcm = PCM(b"\x01\x00", channels=1, sample_rate=8_000)

    with pytest.raises(AudioError, match="AL_SOFT_direct_channels"):
        play(pcm, direct_channels=True)

    assert default_library.al.sources == {}
    assert default_library.al.allocated_buffers == set()


def test_direct_channels_requires_a_boolean(
    default_library: FakeLibrary,
) -> None:
    del default_library

    with pytest.raises(TypeError, match="direct_channels must be a boolean"):
        cast(Any, play)(
            PCM(b"\x01\x00", channels=1, sample_rate=8_000),
            direct_channels=1,
        )


def test_play_can_disable_spatialization_for_an_explicit_session(
    default_library: FakeLibrary,
) -> None:
    pcm = PCM(b"\0\0" * 8, channels=1, sample_rate=8_000)

    with open_playback(library=as_library(default_library)) as playback:
        clip = upload(playback, pcm)
        play(playback, clip, spatialize=False)
        play(playback, clip, spatialize=True)

        assert default_library.al.sources[100][bindings.AL_SOURCE_SPATIALIZE_SOFT] == 0
        assert default_library.al.sources[101][bindings.AL_SOURCE_SPATIALIZE_SOFT] == 1


def test_non_spatial_play_requires_source_spatialize_extension(
    default_library: FakeLibrary,
) -> None:
    default_library.al_extensions.clear()
    pcm = PCM(b"\0\0", channels=1, sample_rate=8_000)

    automatic = play(pcm)
    assert bindings.AL_SOURCE_SPATIALIZE_SOFT not in default_library.al.sources[100]
    automatic.stop()

    with pytest.raises(AudioError, match="AL_SOFT_source_spatialize"):
        play(pcm, spatialize=False)

    with pytest.raises(AudioError, match="AL_SOFT_source_spatialize"):
        play(pcm, spatialize=True)

    assert default_library.al.sources == {}
    assert default_library.al.allocated_buffers == set()


def test_explicit_none_filter_overrides_a_filtered_play_config(
    tmp_path: Path,
    default_library: FakeLibrary,
) -> None:
    path = tmp_path / "filter-override.wav"
    _write_wave(path)
    filtered_config = VoiceConfig(
        filter=LowPassFilter(high_frequency_gain=0.1),
    )

    filtered_sound = play(path, config=filtered_config)
    assert filtered_sound.filter == filtered_config.filter
    assert default_library.al.allocated_filters == {300}
    filtered_sound.stop()

    sound = play(
        path,
        config=filtered_config,
        filter=None,
    )

    assert sound.filter is None
    assert default_library.al.allocated_filters == set()
    sound.stop()


def test_frame_offsets_are_validated_before_opening_the_default_device(
    tmp_path: Path,
    default_library: FakeLibrary,
) -> None:
    path = tmp_path / "frames.wav"
    _write_wave(path)

    with pytest.raises(ValueError, match="less than the sound frame count"):
        play(path, offset_frames=8)
    assert default_library.alc.current_context is default_library.alc.previous_context

    with pytest.raises(ValueError, match="cannot both be set"):
        play(path, offset_seconds=0.00025, offset_frames=2)
    assert default_library.alc.current_context is default_library.alc.previous_context

    sound = play(path, offset_frames=3)
    assert sound.offset_frames == 3
    assert sound.offset_seconds == pytest.approx(3 / 8_000)
