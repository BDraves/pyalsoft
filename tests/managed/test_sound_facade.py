"""Tests for convenient file playback and its implicit runtime."""

from __future__ import annotations

from pathlib import Path
from typing import Any, cast

import pytest

from pyalsoft import (
    PCM,
    AudioError,
    LowPassFilter,
    VoiceConfig,
    bindings,
    open_playback,
    play,
    upload,
)
from tests._support.managed_backend import FakeLibrary, as_library
from tests.managed._sound_support import _write_wave


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
